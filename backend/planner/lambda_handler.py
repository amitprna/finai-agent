"""
FinAI Planner - Orchestrator Agent Lambda Handler
Coordinates the execution pipeline: tags missing assets, refreshes market prices,
determines sub-agent invocations via the LLM, and logs overall status.
"""

import os
import sys
import json
import asyncio
import logging
from pathlib import Path

# ----------------------------------------------------
# Path Bootstrapping
# ----------------------------------------------------
# Adjust path context to ensure import compatibility inside AWS Lambda container
_dir = Path(__file__).parent.absolute()
for _p in [str(_dir), str(_dir.parent)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _m in ["templates", "agent", "market"]:
    sys.modules.pop(_m, None)

import litellm
from dotenv import load_dotenv

# Load local environment variables if available
load_dotenv(override=True)

from src import Database
from templates import ORCHESTRATOR_INSTRUCTIONS
from agent import (
    create_agent, 
    handle_missing_instruments, 
    load_portfolio_summary,
    invoke_reporter, 
    invoke_charter, 
    invoke_retirement
)
from market import update_instrument_prices

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Initialize shared database interface
db = Database()


async def run_orchestrator(job_id: str) -> None:
    """
    Main execution loop of the financial planner pipeline.
    
    Execution Stages:
      1. Pre-process: scan for missing instrument metadata and refresh current prices.
      2. Construct context metrics and fetch the Planner agent configuration.
      3. Run LLM decision loops to dynamically fire sub-agent tool calls in sequence.
      4. Complete execution and update database state.
    """
    try:
        # Update job state to running
        db.jobs.update_status(job_id, "running")

        # Stage 1: Asset tagging check (in-process or lambda)
        logger.info(f"Planner [{job_id}]: Tagging missing instruments...")
        await asyncio.to_thread(handle_missing_instruments, job_id, db)

        # Stage 2: Price scraping update (scrapes latest quotes via Yahoo Finance chart API)
        logger.info(f"Planner [{job_id}]: Refreshing market prices...")
        await asyncio.to_thread(update_instrument_prices, job_id, db)

        # Stage 3: High-level statistics calculation
        summary = await asyncio.to_thread(load_portfolio_summary, job_id, db)

        # Stage 4: Run LiteLLM agent loop to coordinate sub-agents
        model, tools, task, _ = create_agent(job_id, summary, db)

        messages = [
            {"role": "system", "content": ORCHESTRATOR_INSTRUCTIONS},
            {"role": "user",   "content": task},
        ]

        # Loop up to 20 turns to handle multiple sequential tool requests
        for turn in range(20):
            logger.info(f"Planner [{job_id}]: LLM decision turn {turn + 1}")
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=500,
                metadata={
                    "trace_id": job_id,
                    "trace_name": "Portfolio Advisor Orchestration (Planner)",
                    "session_id": job_id
                }
            )
            message = response.choices[0].message
            messages.append(message.model_dump() if hasattr(message, "model_dump") else message.dict())

            # Check if model requested a tool execution
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                logger.info(f"Planner [{job_id}]: No further tool execution requested. Done.")
                break

            # Execute tool actions asynchronously
            for tc in tool_calls:
                name = tc.function.name
                logger.info(f"Planner [{job_id}]: Calling tool → {name}")
                if name == "invoke_reporter":
                    result = await invoke_reporter(job_id)
                elif name == "invoke_charter":
                    result = await invoke_charter(job_id)
                elif name == "invoke_retirement":
                    result = await invoke_retirement(job_id)
                else:
                    result = f"Unknown tool identifier: {name}"
                
                # Append tool result matching chat completion spec
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": name, "content": result})

        # Update final status on database upon successful run
        db.jobs.update_status(job_id, "completed")
        logger.info(f"Planner [{job_id}]: Pipeline completed successfully ✓")

    except Exception as e:
        logger.error(f"Planner [{job_id}]: Pipeline execution failed: {e}", exc_info=True)
        # Update database with job failure and save the error message
        db.jobs.update_status(job_id, "failed", error_message=str(e))
        raise


from observability import observe


def lambda_handler(event, context):
    """
    AWS Lambda handler entry point.
    Receives events from SQS triggers or direct invokes, parsing the job_id payload.
    """
    with observe():
        job_id = None
        # Handle SQS queue triggers
        if "Records" in event:
            body = event["Records"][0]["body"]
            try:
                body = json.loads(body)
                job_id = body.get("job_id", body)
            except json.JSONDecodeError:
                job_id = body
        else:
            # Handle direct API Gateway or test payloads
            job_id = event.get("job_id")

        if not job_id:
            return {"statusCode": 400, "body": json.dumps({"error": "No job_id provided"})}

        # Start execution within standard event loop
        asyncio.run(run_orchestrator(job_id))
        return {"statusCode": 200, "body": json.dumps({"success": True})}