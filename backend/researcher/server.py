"""
FinAI Researcher Service - Investment Advice Agent
"""

import os
import logging
from datetime import datetime, UTC
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from agents import Agent, RunHooks, Runner, trace
from agents.extensions.models.litellm_model import LitellmModel

# Suppress LiteLLM and OpenAI Agents SDK warnings/telemetry exports
logging.basicConfig(level=logging.INFO)
logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
logging.getLogger("openai").setLevel(logging.ERROR)
logging.getLogger("agents").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

# Monkey patch for Langfuse 4.x/3.x compatibility with older LiteLLM versions
try:
    import langfuse
    if not hasattr(langfuse, "version"):
        import langfuse._version
        langfuse.version = langfuse._version
    
    # Intercept __init__ to pop incompatible 'sdk_integration' parameter
    original_init = langfuse.Langfuse.__init__
    def patched_init(self, *args, **kwargs):
        kwargs.pop("sdk_integration", None)
        return original_init(self, *args, **kwargs)
    langfuse.Langfuse.__init__ = patched_init
except Exception:
    pass


# Import from our modules
from context import get_agent_instructions, DEFAULT_RESEARCH_PROMPT
from mcp_servers import create_playwright_mcp_server
from tools import ingest_financial_document

# Load environment
load_dotenv(override=True)

if os.getenv("LANGFUSE_SECRET_KEY"):
    try:
        import litellm
        litellm.success_callback = ["langfuse"]
        logger.info("✅ Observability: LiteLLM Langfuse callback enabled for Researcher")
    except Exception as e:
        logger.error(f"❌ Observability: Failed to enable LiteLLM Langfuse callback for Researcher: {e}")

app = FastAPI(title="FinAI Researcher Service")


MCP_LOGGING_ENABLED = os.getenv("MCP_LOGGING") == "True"


def _trim_for_log(value: Any, max_length: int = 1500) -> str:
    text = str(value)
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}... [trimmed {len(text) - max_length} chars]"


class ResearchLoggingHooks(RunHooks):
    async def on_tool_start(self, context, agent, tool) -> None:
        if MCP_LOGGING_ENABLED:
            logger.info(
                "[tool:start] name=%s type=%s repr=%s",
                getattr(tool, "name", type(tool).__name__),
                type(tool).__name__,
                _trim_for_log(repr(tool), max_length=500),
            )

    async def on_tool_end(self, context, agent, tool, result) -> None:
        if MCP_LOGGING_ENABLED:
            logger.info(
                "[tool:end] name=%s result=%s",
                getattr(tool, "name", type(tool).__name__),
                _trim_for_log(result),
            )


# Request model
class ResearchRequest(BaseModel):
    topic: Optional[str] = None  # Optional - if not provided, agent picks a topic


async def run_research_agent(topic: str = None) -> str:
    """Run the research agent to generate investment advice."""

    # Prepare the user query
    if topic:
        query = f"Research this investment topic: {topic}"
    else:
        query = DEFAULT_RESEARCH_PROMPT

    if MCP_LOGGING_ENABLED:
        logger.info(
            "Starting research agent topic_provided=%s query_preview=%s",
            bool(topic),
            _trim_for_log(query, max_length=500),
        )

    region = os.environ.get("BEDROCK_REGION", "us-west-2")
    os.environ["AWS_REGION_NAME"] = region
    os.environ["AWS_REGION"] = region
    os.environ["AWS_DEFAULT_REGION"] = region
    model_name = os.environ.get(
        "RESEARCHER_MODEL", "bedrock/global.openai.gpt-oss-120b-1:0"
    )
    model = LitellmModel(model=model_name)

    if MCP_LOGGING_ENABLED:
        logger.info(
            "Research agent runtime model=%s aws_region=%s aws_default_region=%s aws_region_name=%s playwright_logging=%s",
            model_name,
            os.environ.get("AWS_REGION"),
            os.environ.get("AWS_DEFAULT_REGION"),
            os.environ.get("AWS_REGION_NAME"),
            os.environ.get("DEBUG"),
        )

    # Create and run the agent with MCP server
    with trace("Researcher"):
        async with create_playwright_mcp_server(timeout_seconds=120) as playwright_mcp:
            if MCP_LOGGING_ENABLED:
                logger.info("Playwright MCP server context created")
            agent = Agent(
                name="FinAI Investment Researcher",
                instructions=get_agent_instructions(),
                model=model,
                tools=[ingest_financial_document],
                mcp_servers=[playwright_mcp],
            )

            try:
                result = await Runner.run(
                    agent,
                    input=query,
                    max_turns=15,
                    hooks=ResearchLoggingHooks() if MCP_LOGGING_ENABLED else None,
                )
                if MCP_LOGGING_ENABLED:
                    logger.info(
                        "Research agent run completed output_preview=%s",
                        _trim_for_log(result.final_output, max_length=1000),
                    )
                return result.final_output
            except Exception:
                if MCP_LOGGING_ENABLED:
                    logger.exception("Research agent run failed")
                raise
            finally:
                if os.getenv("LANGFUSE_SECRET_KEY"):
                    try:
                        try:
                            from langfuse import get_client
                            client = get_client()
                            logger.info("🔍 Observability: Flushing LangFuse client for Researcher via get_client()...")
                            client.flush()
                        except (ImportError, AttributeError):
                            from langfuse import Langfuse
                            client = Langfuse()
                            logger.info("🔍 Observability: Flushing LangFuse client for Researcher via Langfuse()...")
                            client.flush()
                        logger.info("✅ Observability: LangFuse client flushed successfully for Researcher")
                    except Exception as e:
                        logger.warning(f"⚠️ Observability: Failed to flush LangFuse client for Researcher: {e}")



