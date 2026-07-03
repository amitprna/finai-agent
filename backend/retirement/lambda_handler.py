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
# Path Bootstrapping
# ----------------------------------------------------
# Adjust path contexts for Lambda container runtime compatibility
_dir = Path(__file__).parent.absolute()
for _p in [str(_dir), str(_dir.parent)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _m in ["templates", "agent"]:
    sys.modules.pop(_m, None)

import litellm
from dotenv import load_dotenv

load_dotenv(override=True)

from src import Database
from templates import RETIREMENT_INSTRUCTIONS
from agent import create_agent

logger = logging.getLogger(__name__)


def _load_portfolio(job_id: str, db) -> dict:
    """
    Retrieve full user portfolio assets and account details from the database.
    """
    job = db.jobs.find_by_id(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")
        
    user_id = job["user_id"]
    user = db.users.find_by_user_id(user_id)
    accounts = db.accounts.find_by_user(user_id)
    
    portfolio = {
        "user_id": user_id,
        "years_until_retirement": user.get("years_until_retirement", 25) if user else 25,
        "target_retirement_income": float(user.get("target_retirement_income", 800000.0)) if user else 800000.0,
        "accounts": [],
    }
    for account in accounts:
        positions = db.positions.find_by_account(account["id"])
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


async def run_retirement(job_id: str) -> dict:
    """
    Main execution method of the Retirement Specialist agent.
    Runs the LLM model to return a retirement projection narrative, then updates the job record.
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

    # Call the model
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

    # Write final narrative back to the jobs table
    db.jobs.update_retirement(job_id, {
        "analysis": analysis,
        "generated_at": datetime.utcnow().isoformat(),
        "agent": "retirement",
    })
    logger.info(f"Retirement [{job_id}]: Retirement analysis completed and saved ✓")
    return {"success": True, "message": "Retirement analysis completed"}


from observability import observe


def lambda_handler(event, context):
    """AWS Lambda entry handler."""
    with observe():
        if isinstance(event, str):
            event = json.loads(event)
        job_id = event.get("job_id")
        if not job_id:
            return {"statusCode": 400, "body": json.dumps({"error": "job_id is required"})}
        
        result = asyncio.run(run_retirement(job_id))
        return {"statusCode": 200, "body": json.dumps(result)}