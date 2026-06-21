"""
FinAI Researcher Service - Investment Advice Agent
"""

import logging
import os
from datetime import UTC, datetime
from typing import Optional

from agents import Agent, Runner, trace
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Suppress verbose library logs
logging.basicConfig(level=logging.INFO)
logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
logging.getLogger("openai").setLevel(logging.ERROR)
logging.getLogger("agents").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

# Import from our modules
from context import DEFAULT_RESEARCH_PROMPT, get_agent_instructions
from mcp_servers import create_playwright_mcp_server
from tools import ingest_financial_document

load_dotenv(override=True)

# Enable Langfuse callback if environment keys are present
if os.getenv("LANGFUSE_SECRET_KEY"):
    try:
        import litellm

        # This automatically hooks into LiteLLM: every time our agent speaks to AWS Bedrock
        # LiteLLM automatically reports tokens used, latency, prompts, and completions directly to your Langfuse dashboard.
        litellm.success_callback = ["langfuse"]
        logger.info("✅ Observability: LiteLLM Langfuse callback enabled")
    except Exception as e:
        logger.error(f"❌ Observability: Failed to enable Langfuse: {e}")

app = FastAPI(title="FinAI Researcher Service")


# JSON payload should have  an optional key called "topic"
class ResearchRequest(BaseModel):
    topic: Optional[str] = None


async def run_research_agent(topic: Optional[str] = None) -> str:
    """Run the research agent to generate investment advice."""
    query = (
        f"Research this investment topic: {topic}" if topic else DEFAULT_RESEARCH_PROMPT
    )
    logger.info("Starting research agent for topic: %s", topic or "Trending topics")

    # Set up Bedrock AWS region defaults
    region = os.environ.get("BEDROCK_REGION", "us-west-2")
    os.environ["AWS_REGION_NAME"] = region
    os.environ["AWS_REGION"] = region
    os.environ["AWS_DEFAULT_REGION"] = region

    model_name = os.environ.get("RESEARCHER_MODEL", "bedrock/moonshotai.kimi-k2.5")
    model = LitellmModel(model=model_name)

    # Execute agent with browser capabilities in trace context
    # Wraps everything below inside an observability trace window,
    # treating the entire following execution block as a single consolidated event
    with trace("Researcher"):

        # context manager(with): keep mcp server alive while the agents run,
        # Close broswer and other process once and clear resoruces once done
        async with create_playwright_mcp_server(timeout_seconds=120) as playwright_mcp:
            agent = Agent(
                name="FinAI Investment Researcher",
                instructions=get_agent_instructions(),
                model=model,
                tools=[ingest_financial_document],
                mcp_servers=[playwright_mcp],
            )

            try:
                result = await Runner.run(agent, input=query, max_turns=15)
                return result.final_output
            except Exception as e:
                logger.error("Research agent run failed: %s", e)
                raise
            finally:
                # Flush Langfuse queue to make sure traces are uploaded in serverless container lifecycle
                # AWS Lambda instantly freezes all container processes the exact millisecond a web response is returned.
                # If we  don't call .flush(), any pending telemetry traces sitting in the background queue will never get uploaded to Langfuse
                # Forcefully uploads all pending AI log data from your application's memory queue straight to your Langfuse
                if os.getenv("LANGFUSE_SECRET_KEY"):
                    try:
                        from langfuse import Langfuse

                        Langfuse().flush()
                        logger.info("Observability: LangFuse client flushed")
                    except Exception as e:
                        logger.warning(f"Observability: Failed to flush LangFuse: {e}")


@app.get("/")
@app.get("/health")
async def health():
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
    The agent will browse websites, analyze, and save to database.
    """
    try:
        return await run_research_agent(request.topic)
    except Exception as e:
        logger.error(f"Error in /research: {e}")
        raise HTTPException(status_code=500, detail=str(e))
