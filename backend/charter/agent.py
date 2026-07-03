"""
FinAI Charter Agent - Portfolio Visual Data Preparation
This script parses the user's active investment portfolio data (holdings, cash, accounts)
and calculates aggregated totals for asset classes, sectors, and geographic regions.
The resulting metrics are formatted as a textual task prompt for the Chart Maker LLM.
"""

import os
import logging
from typing import Dict, Any
from templates import CHARTER_INSTRUCTIONS, create_charter_task

# Initialize the module logger to track execution steps in CloudWatch
logger = logging.getLogger()


def analyze_portfolio(portfolio_data: Dict[str, Any]) -> str:
    """
    Parses raw portfolio structures and aggregates valuations across accounts.
    Calculates percentage weights and compiles textual metrics for the LLM.

    Args:
        portfolio_data: Dictionary mapping accounts, positions, and pricing metadata.

    Returns:
        String detailing portfolio analysis summaries and calculations.
    """
    # result: List of strings accumulated sequentially to form the final report text.
    result = []
    # total_value: Running sum tracking the entire net worth of the portfolio in INR.
    total_value = 0.0
    # position_values: Dictionary mapping each ticker symbol to its combined value.
    position_values = {}
    # account_totals: Dictionary mapping account names to cash balance and positions.
    account_totals = {}

    # ----------------------------------------------------
    # Account & Position Valuation Aggregation
    # ----------------------------------------------------
    # Loop through each investment account (e.g. Roth IRA, NPS, Demat) in the user's dataset.
    # Calculates total asset balances and compiles individual holding statistics.
    for account in portfolio_data.get("accounts", []):
        # account_name: Retrieve the user-defined name of the investment account.
        account_name = account.get("name", "Unknown")
        # account_type: Retrieve account category identifier (e.g., Demat, NPS).
        account_type = account.get("type", "unknown")
        
        # Extract the cash balance from the account parameters.
        # Handles empty strings or missing parameters by defaulting to 0.0.
        cash_balance = account.get("cash_balance")
        if cash_balance is None or cash_balance == "":
            cash = 0.0
        else:
            cash = float(cash_balance)

        # Initialize the account mapping dictionary if this is the first time we see this account name.
        # Tracks combined value, account classification, and nested lists of positions.
        if account_name not in account_totals:
            account_totals[account_name] = {"value": 0.0, "type": account_type, "positions": []}

        # Add the uninvested cash balance to this specific account's total value.
        # Also credit this cash amount to the overall portfolio net worth.
        account_totals[account_name]["value"] += cash
        total_value += cash

        # Iterate over all individual asset holdings (shares) listed under this account.
        # Calculates market valuations for each ticker based on shares held.
        for position in account.get("positions", []):
            # symbol: The ticker identifier of the asset (e.g. 'NIFTYBEES.NS').
            symbol = position.get("symbol")
            # quantity: Number of shares owned (can be a fractional float).
            quantity = float(position.get("quantity", 0))
            # instrument: The model object mapping metadata for this financial asset.
            instrument = position.get("instrument", {})
            
            # Fetch current share price from database fields.
            # Defaults to 1.0 if database query returned no pricing context.
            current_price = instrument.get("current_price")
            if current_price is None or current_price == "":
                price = 1.0  
                logger.warning(f"Charter: No price quote for {symbol}, defaulting to 1.0")
            else:
                price = float(current_price)
                
            # value: Compute the total monetary value of this position (shares * price per share).
            value = quantity * price

            # Add this position value to the combined global value map for this ticker symbol.
            # This handles cases where the same asset is owned across multiple different accounts.
            position_values[symbol] = position_values.get(symbol, 0.0) + value
            
            # Add this position value to this specific account's total assets value.
            account_totals[account_name]["value"] += value
            
            # Append this position context metadata dictionary to this account's details.
            account_totals[account_name]["positions"].append(
                {"symbol": symbol, "value": value, "instrument": instrument}
            )
            
            # Credit this position value to the overall global portfolio valuation.
            total_value += value

    # Append overall summary statements to the results list
    result.append("Portfolio Analysis:")
    result.append(f"Total Value: ₹{total_value:,.2f}")
    result.append(f"Number of Accounts: {len(account_totals)}")
    result.append(f"Number of Positions: {len(position_values)}")
 
    # Compile the account percentage distribution statements
    result.append("\nAccount Breakdown:")
    for name, data in account_totals.items():
        # pct: Compute weight of this account relative to the total portfolio value.
        pct = (data["value"] / total_value * 100) if total_value > 0 else 0
        result.append(f"  {name} ({data['type']}): ₹{data['value']:,.2f} ({pct:.1f}%)")
 
    # Compile top holdings breakdown by monetary value (sorted descending)
    result.append("\nTop Holdings by Value:")
    sorted_positions = sorted(position_values.items(), key=lambda x: x[1], reverse=True)[:10]
    for symbol, value in sorted_positions:
        # pct: Compute weight of this holding relative to the total portfolio value.
        pct = (value / total_value * 100) if total_value > 0 else 0
        result.append(f"  {symbol}: ₹{value:,.2f} ({pct:.1f}%)")

    # ----------------------------------------------------
    # Category Allocation Aggregation
    # ----------------------------------------------------
    # Calculate combined values of asset classes, sectors, and geographic regions.
    # For example, if ETF 'NIFTYBEES' is 35% financials, we allocate 35% of its value to financials.
    result.append("\nCalculated Allocations:")
    
    asset_classes = {}
    regions = {}
    sectors = {}
    
    # Loop through accounts and positions again to distribute fractional values to categories.
    for account in portfolio_data.get("accounts", []):
        for position in account.get("positions", []):
            symbol = position.get("symbol")
            quantity = float(position.get("quantity", 0))
            instrument = position.get("instrument", {})
            
            current_price = instrument.get("current_price")
            if current_price is None or current_price == "":
                price = 1.0
            else:
                price = float(current_price)
            value = quantity * price
            
            # Aggregate asset classes distributions (e.g. equity, fixed income, commodities).
            # We multiply position value by allocation percentage and add to category total.
            for asset_class, pct in instrument.get("allocation_asset_class", {}).items():
                asset_value = value * (pct / 100)
                asset_classes[asset_class] = asset_classes.get(asset_class, 0.0) + asset_value
            
            # Aggregate geographic region distributions (e.g. north america, asia).
            for region, pct in instrument.get("allocation_regions", {}).items():
                region_value = value * (pct / 100)
                regions[region] = regions.get(region, 0.0) + region_value
            
            # Aggregate industry sector distributions (e.g. technology, financials, energy).
            for sector, pct in instrument.get("allocation_sectors", {}).items():
                sector_value = value * (pct / 100)
                sectors[sector] = sectors.get(sector, 0.0) + sector_value
    
    # Calculate sum of uninvested cash balances across all accounts.
    # Add this combined cash value directly to the asset classes allocation dictionary under 'cash'.
    total_cash = sum(
        float(acc.get("cash_balance")) if acc.get("cash_balance") is not None else 0.0
        for acc in portfolio_data.get("accounts", [])
    )
    if total_cash > 0:
        asset_classes["cash"] = asset_classes.get("cash", 0.0) + total_cash
    
    # Append asset classes breakdown to results
    result.append("\nAsset Classes:")
    for asset_class, value in sorted(asset_classes.items(), key=lambda x: x[1], reverse=True):
        result.append(f"  {asset_class}: ₹{value:,.2f}")
    
    # Append geographic regions breakdown to results
    result.append("\nGeographic Regions:")
    for region, value in sorted(regions.items(), key=lambda x: x[1], reverse=True):
        result.append(f"  {region}: ₹{value:,.2f}")
    
    # Append industry sectors breakdown to results (limited to top 10)
    result.append("\nSectors:")
    for sector, value in sorted(sectors.items(), key=lambda x: x[1], reverse=True)[:10]:
        result.append(f"  {sector}: ₹{value:,.2f}")

    # Return the assembled textual results report
    return "\n".join(result)


