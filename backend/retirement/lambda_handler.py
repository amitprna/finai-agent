"""
FinAI Retirement Specialist - Retirement Projection Agent Lambda Handler
Loads user portfolio datasets, feeds it to the LiteLLM client, and writes
the final markdown retirement analysis back to the database.
"""

import os
import sys
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime

# ----------------------------------------------------
# Path Bootstrapping & Module Resolution
# ----------------------------------------------------
# Adjust path contexts dynamically inside Lambda environments so python can find sibling folders
# and files. We also pop templates and agent modules from sys.modules to prevent container caching.
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
from templates import RETIREMENT_INSTRUCTIONS
from agent import create_agent

# Initialize module logger
logger = logging.getLogger(__name__)


def _load_portfolio(job_id: str, db) -> dict:
    """
    Retrieve full user portfolio assets and account details from the database.
    
    Why do we need this?
    Before calculating Monte Carlo projections, we must know the user's cash balance
    and position details. This function retrieves and aggregates:
    1. Years until retirement.
    2. Target annual income.
    3. Cash balances and quantities of shares held.
    4. Sibling instrument profiles (allocation classes, current prices).
    """
    job = db.jobs.find_by_id(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
        
    user_id = job["user_id"]
    user = db.users.find_by_user_id(user_id)
    accounts = db.accounts.find_by_user(user_id)
    
    # Establish baseline dictionary
    portfolio = {
        "user_id": user_id,
        "years_until_retirement": user.get("years_until_retirement", 25) if user else 25,
        "target_retirement_income": float(user.get("target_retirement_income", 800000.0)) if user else 800000.0,
        "accounts": [],
    }
    
    # Loop over accounts to gather cash and positions details
    for account in accounts:
        positions = db.positions.find_by_account(account["id"])
        portfolio["accounts"].append({
            "name": account["account_name"],
            "cash_balance": float(account.get("cash_balance", 0.0)),
            # Match each stock symbol with its corresponding tag-index instrument specifications
            "positions": [
                {"symbol": p["symbol"], "quantity": float(p["quantity"]),
                 "instrument": db.instruments.find_by_symbol(p["symbol"])}
                for p in positions
            ],
        })
    return portfolio


async def run_retirement(job_id: str) -> dict:
    """
    Main execution method of the Retirement Specialist agent.
    Runs the LLM model to return a retirement projection narrative, then updates the job record.
    
    How does it work?
    1. Loads the user portfolio.
    2. Runs the agent configuration helper to generate the task prompt (loaded with simulation outcomes).
    3. Submits the prompt to Bedrock using 'litellm.acompletion'.
    4. Saves the generated analysis narrative text back to the database.
    """
    db = Database()
    portfolio = _load_portfolio(job_id, db)
    
    # Establish default parameters context
    user_prefs = {
        "years_until_retirement": portfolio.get("years_until_retirement", 25),
        "target_retirement_income": portfolio.get("target_retirement_income", 800000.0),
        "current_age": 35,
    }
    model, _, task = create_agent(job_id, portfolio, user_prefs, db)

    # Call the model asynchronously using LiteLLM wrapper
    response = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": RETIREMENT_INSTRUCTIONS},
            {"role": "user",   "content": task},
        ],
        max_tokens=4000,
        metadata={
            "trace_id": job_id,
            "trace_name": "Retirement Specialist Agent",
            "session_id": job_id
        }
    )
    analysis = response.choices[0].message.content or ""

    # Write final narrative back to the jobs table (updates retirement columns)
    db.jobs.update_retirement(job_id, {
        "analysis": analysis,
        "generated_at": datetime.utcnow().isoformat(),
        "agent": "retirement",
    })
    logger.info(f"Retirement [{job_id}]: Retirement analysis completed and saved ✓")
    return {"success": True, "message": "Retirement analysis completed"}


from observability import observe


def lambda_handler(event, context):
    """
    AWS Lambda entry handler.
    Sets up tracing callbacks, parses the incoming payload, and triggers the asynchronous pipeline.
    """
    with observe():
        if isinstance(event, str):
            event = json.loads(event)
            
        job_id = event.get("job_id")
        if not job_id:
            return {"statusCode": 400, "body": json.dumps({"error": "job_id is required"})}
        
        # Start execution within standard event loop
        result = asyncio.run(run_retirement(job_id))
        return {"statusCode": 200, "body": json.dumps(result)}