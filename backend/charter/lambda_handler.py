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
# Path Bootstrapping
# ----------------------------------------------------
# Adjust path contexts for Lambda container runtime compatibility.
# Injects the directory containing this script and its parent directory to sys.path,
# and clears any pre-existing templates and agent modules from sys.modules cache.
_dir = Path(__file__).parent.absolute()
for _p in [str(_dir), str(_dir.parent)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _m in ["templates", "agent"]:
    sys.modules.pop(_m, None)

import litellm
from dotenv import load_dotenv

# Load local environment variables from .env files if present
load_dotenv(override=True)

from src import Database
from templates import CHARTER_INSTRUCTIONS
from agent import create_agent

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
    # Throws a ValueError if the job record is not found in the table.
    job = db.jobs.find_by_id(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
        
    # Extract the user ID linked to the active analysis job.
    user_id = job["user_id"]
    # Retrieve user retirement criteria and years-to-retirement goals.
    user = db.users.find_by_user_id(user_id)
    # Fetch all investment accounts belonging to the targeted user.
    accounts = db.accounts.find_by_user(user_id)
    
    # Initialize the base portfolio dictionary.
    # Sets default years to retirement to 25 if the user profile is missing.
    portfolio = {
        "user_id": user_id,
        "years_until_retirement": user.get("years_until_retirement", 25) if user else 25,
        "accounts": [],
    }
    
    # Walk through each investment account to extract positions and asset balances.
    for account in accounts:
        # Fetch all share positions registered inside this account from the database.
        positions = db.positions.find_by_account(account["id"])
        # Append the account name, cash balance, and positions list.
        # Resolves each position symbol to its database instrument details dynamically.
        portfolio["accounts"].append({
            "name": account["account_name"],
            "cash_balance": float(account.get("cash_balance", 0.0)),
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

    Args:
        job_id: The active analysis job UUID.

    Returns:
        Dictionary mapping success status and count of visual charts generated.
    """
    # Initialize the shared database interface.
    db = Database()
    # Load all user account balances and holdings data into memory.
    portfolio = _load_portfolio(job_id, db)
    # Build the model target string and the task prompt text.
    model, task = create_agent(job_id, portfolio, db)

    # Call the LLM completion API asynchronously using LiteLLM.
    # Feeds the system instructions (charter templates) and user portfolio text metrics.
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
    # Extract the raw text content from the completion choice.
    output = response.choices[0].message.content or ""
    logger.info(f"Charter [{job_id}]: Received LLM output of length = {len(output)}")

    # Extract the raw JSON block boundaries from the LLM output string.
    # Locates the first open brace '{' and the last close brace '}' to ignore extra wrapping text.
    charts_data = {}
    start = output.find("{")
    end = output.rfind("}")
    
    # Verify valid brace index boundaries before parsing.
    if start >= 0 and end > start:
        try:
            # Parse the extracted string slice into standard Python dictionaries.
            parsed = json.loads(output[start:end + 1])
            # Process and map each chart definition list item.
            # Extracts the custom key to use as the dictionary mapping key.
            for chart in parsed.get("charts", []):
                key = chart.pop("key", f"chart_{len(charts_data) + 1}")
                charts_data[key] = chart
            logger.info(f"Charter [{job_id}]: Successfully parsed {len(charts_data)} charts ✓")
        except json.JSONDecodeError as e:
            logger.error(f"Charter [{job_id}]: JSON decode failure: {e}")
    else:
        logger.error(f"Charter [{job_id}]: No JSON structures detected inside LLM output")

    # If successfully parsed chart definitions exist, update database.
    # Saves the chart config map under the job record's charts_payload column.
    if charts_data:
        db.jobs.update_charts(job_id, charts_data)
        logger.info(f"Charter [{job_id}]: Charts saved ✓")

    return {"success": bool(charts_data), "charts_generated": len(charts_data)}


from observability import observe


def lambda_handler(event, context):
    """
    AWS Lambda entry handler.
    Receives triggers, parses incoming payloads, and runs the main event loop.

    Args:
        event: Lambda trigger payload dictionary.
        context: Lambda execution context metadata.

    Returns:
        Dictionary mapping HTTP status code and response payload body.
    """
    # Wrap execution inside the observability context manager to flush Langfuse telemetries.
    with observe():
        # Parse the event if passed as a raw string.
        if isinstance(event, str):
            event = json.loads(event)
        # Extract the job ID parameter.
        job_id = event.get("job_id")
        if not job_id:
            return {"statusCode": 400, "body": json.dumps({"error": "job_id is required"})}
        
        # Start execution within standard asyncio loop.
        result = asyncio.run(run_charter(job_id))
        return {"statusCode": 200, "body": json.dumps(result)}