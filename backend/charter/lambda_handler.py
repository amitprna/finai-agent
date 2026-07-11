"""
FinAI Charter - Chart Maker Agent Lambda Handler
This Lambda function loads user portfolio datasets from the database, sends them to the LiteLLM
client, extracts Plotly-JSON chart blocks, and saves the visualization data back to the database.
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
# Setup paths dynamically in AWS Lambda containers to ensure python can import sibling directories.
# We also clear templates and agent caches from sys.modules to enforce reload of fresh configurations.
_dir = Path(__file__).parent.absolute()
for _p in [str(_dir), str(_dir.parent)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _m in ["templates", "agent"]:
    sys.modules.pop(_m, None)

import litellm
from dotenv import load_dotenv

# Load local environment parameters
load_dotenv(override=True)

from src import Database
from templates import CHARTER_INSTRUCTIONS
from agent import create_agent

# Initialize module logger to track parsing metrics
logger = logging.getLogger(__name__)


def _load_portfolio(job_id: str, db) -> dict:
    """
    Retrieve full user portfolio assets and account details from the database.

    Args:
        job_id: The active analysis job UUID.
        db: The initialized Database instance wrapper.

    Returns:
        Structured dictionary payload mapping accounts and positions data.
    """
    # Look up the job record in the database using the unique job ID.
    job = db.jobs.find_by_id(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
        
    user_id = job["user_id"]
    user = db.users.find_by_user_id(user_id)
    accounts = db.accounts.find_by_user(user_id)
    
    portfolio = {
        "user_id": user_id,
        "years_until_retirement": user.get("years_until_retirement", 25) if user else 25,
        "accounts": [],
    }
    
    # Loop over accounts to gather uninvested cash and positions details
    for account in accounts:
        positions = db.positions.find_by_account(account["id"])
        portfolio["accounts"].append({
            "name": account["account_name"],
            "cash_balance": float(account.get("cash_balance", 0.0)),
            # Fetch instrument specifications (asset percentages and current price) for each holding
            "positions": [
                {"symbol": p["symbol"], "quantity": float(p["quantity"]),
                 "instrument": db.instruments.find_by_symbol(p["symbol"])}
                for p in positions
            ],
        })
    return portfolio


async def run_charter(job_id: str) -> dict:
    """
    Main execution method of the Charter agent.
    Runs the LLM model to return plotly-compatible JSON configs, then updates the job record.

    How does it work?
    1. Loads the portfolio variables and runs the agent setup to get the model string and task.
    2. Sends the task and system instructions to the LLM.
    3. The model returns a string that contains a JSON object representing Plotly charts.
    4. We find the boundaries of the JSON object using string find methods ('{' and '}'), parse it,
       and store it under the jobs database record.
    """
    db = Database()
    portfolio = _load_portfolio(job_id, db)
    model, task = create_agent(job_id, portfolio, db)

    # Call the LLM completion API asynchronously using LiteLLM.
    response = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": CHARTER_INSTRUCTIONS},
            {"role": "user",   "content": task},
        ],
        max_tokens=4000,
        metadata={
            "trace_id": job_id,
            "trace_name": "Chart Generator Agent",
            "session_id": job_id
        }
    )
    
    # Extract the raw text content from the completion response
    output = response.choices[0].message.content or ""
    logger.info(f"Charter [{job_id}]: Received LLM output of length = {len(output)}")

    # ----------------------------------------------------
    # Robust JSON Extraction Pattern (Brace Matching)
    # ----------------------------------------------------
    # Why do we do this?
    # LLMs frequently surround their actual JSON output with markdown indicators (like ```json ... ```)
    # or introductory text (like "Here are your charts:").
    # To bypass this conversational noise, we locate the first occurrence of '{' and the last occurrence of '}'.
    # Slice the string from first brace to last brace to isolate the pure JSON payload before calling json.loads.
    charts_data = {}
    start = output.find("{")
    end = output.rfind("}")
    
    # Verify valid brace index boundaries before parsing
    if start >= 0 and end > start:
        try:
            # Parse the isolated substring slice
            parsed = json.loads(output[start:end + 1])
            # Iterate through the returned charts list and catalog them under their respective keys
            for chart in parsed.get("charts", []):
                key = chart.pop("key", f"chart_{len(charts_data) + 1}")
                charts_data[key] = chart
            logger.info(f"Charter [{job_id}]: Successfully parsed {len(charts_data)} charts ✓")
        except json.JSONDecodeError as e:
            logger.error(f"Charter [{job_id}]: JSON decode failure: {e}")
    else:
        logger.error(f"Charter [{job_id}]: No JSON structures detected inside LLM output")

    # If chart details were parsed successfully, update database
    if charts_data:
        db.jobs.update_charts(job_id, charts_data)
        logger.info(f"Charter [{job_id}]: Charts saved ✓")

    return {"success": bool(charts_data), "charts_generated": len(charts_data)}


from observability import observe


def lambda_handler(event, context):
    """
    AWS Lambda entry handler.
    Receives triggers, parses incoming payloads, and runs the main event loop.
    """
    # Wrap execution inside the observability context manager to flush Langfuse telemetries.
    with observe():
        if isinstance(event, str):
            event = json.loads(event)
            
        job_id = event.get("job_id")
        if not job_id:
            return {"statusCode": 400, "body": json.dumps({"error": "job_id is required"})}
        
        # Start execution within standard asyncio loop.
        result = asyncio.run(run_charter(job_id))
        return {"statusCode": 200, "body": json.dumps(result)}