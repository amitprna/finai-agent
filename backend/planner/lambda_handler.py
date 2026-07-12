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
# Path Bootstrapping & Module Resolution
# ----------------------------------------------------
# Why do we need this?
# When running inside AWS Lambda, the workspace root and script directories might not be in Python's
# default import search paths. To prevent 'ModuleNotFoundError', we dynamically locate the folder where
# this script lives (_dir) and its parent folder, then insert them at the front of sys.path (import search path list).
#
# Additionally, Lambda environments can reuse container instances across invocations. To prevent caching issues
# where outdated templates or agents are kept in memory, we pop them from sys.modules, forcing Python to reload them.
_dir = Path(__file__).parent.absolute()
for _p in [str(_dir), str(_dir.parent)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _m in ["templates", "agent", "market"]:
    sys.modules.pop(_m, None)

import litellm
from dotenv import load_dotenv

# Load local environment variables if a .env file is available in the current workspace (override existing values)
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

# Setup logging configuration so we can monitor execution logs inside AWS CloudWatch
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Initialize shared database interface to read and write user portfolios and job statuses
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
        # Update job state in the database to "running" to notify the frontend/user
        db.jobs.update_status(job_id, "running")

        # ----------------------------------------------------
        # Understanding asyncio.to_thread
        # ----------------------------------------------------
        # Python's asyncio module runs asynchronous code on a single thread called the event loop.
        # As long as tasks are asynchronous (using await), the event loop can pause one task and switch
        # to another, making it extremely fast for network-bound tasks.
        #
        # However, standard synchronous functions (like handle_missing_instruments, which does heavy
        # database queries, and update_instrument_prices, which scrapes market data synchronously)
        # do not support 'await' natively. If we called them directly, they would block the entire
        # event loop. This means the program would freeze, unable to execute other concurrent tasks.
        #
        # asyncio.to_thread(func, *args) solves this by:
        # 1. Spawning a new worker thread from Python's internal thread pool.
        # 2. Running the blocking, synchronous function inside that separate thread.
        # 3. Wrapping the execution in an awaitable object, allowing the main event loop to yield control
        #    and continue processing other async tasks while the worker thread executes in the background.

        # Stage 1: Asset tagging check (in-process or lambda)
        # Scans user portfolio for holdings missing metadata (e.g. sectors, regions) and classifies them.
        logger.info(f"Planner [{job_id}]: Tagging missing instruments...")
        await asyncio.to_thread(handle_missing_instruments, job_id, db)

        # Stage 2: Price scraping update (scrapes latest quotes via Yahoo Finance chart API)
        # Pulls the latest prices for all stocks/instruments in the user's portfolio.
        logger.info(f"Planner [{job_id}]: Refreshing market prices...")
        await asyncio.to_thread(update_instrument_prices, job_id, db)

        # Stage 3: High-level statistics calculation
        # Computes total portfolio value, cash balance, and years to retirement to build the context summary.
        logger.info(f"Planner [{job_id}]: Calculating portfolio statistics summary...")
        summary = await asyncio.to_thread(load_portfolio_summary, job_id, db)

        # Stage 4: Run LiteLLM agent loop to coordinate sub-agents
        # Assembles the LiteLLM config: model, available tools (sub-agents), and the task prompt.
        model, tools, task, _ = create_agent(job_id, summary, db)

        # Prepare messages context starting with system instructions and user task
        messages = [
            {"role": "system", "content": ORCHESTRATOR_INSTRUCTIONS},
            {"role": "user",   "content": task},
        ]

        # Loop up to 20 turns to handle multiple sequential tool requests.
        # The model might decide to call reporter first, see its result, then call charter, then retirement.
        for turn in range(20):
            logger.info(f"Planner [{job_id}]: LLM decision turn {turn + 1}")
            
            # acompletion is the asynchronous version of litellm's completion function.
            # It sends the conversation history and tool definitions to the LLM.
            # By using await, we let other async tasks run while we wait for the model's response.
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
            
            # Extract the model's message response
            message = response.choices[0].message
            # Convert Pydantic message object into a standard dictionary so we can append it to messages list
            messages.append(message.model_dump() if hasattr(message, "model_dump") else message.dict())

            """
                ChatCompletionMessage(
                    content=None,
                    role='assistant',
                    function_call=None,
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id='call_abc123XYZ',
                            function=Function(
                                arguments='{"job_id": "5aa90c8b-da31-491d-9199-5e830d7e61a6"}',
                                name='invoke_reporter'
                            ),
                            type='function'
                        )
                    ]
                )

            """
            # Check if the model requested to execute a tool (like calling a sub-agent)
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                # If there are no tool calls, the model has finished its coordination and is done.
                logger.info(f"Planner [{job_id}]: No further tool execution requested. Done.")
                break

            # Execute the requested tools (sub-agents) sequentially
            for tc in tool_calls:
                name = tc.function.name
                logger.info(f"Planner [{job_id}]: Calling tool → {name}")
                
                # Check which tool the model requested, and execute the corresponding async function.
                if name == "invoke_reporter":
                    result = await invoke_reporter(job_id)
                elif name == "invoke_charter":
                    result = await invoke_charter(job_id)
                elif name == "invoke_retirement":
                    result = await invoke_retirement(job_id)
                else:
                    result = f"Unknown tool identifier: {name}"
                
                # Append the tool's execution result back to the messages list.
                # The model needs to see this result in the next turn to decide what to do next.
                messages.append({
                    "role": "tool", 
                    "tool_call_id": tc.id, 
                    "name": name, 
                    "content": result
                })

        # Update final status in database to "completed" upon successful completion
        db.jobs.update_status(job_id, "completed")
        logger.info(f"Planner [{job_id}]: Pipeline completed successfully ✓")

    except Exception as e:
        logger.error(f"Planner [{job_id}]: Pipeline execution failed: {e}", exc_info=True)
        # Update database with job failure and save the error message so the user knows what went wrong
        db.jobs.update_status(job_id, "failed", error_message=str(e))
        raise


from observability import observe


def lambda_handler(event, context):
    """
    AWS Lambda handler entry point.
    Receives events from SQS triggers or direct invokes, parsing the job_id payload.
    """
    # Wrap execution inside the observe context manager to set up Langfuse tracing and flush
    # all remaining telemetry data to the server before the Lambda environment freezes.
    with observe():
        job_id = None
        # Handle SQS queue triggers (messages are wrapped inside a list under the 'Records' key)
        if "Records" in event:
            body = event["Records"][0]["body"]
            try:
                body = json.loads(body)
                job_id = body.get("job_id", body)
            except json.JSONDecodeError:
                job_id = body
        else:
            # Handle direct API Gateway triggers or test event payloads
            job_id = event.get("job_id")

        if not job_id:
            return {"statusCode": 400, "body": json.dumps({"error": "No job_id provided"})}

        # Start execution within the standard asyncio event loop
        asyncio.run(run_orchestrator(job_id))
        return {"statusCode": 200, "body": json.dumps({"success": True})}