"""
FinAI Reporter Agent - Portfolio Formatting & Configuration
Defines formatting models and structures required to prompt the Report Writer.
"""

import os
import json
import boto3
import logging
from typing import Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ReporterContext:
    """
    Holds context metrics representing the current user's job and portfolio details.
    
    Attributes:
        job_id: Analysis job UUID
        portfolio_data: Dict containing user accounts and positions list
        db: Initialized database client instance
    """
    job_id: str
    portfolio_data: Dict[str, Any]
    db: Any = None


def format_portfolio(portfolio: Dict[str, Any]) -> str:
    """
    Format portfolio database collections into a clean, human-readable markdown segment.
    This serves as structural context within the LLM analyst's prompt.
    
    Why is this function used?
    LLMs understand structured text like Markdown extremely well. Instead of sending raw JSON
    to the LLM, we compile cash balances, tickers, shares, and current valuations into a nice,
    scannable document.

    How does it work?
    1. It extracts years to retirement and target income.
    2. It loops through accounts to show cash balances and positions.
    3. For each position, it multiplies quantity by share price to calculate holding value.
    4. It inspects 'allocation_asset_class' (e.g. {"equity": 80, "fixed_income": 20}) and dynamically
       labels the asset with its dominant class (using max()).
    """
    lines = ["# Portfolio Summary"]
    years = portfolio.get("years_until_retirement", 25)
    income = portfolio.get("target_retirement_income", 800000)
    lines.append(f"- Years to retirement: {years}")
    lines.append(f"- Target retirement income: ₹{income:,.0f}/year")
    lines.append("")

    total_val = 0.0
    # Walk through each investment account in the user's portfolio (e.g. Demat, PPF, NPS)
    for account in portfolio.get("accounts", []):
        cash = float(account.get("cash_balance", 0))
        name = account.get("name", "Account")
        lines.append(f"\n## {name}  (Cash: ₹{cash:,.0f})")
        
        # Walk holdings inside the current account
        for pos in account.get("positions", []):
            sym = pos.get("symbol", "")
            qty = float(pos.get("quantity") or 0)
            inst = pos.get("instrument") or {}
            price = float(inst.get("current_price") or 0)
            val = qty * price
            total_val += val
            
            # Determine asset class segment by finding the key with the highest allocation percentage.
            # Example: {"equity": 90, "fixed_income": 10} -> max() will return "equity".
            asset = (inst.get("allocation_asset_class") or {})
            cls = max(asset, key=asset.get) if asset else "?"
            lines.append(f"  - {sym}: {qty:,.0f} shares @ ₹{price:.2f} = ₹{val:,.0f}  [{cls}]")

    lines.append(f"\n**Total Invested:** ₹{total_val:,.0f}")
    return "\n".join(lines)


async def get_market_insights(symbols: List[str]) -> str:
    """
    Query the S3 Vectors index using the SageMaker endpoint and custom s3vectors client
    to retrieve relevant indexed research reports for the requested symbols.
    """
    if not symbols:
        return "No symbols provided for market research."

    # Fetch variables from environment
    region = os.getenv("DEFAULT_AWS_REGION", "us-east-1")
    sagemaker_endpoint = os.getenv("SAGEMAKER_ENDPOINT", "finai-embedding-endpoint")
    vector_bucket = os.getenv("VECTOR_BUCKET")

    if not vector_bucket:
        logger.warning("get_market_insights: VECTOR_BUCKET env var is not set. Falling back.")
        return f"Live market research is not configured. Portfolio details should be used directly."

    query_text = f"Financial details, news, and market analysis for {' '.join(symbols)}"

    try:
        # 1. Invoke SageMaker Serverless Endpoint to get embeddings
        sagemaker_client = boto3.client("sagemaker-runtime", region_name=region)
        sm_response = sagemaker_client.invoke_endpoint(
            EndpointName=sagemaker_endpoint,
            ContentType="application/json",
            Body=json.dumps({"inputs": query_text}),
        )
        sm_result = json.loads(sm_response["Body"].read().decode())
        
        # HuggingFace sentence-transformers return nested array formats depending on task shape
        query_embedding = sm_result[0] if isinstance(sm_result[0], list) else sm_result
        if isinstance(query_embedding[0], list):
            query_embedding = query_embedding[0]

        # 2. Run semantic search against the index to retrieve the ingested analysis
        s3vectors_client = boto3.client("s3vectors", region_name=region)
        search_response = s3vectors_client.query_vectors(
            vectorBucketName=vector_bucket,
            indexName="financial-research",
            queryVector={"float32": query_embedding},
            topK=3,
            returnDistance=True,
            returnMetadata=True,
        )

        vectors = search_response.get("vectors", [])
        if not vectors:
            return f"No indexed research reports or news updates found in database for symbols: {', '.join(symbols)}"

        insights = []
        for i, vec in enumerate(vectors, 1):
            metadata = vec.get("metadata", {})
            doc_text = metadata.get("text", "")
            source = metadata.get("source", "Unknown Source")
            timestamp = metadata.get("timestamp", "")
            insights.append(
                f"Document {i} [Source: {source}, Date: {timestamp}]:\n{doc_text}\n"
            )

        return "\n---\n".join(insights)

    except Exception as e:
        logger.error(f"get_market_insights: Failed to retrieve semantic research context: {e}", exc_info=True)
        return f"Market research lookups failed: {e}. Please complete the report using the portfolio data."


def create_agent(job_id: str, portfolio: Dict[str, Any], db=None):
    """
    Constructs model configuration, tasks, system prompts, and tools for the Reporter.
    
    This configures the Report Writer agent:
    1. Sets model ID (Bedrock/Moonshot Kimi).
    2. Exposes 'get_market_insights' as a function tool the LLM can call if it needs ticker data.
    3. Builds the task description by calling 'format_portfolio' to inject portfolio figures.
    """
    model_id = os.getenv("BEDROCK_MODEL_ID", "moonshotai.kimi-k2.5")
    bedrock_region = os.getenv("BEDROCK_REGION", "us-west-2")
    os.environ["AWS_REGION_NAME"] = bedrock_region
    model = f"bedrock/{model_id}"

    context = ReporterContext(job_id=job_id, portfolio_data=portfolio, db=db)

    # Downstream tools exposed to the Report Writer agent
    tools = [
        {"type": "function", "function": {
            "name": "get_market_insights",
            "description": "Retrieve live market context for specific ticker symbols.",
            "parameters": {"type": "object",
                           "properties": {"symbols": {"type": "array", "items": {"type": "string"},
                                                       "description": "List of ticker symbols"}},
                           "required": ["symbols"]},
        }},
    ]

    # Analysis task prompts defining the report guidelines
    task = (
        f"Analyse this Indian equity portfolio and write a comprehensive markdown report.\n\n"
        f"{format_portfolio(portfolio)}\n\n"
        "Your report should cover:\n"
        "- Executive Summary (3-4 key points)\n"
        "- Portfolio Composition & Diversification\n"
        "- Risk Assessment\n"
        "- Retirement Readiness (given user goals)\n"
        "- Specific Actionable Recommendations (5-7 items)\n"
        "- Conclusion\n\n"
        "Use ₹ for Indian Rupees. Be specific with numbers. Write in clear markdown."
    )

    return model, tools, task, context

