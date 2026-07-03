"""
FinAI Planner Agent - Tool Definitions & Orchestration Configuration
Defines the main orchestrator agent's context structure, sub-agent invocation wrappers,
instrument pre-processing logic, and tools.
"""

import os
import sys
import json
import boto3
import logging
from typing import Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Fetch environment-configured lambda names for downstream sub-agents
REPORTER_FUNCTION   = os.getenv("REPORTER_FUNCTION",   "finai-reporter")
CHARTER_FUNCTION    = os.getenv("CHARTER_FUNCTION",    "finai-charter")
RETIREMENT_FUNCTION = os.getenv("RETIREMENT_FUNCTION", "finai-retirement")
TAGGER_FUNCTION     = os.getenv("TAGGER_FUNCTION",     "finai-tagger")

# Run in mock/local mode where downstream agents are executed in-process
# (ideal for local testing without deploying individual lambdas)
MOCK_LAMBDAS = os.getenv("MOCK_LAMBDAS", "false").lower() == "true"


@dataclass
class PlannerContext:
    """Holds job contextual variables utilized by the Planner execution system."""
    job_id: str


# ----------------------------------------------------
# Downstream Agent Invocation Wrappers
# ----------------------------------------------------

async def _call_agent_local(agent_name: str, job_id: str) -> str:
    """
    Import and run sub-agent handlers inside the same Python process.
    Injects root backend path to sys.path so modules can be imported directly.
    """
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    try:
        # Dynamically import and execute downstream handler based on agent identifier
        if agent_name == "Reporter":
            from reporter.lambda_handler import run_reporter
            result = await run_reporter(job_id)
        elif agent_name == "Charter":
            from charter.lambda_handler import run_charter
            result = await run_charter(job_id)
        elif agent_name == "Retirement":
            from retirement.lambda_handler import run_retirement
            result = await run_retirement(job_id)
        else:
            return f"Unknown local agent target: {agent_name}"
        return f"{agent_name} completed: {result.get('message', 'done')}"
    except Exception as e:
        logger.error(f"[LOCAL] {agent_name} execution error: {e}", exc_info=True)
        return f"{agent_name} failed: {e}"


async def _call_agent_lambda(agent_name: str, function_name: str, job_id: str) -> str:
    """
    Call deployed AWS Lambda function using the boto3 client.
    Performs a synchronous invoke (RequestResponse) with the job_id payload.
    """
    try:
        lambda_client = boto3.client("lambda")
        # Synchronously invoke the specific Lambda target function
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"job_id": job_id}),
        )
        # Parse payload response from lambda wrapper
        result = json.loads(response["Payload"].read())
        if isinstance(result, dict) and "body" in result:
            result = json.loads(result["body"]) if isinstance(result["body"], str) else result["body"]
        return f"{agent_name} completed: {result.get('message', 'done')}"
    except Exception as e:
        logger.error(f"[LAMBDA] {agent_name} execution error: {e}")
        return f"{agent_name} failed: {e}"


# Tool methods invoked by the LLM Planner agent

async def invoke_reporter(job_id: str) -> str:
    """
    Tool function exposing the Report Writer agent to the LLM.
    Sends user portfolio data to generate a detailed markdown assessment.
    """
    if MOCK_LAMBDAS:
        return await _call_agent_local("Reporter", job_id)
    return await _call_agent_lambda("Reporter", REPORTER_FUNCTION, job_id)


async def invoke_charter(job_id: str) -> str:
    """
    Tool function exposing the Chart Maker agent to the LLM.
    Builds data visualization charts (pie charts, sector holdings, etc.).
    """
    if MOCK_LAMBDAS:
        return await _call_agent_local("Charter", job_id)
    return await _call_agent_lambda("Charter", CHARTER_FUNCTION, job_id)


async def invoke_retirement(job_id: str) -> str:
    """
    Tool function exposing the Retirement Specialist agent to the LLM.
    Performs Monte Carlo wealth accumulation and withdrawal projections.
    """
    if MOCK_LAMBDAS:
        return await _call_agent_local("Retirement", job_id)
    return await _call_agent_lambda("Retirement", RETIREMENT_FUNCTION, job_id)


# ----------------------------------------------------
# Instrument Pre-Processing
# ----------------------------------------------------

