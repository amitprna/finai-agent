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

# Setup logger to help with local debugging and logging in AWS CloudWatch logs
logger = logging.getLogger(__name__)

# ----------------------------------------------------
# Downstream Sub-Agent Lambda Function Names
# ----------------------------------------------------
# These read the physical names of deployed AWS Lambda functions from environment variables.
# In production, these variables point to the actual AWS ARN or Lambda name (e.g. 'finai-prod-reporter').
# If the environment variables are not set, they fallback to standard defaults like 'finai-reporter'.
REPORTER_FUNCTION   = os.getenv("REPORTER_FUNCTION",   "finai-reporter")
CHARTER_FUNCTION    = os.getenv("CHARTER_FUNCTION",    "finai-charter")
RETIREMENT_FUNCTION = os.getenv("RETIREMENT_FUNCTION", "finai-retirement")
TAGGER_FUNCTION     = os.getenv("TAGGER_FUNCTION",     "finai-tagger")

# MOCK_LAMBDAS:
# A boolean flag that controls whether the orchestrator calls real deployed AWS Lambda functions,
# or executes sub-agents locally within the same Python process.
# Setting this to 'true' in '.env' allows developers to test the agent loop locally without AWS credentials.
MOCK_LAMBDAS = os.getenv("MOCK_LAMBDAS", "false").lower() == "true"


@dataclass
class PlannerContext:
    """
    Holds job contextual variables utilized by the Planner execution system.
    This simple container holds the active job_id and can be expanded to hold other metadata.
    """
    job_id: str


# ----------------------------------------------------
# Downstream Agent Invocation Wrappers
# ----------------------------------------------------

async def _call_agent_local(agent_name: str, job_id: str) -> str:
    """
    Import and run sub-agent handlers inside the same Python process.
    
    Why do we do this?
    If MOCK_LAMBDAS is True, we want to run the sub-agents (Reporter, Charter, Retirement)
    locally. Instead of making network calls to AWS, we dynamically import the python modules
    and run their handlers directly.

    How does it work?
    1. We compute the absolute path to the parent directory ('backend') so Python knows where to look.
    2. We inject this path into 'sys.path' if it's not already there.
    3. We dynamically import the specific handler (e.g. 'run_reporter') and run it.
    """
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    try:
        # Dynamically import and execute the downstream handler based on the target agent name
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
        
        # Return a success string with the completion message
        return f"{agent_name} completed: {result.get('message', 'done')}"
    except Exception as e:
        logger.error(f"[LOCAL] {agent_name} execution error: {e}", exc_info=True)
        return f"{agent_name} failed: {e}"


async def _call_agent_lambda(agent_name: str, function_name: str, job_id: str) -> str:
    """
    Call deployed AWS Lambda function using the boto3 client.
    
    Why do we do this?
    In a real production environment on AWS, we want each agent to run in its own isolated
    microservice (Lambda). This function handles the network requests to invoke those Lambdas.

    How does it work?
    1. We initialize the AWS Lambda service client using boto3.
    2. We call lambda_client.invoke with the function name and a JSON payload containing the job_id.
    3. We use InvocationType='RequestResponse' to perform a synchronous invocation (meaning we block
       and wait for the sub-agent to finish executing and return its results).
    4. We read and parse the payload returned by the Lambda function.
    """
    try:
        lambda_client = boto3.client("lambda")
        # Synchronously invoke the specific Lambda target function
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"job_id": job_id}),
        )
        # Read the raw stream from the response payload and parse it as JSON
        result = json.loads(response["Payload"].read())
        # AWS Lambda handlers often return responses in a standard API Gateway proxy format:
        # {"statusCode": 200, "body": "{...}"}. We extract and parse the body if present.
        if isinstance(result, dict) and "body" in result:
            result = json.loads(result["body"]) if isinstance(result["body"], str) else result["body"]
        return f"{agent_name} completed: {result.get('message', 'done')}"
    except Exception as e:
        logger.error(f"[LAMBDA] {agent_name} execution error: {e}")
        return f"{agent_name} failed: {e}"