def create_agent(job_id: str, portfolio_data: Dict[str, Any], db=None):
    """
    Constructs model configuration, system instructions, and task prompts for the Charter.
    No downstream tools are exposed to this agent.

    Args:
        job_id: Analysis job UUID
        portfolio_data: Map of user accounts, positions, and current pricing data
        db: Database instance wrapper

    Returns:
        Tuple containing model identifier and task prompt string
    """
    # model_id: Retrieve model name from environment or default to moonshot kimi
    model_id = os.getenv("BEDROCK_MODEL_ID", "moonshotai.kimi-k2.5")
    # bedrock_region: Retrieve AWS region hosting Bedrock models
    bedrock_region = os.getenv("BEDROCK_REGION", "us-west-2")
    # Set AWS region environment variable needed for LiteLLM Bedrock connector
    os.environ["AWS_REGION_NAME"] = bedrock_region
    
    logger.info(f"Charter: Constructing agent (model: {model_id}, region: {bedrock_region})")
    
    model = f"bedrock/{model_id}"
    
    # Analyze raw values to provide text metrics summary
    portfolio_analysis = analyze_portfolio(portfolio_data)
    
    # Create system task prompt using templates creation functions
    task = create_charter_task(portfolio_analysis, portfolio_data)
    
    return model, task