def handle_missing_instruments(job_id: str, db) -> None:
    """
    Pre-processing scan: checks if any portfolio holdings do not have
    geographic, sector, or asset class allocation metadata.
    If missing, runs the Tagger Agent to classify the tickers before proceeding.
    """
    job = db.jobs.find_by_id(job_id)
    if not job:
        return

    user_id = job["user_id"]
    accounts = db.accounts.find_by_user(user_id)
    missing = []

    # Iterate over user accounts and position holdings
    for account in accounts:
        for pos in db.positions.find_by_account(account["id"]):
            instrument = db.instruments.find_by_symbol(pos["symbol"])
            if instrument:
                has_data = instrument.get("allocation_regions") and instrument.get("allocation_sectors")
                if not has_data:
                    missing.append({"symbol": pos["symbol"], "name": instrument.get("name", "")})
            else:
                # Add to missing list if symbol not indexed in db at all
                missing.append({"symbol": pos["symbol"], "name": ""})

    if not missing:
        logger.info("Planner: All portfolio instruments have allocation profiles ✓")
        return

    logger.info(f"Planner: {len(missing)} instruments need tagging: {[m['symbol'] for m in missing]}")

    # Invoke Tagger agent to tag missing symbols (local import or AWS invocation)
    if MOCK_LAMBDAS:
        try:
            backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            if backend_dir not in sys.path:
                sys.path.insert(0, backend_dir)
            from tagger.lambda_handler import lambda_handler as tagger_handler
            tagger_handler({"instruments": missing}, None)
        except Exception as e:
            logger.error(f"Planner: Local Tagger call failed: {e}")
    else:
        try:
            lambda_client = boto3.client("lambda")
            lambda_client.invoke(
                FunctionName=TAGGER_FUNCTION,
                InvocationType="RequestResponse",
                Payload=json.dumps({"instruments": missing}),
            )
        except Exception as e:
            logger.error(f"Planner: Tagger Lambda invocation failed: {e}")


def load_portfolio_summary(job_id: str, db) -> Dict[str, Any]:
    """
    Loads portfolio values, cash balances, and retirement goals.
    Provides a high-level summary to prevent flooding the Planner LLM context with raw positions list.
    """
    job = db.jobs.find_by_id(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    user_id = job["user_id"]
    user = db.users.find_by_user_id(user_id)
    accounts = db.accounts.find_by_user(user_id)

    total_value = 0.0
    total_positions = 0
    total_cash = 0.0

    for account in accounts:
        total_cash += float(account.get("cash_balance", 0))
        positions = db.positions.find_by_account(account["id"])
        total_positions += len(positions)
        for pos in positions:
            instrument = db.instruments.find_by_symbol(pos["symbol"])
            if instrument and instrument.get("current_price"):
                total_value += float(instrument["current_price"]) * float(pos["quantity"])

    return {
        "total_value": total_value + total_cash,
        "num_accounts": len(accounts),
        "num_positions": total_positions,
        "years_until_retirement": user.get("years_until_retirement", 25) if user else 25,
        "target_retirement_income": float(user.get("target_retirement_income", 800000)) if user else 800000,
    }


# ----------------------------------------------------
# LLM Orchestrator Construction
# ----------------------------------------------------

def create_agent(job_id: str, portfolio_summary: Dict[str, Any], db):
    """
    Assembles model identifier, tools list, context structure, and task instructions.
    """
    model_id = os.getenv("BEDROCK_MODEL_ID", "moonshotai.kimi-k2.5")
    bedrock_region = os.getenv("BEDROCK_REGION", "us-west-2")
    os.environ["AWS_REGION_NAME"] = bedrock_region
    model = f"bedrock/{model_id}"

    # Declare functions exposed to the LLM agent
    tools = [
        {"type": "function", "function": {
            "name": "invoke_reporter",
            "description": "Generates a detailed portfolio analysis report in markdown.",
            "parameters": {"type": "object", "properties": {
                "job_id": {"type": "string", "description": "The current analysis job ID"}
            }, "required": ["job_id"]},
        }},
        {"type": "function", "function": {
            "name": "invoke_charter",
            "description": "Builds portfolio chart visual data summaries (pie/bar charts).",
            "parameters": {"type": "object", "properties": {
                "job_id": {"type": "string", "description": "The current analysis job ID"}
            }, "required": ["job_id"]},
        }},
        {"type": "function", "function": {
            "name": "invoke_retirement",
            "description": "Computes retirement Monte Carlo projections and timeline readiness.",
            "parameters": {"type": "object", "properties": {
                "job_id": {"type": "string", "description": "The current analysis job ID"}
            }, "required": ["job_id"]},
        }},
    ]

    # Task statement providing critical summary numbers
    task = (
        f"Job ID: {job_id}\n"
        f"Portfolio: {portfolio_summary['num_accounts']} accounts, "
        f"{portfolio_summary['num_positions']} positions, "
        f"₹{portfolio_summary['total_value']:,.0f} total value.\n"
        f"Years to retirement: {portfolio_summary['years_until_retirement']}.\n\n"
        f"Please invoke the three analysis agents (reporter, charter, retirement) to compile details."
    )

    return model, tools, task, PlannerContext(job_id=job_id)