@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "service": "FinAI Researcher",
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.post("/research")
async def research(request: ResearchRequest) -> str:
    """
    Generate investment research and advice.

    The agent will:
    1. Browse current financial websites for data
    2. Analyze the information found
    3. Store the analysis in the knowledge base

    If no topic is provided, the agent will pick a trending topic.
    """
    logger.info(f"MCP_LOGGING_ENABLED={MCP_LOGGING_ENABLED}")

    try:
        response = await run_research_agent(request.topic)
        return response
    except Exception as e:
        print(f"Error in research endpoint: {e}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/research/auto")
async def research_auto():
    """
    Automated research endpoint for scheduled runs.
    Picks a trending topic automatically and generates research.
    Used by EventBridge Scheduler for periodic research updates.
    """
    try:
        # Always use agent's choice for automated runs
        response = await run_research_agent(topic=None)
        return {
            "status": "success",
            "timestamp": datetime.now(UTC).isoformat(),
            "message": "Automated research completed",
            "preview": response[:200] + "..." if len(response) > 200 else response,
        }
    except Exception as e:
        print(f"Error in automated research: {e}")
        return {"status": "error", "timestamp": datetime.now(UTC).isoformat(), "error": str(e)}


@app.get("/health")
async def health():
    """Detailed health check."""
    # Debug container detection
    container_indicators = {
        "dockerenv": os.path.exists("/.dockerenv"),
        "containerenv": os.path.exists("/run/.containerenv"),
        "aws_execution_env": os.environ.get("AWS_EXECUTION_ENV", ""),
        "ecs_container_metadata": os.environ.get("ECS_CONTAINER_METADATA_URI", ""),
        "kubernetes_service": os.environ.get("KUBERNETES_SERVICE_HOST", ""),
    }

    return {
        "service": "FinAI Researcher",
        "status": "healthy",
        "ingest_lambda_configured": bool(os.getenv("INGEST_LAMBDA_NAME")),
        "timestamp": datetime.now(UTC).isoformat(),
        "debug_container": container_indicators,
        "aws_region": os.environ.get("AWS_DEFAULT_REGION", "not set"),
        "bedrock_model": "bedrock/amazon.nova-pro-v1:0",
        "mcp_logging_enabled": MCP_LOGGING_ENABLED,
    }


@app.get("/test-bedrock")
async def test_bedrock():
    """Test Bedrock connection directly."""
    try:
        import boto3

        region = os.environ.get("BEDROCK_REGION", "us-west-2")
        model_id = os.environ.get("RESEARCHER_MODEL", "bedrock/global.openai.gpt-oss-120b-1:0")

        os.environ["AWS_REGION_NAME"] = region
        os.environ["AWS_REGION"] = region
        os.environ["AWS_DEFAULT_REGION"] = region

        session = boto3.Session()
        actual_region = session.region_name

        client = boto3.client("bedrock-runtime", region_name=region)

        try:
            bedrock_client = boto3.client("bedrock", region_name=region)
            models = bedrock_client.list_foundation_models()
            openai_models = [
                m["modelId"] for m in models["modelSummaries"] if "openai" in m["modelId"].lower()
            ]
        except Exception as list_error:
            openai_models = f"Error listing: {str(list_error)}"

        model = LitellmModel(model=model_id)

        agent = Agent(
            name="Test Agent",
            instructions="You are a helpful assistant. Be very brief.",
            model=model,
        )

        result = await Runner.run(agent, input="Say hello in 5 words or less", max_turns=1)

        return {
            "status": "success",
            "model": str(model.model),  # Use actual model from LitellmModel
            "region": actual_region,
            "response": result.final_output,
            "debug": {
                "boto3_session_region": actual_region,
                "available_openai_models": openai_models,
            },
        }
    except Exception as e:
        import traceback

        return {
            "status": "error",
            "error": str(e),
            "type": type(e).__name__,
            "traceback": traceback.format_exc(),
            "debug": {
                "boto3_session_region": session.region_name if "session" in locals() else "unknown",
                "env_vars": {
                    "AWS_REGION_NAME": os.environ.get("AWS_REGION_NAME"),
                    "AWS_REGION": os.environ.get("AWS_REGION"),
                    "AWS_DEFAULT_REGION": os.environ.get("AWS_DEFAULT_REGION"),
                },
            },
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
