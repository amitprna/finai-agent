"""
FinAI Researcher Service - Investment Advice Agent
"""

import os
import logging
from datetime import datetime, UTC
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from agents import Agent, Runner, trace
from agents.extensions.models.litellm_model import LitellmModel

# Suppress verbose library logs
logging.basicConfig(level=logging.INFO)
logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
logging.getLogger("openai").setLevel(logging.ERROR)
logging.getLogger("agents").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

# Import from our modules
from context import get_agent_instructions, DEFAULT_RESEARCH_PROMPT
from mcp_servers import create_playwright_mcp_server
from tools import ingest_financial_document

load_dotenv(override=True)

# Enable Langfuse callback if environment keys are present
if os.getenv("LANGFUSE_SECRET_KEY"):
    try:
        import litellm
        litellm.success_callback = ["langfuse"]
        logger.info("✅ Observability: LiteLLM Langfuse callback enabled")
    except Exception as e:
        logger.error(f"❌ Observability: Failed to enable Langfuse: {e}")

app = FastAPI(title="FinAI Researcher Service")


class ResearchRequest(BaseModel):
    topic: Optional[str] = None


async def run_research_agent(topic: Optional[str] = None) -> str:
    """Run the research agent to generate investment advice."""
    query = f"Research this investment topic: {topic}" if topic else DEFAULT_RESEARCH_PROMPT
    logger.info("Starting research agent for topic: %s", topic or "Trending topics")

    # Set up Bedrock AWS region defaults
    region = os.environ.get("BEDROCK_REGION", "us-west-2")
    os.environ["AWS_REGION_NAME"] = region
    os.environ["AWS_REGION"] = region
    os.environ["AWS_DEFAULT_REGION"] = region

    model_name = os.environ.get("RESEARCHER_MODEL", "bedrock/global.openai.gpt-oss-120b-1:0")
    model = LitellmModel(model=model_name)

    # Execute agent with browser capabilities in trace context
    with trace("Researcher"):
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
