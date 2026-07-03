"""
FinAI Reporter - Report Writer Agent Lambda Handler
Loads user portfolio datasets, feeds it to the LiteLLM client, parses optional
tool queries, and writes final markdown reports back to the database.
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
# Setup path context before executing local module imports
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
from templates import REPORTER_INSTRUCTIONS
from agent import create_agent

logger = logging.getLogger(__name__)


def _load_portfolio(job_id: str, db) -> dict:
    """
    Retrieve full user portfolio assets and account details from the database.
    
    Args:
        job_id: Analysis job UUID
        db: Database client instance
        
    Returns:
        Structured dictionary payload representing the portfolio configuration
    """
    job = db.jobs.find_by_id(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    user_id = job["user_id"]
    user = db.users.find_by_user_id(user_id)
    accounts = db.accounts.find_by_user(user_id)

    # Standardize portfolio model properties
    portfolio = {
        "user_id": user_id,
        "years_until_retirement": user.get("years_until_retirement", 25) if user else 25,
        "target_retirement_income": float(user.get("target_retirement_income", 800000)) if user else 800000,
        "accounts": [],
    }
    for account in accounts:
        positions = db.positions.find_by_account(account["id"])
        portfolio["accounts"].append({
            "name": account["account_name"],
            "cash_balance": float(account.get("cash_balance", 0)),
            "positions": [
                {"symbol": p["symbol"], "quantity": float(p["quantity"]),
                 "instrument": db.instruments.find_by_symbol(p["symbol"])}
                for p in positions
            ],
        })
    return portfolio


async def run_reporter(job_id: str) -> dict:
    """
    Main execution method of the Reporter agent.
    Runs model generation loop, handling market insights tool callbacks if requested.
    Saves final report string to database.
    """
    db = Database()
    portfolio = _load_portfolio(job_id, db)
    model, tools, task, _ = create_agent(job_id, portfolio, db)

    messages = [
        {"role": "system", "content": REPORTER_INSTRUCTIONS},
        {"role": "user",   "content": task},
    ]

    report_text = ""
    # Loop up to 10 turns to handle downstream tool evaluations
    for turn in range(10):
        logger.info(f"Reporter [{job_id}]: LLM turn {turn + 1}")
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=4000,
            metadata={
                "trace_id": job_id,
                "trace_name": "Report Writer Agent",
                "session_id": job_id
            }
        )
        message = response.choices[0].message
        messages.append(message.model_dump() if hasattr(message, "model_dump") else message.dict())

        # Check if the model requested tools execution
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            report_text = message.content or ""
            break

        # Process tool calls (like market search requests)
        for tc in tool_calls:
            from agent import get_market_insights
            try:
                args = json.loads(tc.function.arguments)
                result = await get_market_insights(symbols=args.get("symbols", []))
            except Exception as e:
                result = f"Tool execution error: {e}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.function.name, "content": result})

    # Save final report to the jobs table
    db.jobs.update_report(job_id, {
        "content": report_text,
        "generated_at": datetime.utcnow().isoformat(),
        "agent": "reporter",
    })
    logger.info(f"Reporter [{job_id}]: Report generated and saved successfully ✓")
    return {"success": True, "message": "Report generated and saved"}


from observability import observe


def lambda_handler(event, context):
    """AWS Lambda entry handler."""
    with observe():
        if isinstance(event, str):
            event = json.loads(event)
        job_id = event.get("job_id")
        if not job_id:
            return {"statusCode": 400, "body": json.dumps({"error": "job_id is required"})}
        
        result = asyncio.run(run_reporter(job_id))
        return {"statusCode": 200, "body": json.dumps(result)}
