"""
FinAI Reporter Agent - Portfolio Formatting & Configuration
Defines formatting models and structures required to prompt the Report Writer.
"""

import os
import logging
from typing import Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ReporterContext:
    """Holds context metrics representing the current user's job and portfolio details."""
    job_id: str
    portfolio_data: Dict[str, Any]
    db: Any = None


def format_portfolio(portfolio: Dict[str, Any]) -> str:
    """
    Format portfolio database collections into a clean, human-readable markdown segment.
    This serves as structural context within the LLM analyst's prompt.
    """
    lines = ["# Portfolio Summary"]
    years = portfolio.get("years_until_retirement", 25)
    income = portfolio.get("target_retirement_income", 800000)
    lines.append(f"- Years to retirement: {years}")
    lines.append(f"- Target retirement income: ₹{income:,.0f}/year")
    lines.append("")

    total_val = 0.0
    # Walk through each investment account in the user's portfolio
    for account in portfolio.get("accounts", []):
        cash = float(account.get("cash_balance", 0))
        name = account.get("name", "Account")
        lines.append(f"\n## {name}  (Cash: ₹{cash:,.0f})")
        
        # Walk holdings inside the account
        for pos in account.get("positions", []):
            sym = pos.get("symbol", "")
            qty = float(pos.get("quantity") or 0)
            inst = pos.get("instrument") or {}
            price = float(inst.get("current_price") or 0)
            val = qty * price
            total_val += val
            
            # Determine asset class segment
            asset = (inst.get("allocation_asset_class") or {})
            cls = max(asset, key=asset.get) if asset else "?"
            lines.append(f"  - {sym}: {qty:,.0f} shares @ ₹{price:.2f} = ₹{val:,.0f}  [{cls}]")

    lines.append(f"\n**Total Invested:** ₹{total_val:,.0f}")
    return "\n".join(lines)


async def get_market_insights(symbols: List[str]) -> str:
    """
    Stub method representing semantic query expansion.
    In local execution environments, returns a simple fallback message.
    """
    if symbols:
        return (f"Live market research for {', '.join(symbols[:5])} is not configured in this local environment. "
                "Please proceed with the portfolio data you have.")
    return "No symbols provided for market research."


def create_agent(job_id: str, portfolio: Dict[str, Any], db=None):
    """
    Constructs model configuration, tasks, system prompts, and tools for the Reporter.
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

    # Analysis task prompts
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