# ----------------------------------------------------
# LLM Planner Tools
# ----------------------------------------------------
# These three async functions are the actual Python actions that the Orchestrator LLM can invoke.
# When the LLM calls 'invoke_reporter', the orchestrator executes this function, which routes
# the call locally or to AWS Lambda depending on the MOCK_LAMBDAS flag.

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
    
    Why is this needed?
    If a user has recently added a new stock (like 'RELIANCE') that isn't in our database database,
    we don't know what sector it belongs to or what region it invests in. Without this metadata,
    the Charter and Reporter cannot create accurate allocation breakdowns.
    We scan the positions first, and if we find any instrument without sector/region allocation,
    we invoke the Tagger Agent (which uses an LLM to research the company and tag it in the database).
    """
    # Look up the active job record in the database
    job = db.jobs.find_by_id(job_id)
    if not job:
        return

    user_id = job["user_id"]
    # Retrieve all investment accounts associated with the user
    accounts = db.accounts.find_by_user(user_id)
    missing = []

    # Iterate over user accounts and position holdings to find instruments missing tag metadata
    for account in accounts:
        for pos in db.positions.find_by_account(account["id"]):
            instrument = db.instruments.find_by_symbol(pos["symbol"])
            if instrument:
                # An instrument is considered fully tagged if it has both geographic and sector lists
                has_data = instrument.get("allocation_regions") and instrument.get("allocation_sectors")
                if not has_data:
                    missing.append({"symbol": pos["symbol"], "name": instrument.get("name", "")})
            else:
                # Symbol is completely unknown to the database instruments catalog
                missing.append({"symbol": pos["symbol"], "name": ""})

    # If all positions are already tagged, we have nothing to do
    if not missing:
        logger.info("Planner: All portfolio instruments have allocation profiles ✓")
        return

    logger.info(f"Planner: {len(missing)} instruments need tagging: {[m['symbol'] for m in missing]}")

    # Invoke Tagger agent to tag missing symbols (local import or AWS Lambda invocation)
    if MOCK_LAMBDAS:
        try:
            backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            if backend_dir not in sys.path:
                sys.path.insert(0, backend_dir)
            from tagger.lambda_handler import lambda_handler as tagger_handler
            # Call tagger_handler synchronously in-process
            tagger_handler({"instruments": missing}, None)
        except Exception as e:
            logger.error(f"Planner: Local Tagger call failed: {e}")
    else:
        try:
            # Call tagger_handler via AWS Lambda invoke
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
    
    Why is this needed?
    If a user has 50 positions across 5 accounts, sending all 50 position records to the main orchestrator
    LLM consumes a large amount of token context and can cause the model to get distracted.
    Instead, we do the math (summing positions and calculating total value) and feed the model
    a clean high-level summary (e.g. Total Value = ₹10,00,000, 2 accounts, 10 positions).
    """
    # Fetch job record to map the user
    job = db.jobs.find_by_id(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    user_id = job["user_id"]
    # Retrieve user's demographic profile (to fetch retirement goals)
    user = db.users.find_by_user_id(user_id)
    # Fetch all investment accounts for the user
    accounts = db.accounts.find_by_user(user_id)

    total_value = 0.0
    total_positions = 0
    total_cash = 0.0

    # Calculate portfolio values
    for account in accounts:
        # Sum cash balance in the account
        total_cash += float(account.get("cash_balance", 0))
        # Find holdings in the account
        positions = db.positions.find_by_account(account["id"])
        total_positions += len(positions)
        
        # Calculate market value of each holding (quantity * current price)
        for pos in positions:
            instrument = db.instruments.find_by_symbol(pos["symbol"])
            if instrument and instrument.get("current_price"):
                total_value += float(instrument["current_price"]) * float(pos["quantity"])

    return {
        "total_value": total_value + total_cash,
        "num_accounts": len(accounts),
        "num_positions": total_positions,
        # Default to 25 years and ₹8,00,000 retirement target if user settings are unconfigured
        "years_until_retirement": user.get("years_until_retirement", 25) if user else 25,
        "target_retirement_income": float(user.get("target_retirement_income", 800000)) if user else 800000,
    }


# ----------------------------------------------------
# LLM Orchestrator Construction
# ----------------------------------------------------

def create_agent(job_id: str, portfolio_summary: Dict[str, Any], db):
    """
    Assembles model identifier, tools list, context structure, and task instructions.
    
    This function configures the environment and properties needed to construct the LLM call.
    It returns:
    1. model: The string identifier used to call Bedrock (e.g. 'bedrock/moonshotai.kimi-k2.5')
    2. tools: A JSON Schema list defining the functions (Reporter, Charter, Retirement) the LLM can call
    3. task: The dynamic prompt explaining the user's portfolio summary and directing the LLM what to do
    4. context: A data object containing the job ID
    """
    # Read the target Bedrock model name and region from environment variables.
    model_id = os.getenv("BEDROCK_MODEL_ID", "moonshotai.kimi-k2.5")
    bedrock_region = os.getenv("BEDROCK_REGION", "us-west-2")
    # LiteLLM looks at AWS_REGION_NAME to figure out where to make the Bedrock API requests.
    os.environ["AWS_REGION_NAME"] = bedrock_region
    model = f"bedrock/{model_id}"

    # Declare the functions exposed to the LLM agent using standard JSON Schema format.
    # The LLM reads these descriptions to decide which tools to call.
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

    # Task statement providing critical summary numbers.
    # We pass total values and counts so the Planner LLM has basic context of the portfolio size.
    task = (
        f"Job ID: {job_id}\n"
        f"Portfolio: {portfolio_summary['num_accounts']} accounts, "
        f"{portfolio_summary['num_positions']} positions, "
        f"₹{portfolio_summary['total_value']:,.0f} total value.\n"
        f"Years to retirement: {portfolio_summary['years_until_retirement']}.\n\n"
        f"Please invoke the three analysis agents (reporter, charter, retirement) to compile details."
    )

    return model, tools, task, PlannerContext(job_id=job_id)

