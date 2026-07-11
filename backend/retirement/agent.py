"""
FinAI Retirement Specialist Agent - Planning Calculations & Task Prompting
Performs financial projections (asset class distribution, Monte Carlo simulation,
savings timeline milestones) and prompts the Retirement Specialist agent.
"""

import os
import json
import logging
import random
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger()


def calculate_portfolio_value(portfolio_data: Dict[str, Any]) -> float:
    """
    Sum the values of all holdings and cash accounts.
    
    How does it work?
    1. It iterates over accounts and adds uninvested cash.
    2. For each position, it multiplies quantity by the instrument current price (defaulting to 100.0 if missing).
    """
    total_value = 0.0

    for account in portfolio_data.get("accounts", []):
        cash = float(account.get("cash_balance", 0.0))
        total_value += cash

        for position in account.get("positions", []):
            quantity = float(position.get("quantity", 0.0))
            instrument = position.get("instrument", {})
            price = float(instrument.get("current_price", 100.0))
            total_value += quantity * price

    return total_value


def calculate_asset_allocation(portfolio_data: Dict[str, Any]) -> Dict[str, float]:
    """
    Calculate the aggregate portfolio weights per asset class.
    
    Why do we need this?
    To run a Monte Carlo simulation, we need to know the overall allocation of the portfolio
    (e.g., 60% equities, 30% bonds, 10% cash). Since mutual funds or ETFs have split allocations,
    we look up the asset class percentages inside each instrument and multiply it by the monetary
    value of the holding.

    Returns:
        Dictionary mapping asset class string identifiers to decimal weights (e.g. {"equity": 0.7})
    """
    total_equity = 0.0
    total_bonds = 0.0
    total_real_estate = 0.0
    total_commodities = 0.0
    total_cash = 0.0
    total_value = 0.0

    for account in portfolio_data.get("accounts", []):
        # Accumulate cash values
        cash = float(account.get("cash_balance", 0.0))
        total_cash += cash
        total_value += cash

        for position in account.get("positions", []):
            quantity = float(position.get("quantity", 0.0))
            instrument = position.get("instrument", {})
            price = float(instrument.get("current_price", 100.0))
            value = quantity * price
            total_value += value

            # Extract asset class allocation percentages (e.g., {"equity": 90.0, "fixed_income": 10.0})
            asset_allocation = instrument.get("allocation_asset_class", {})
            if asset_allocation:
                # Add fractional monetary value to each asset class bucket
                total_equity += value * asset_allocation.get("equity", 0.0) / 100.0
                total_bonds += value * asset_allocation.get("fixed_income", 0.0) / 100.0
                total_real_estate += value * asset_allocation.get("real_estate", 0.0) / 100.0
                total_commodities += value * asset_allocation.get("commodities", 0.0) / 100.0

    if total_value == 0:
        return {"equity": 0.0, "bonds": 0.0, "real_estate": 0.0, "commodities": 0.0, "cash": 0.0}

    # Normalize weights relative to total portfolio value (cash + assets)
    return {
        "equity": total_equity / total_value,
        "bonds": total_bonds / total_value,
        "real_estate": total_real_estate / total_value,
        "commodities": total_commodities / total_value,
        "cash": total_cash / total_value,
    }


def run_monte_carlo_simulation(
    current_value: float,
    years_until_retirement: int,
    target_annual_income: float,
    asset_allocation: Dict[str, float],
    num_simulations: int = 500,
) -> Dict[str, Any]:
    """
    Runs a Monte Carlo simulation tracking wealth projections.
    
    What is a Monte Carlo simulation?
    Instead of assuming a constant 8% market return every year, the simulation runs the portfolio's growth
    hundreds of times (num_simulations=500) using random returns sampled from standard distributions.
    This simulates real market volatility and sequence of returns risk.

    How does it work?
    1. Sets historical mean and standard deviations (volatilities) for Equities, Bonds, Real Estate, and Commodities.
    2. Runs 500 scenarios. Each scenario has two stages:
       - Phase 1: Accumulation (pre-retirement). Every year, we sample random returns for each asset class using
         a Gaussian distribution (random.gauss), multiply by allocation weights, add returns, and add a savings
         contribution of ₹1 Lakh.
       - Phase 2: Retirement (post-retirement). We sample returns every year, and subtract an annual withdrawal
         (initially target_annual_income, increased by 6% inflation every year).
       - We count how many years the portfolio lasted (out of 30 years). If it survived all 30 years, it is a success.
    3. We sort the final values to calculate percentiles (10th percentile = worst case, median = average case,
       90th percentile = best case) and the overall success rate (percentage of successful scenarios).
    """
    # Define historical mean/std returns parameters for the Indian market context
    equity_return_mean = 0.12     # 12% average annual equity return
    equity_return_std = 0.18      # 18% volatility standard deviation
    bond_return_mean = 0.07       # 7% average bond return
    bond_return_std = 0.05        # 5% bond volatility
    real_estate_return_mean = 0.08
    real_estate_return_std = 0.12
    commodities_return_mean = 0.08
    commodities_return_std = 0.12

    successful_scenarios = 0
    final_values = []
    years_lasted = []

    # Run simulations
    for _ in range(num_simulations):
        portfolio_value = current_value

        # Stage 1: Accumulation Phase (Pre-retirement growth)
        for _ in range(years_until_retirement):
            # Model returns using Gaussian distributions (bell curves) centered at the mean return
            equity_return = random.gauss(equity_return_mean, equity_return_std)
            bond_return = random.gauss(bond_return_mean, bond_return_std)
            real_estate_return = random.gauss(real_estate_return_mean, real_estate_return_std)
            commodities_return = random.gauss(commodities_return_mean, commodities_return_std)

            # Calculate weighted return of the portfolio based on asset allocation
            portfolio_return = (
                asset_allocation["equity"] * equity_return
                + asset_allocation["bonds"] * bond_return
                + asset_allocation["real_estate"] * real_estate_return
                + asset_allocation.get("commodities", 0.0) * commodities_return
                + asset_allocation["cash"] * 0.02  # Cash returns assumed flat 2%
            )

            # Apply return yield and add annual savings contribution (assumed flat ₹1 Lakh)
            portfolio_value = portfolio_value * (1 + portfolio_return)
            portfolio_value += 100000.0

        # Stage 2: Retirement Phase (models 30 years of withdrawals)
        retirement_years = 30
        annual_withdrawal = target_annual_income
        years_income_lasted = 0

        for year in range(retirement_years):
            if portfolio_value <= 0:
                break

            # Adjust withdrawal rate for inflation (assumed 6% average inflation rate in India)
            # This means the user withdraws 6% more cash every year to maintain purchase power
            annual_withdrawal *= 1.06

            # Sample returns during the retirement phase
            equity_return = random.gauss(equity_return_mean, equity_return_std)
            bond_return = random.gauss(bond_return_mean, bond_return_std)
            real_estate_return = random.gauss(real_estate_return_mean, real_estate_return_std)
            commodities_return = random.gauss(commodities_return_mean, commodities_return_std)

            portfolio_return = (
                asset_allocation["equity"] * equity_return
                + asset_allocation["bonds"] * bond_return
                + asset_allocation["real_estate"] * real_estate_return
                + asset_allocation.get("commodities", 0.0) * commodities_return
                + asset_allocation["cash"] * 0.02
            )

            # Apply market returns and subtract the annual withdrawal
            portfolio_value = portfolio_value * (1 + portfolio_return) - annual_withdrawal

            if portfolio_value > 0:
                years_income_lasted += 1

        # Track results for this scenario
        final_values.append(max(0.0, portfolio_value))
        years_lasted.append(years_income_lasted)

        # A scenario is a success if the portfolio survived the full retirement window
        if years_income_lasted >= retirement_years:
            successful_scenarios += 1

    # Extract percentile statistics by sorting the final values
    final_values.sort()
    success_rate = (successful_scenarios / num_simulations) * 100

    # Calculate expected portfolio valuation at retirement using flat expected returns (deterministic baseline)
    expected_return = (
        asset_allocation["equity"] * equity_return_mean
        + asset_allocation["bonds"] * bond_return_mean
        + asset_allocation["real_estate"] * real_estate_return_mean
        + asset_allocation.get("commodities", 0.0) * commodities_return_mean
        + asset_allocation["cash"] * 0.02
    )
    expected_value_at_retirement = current_value
    for _ in range(years_until_retirement):
        expected_value_at_retirement *= 1 + expected_return
        expected_value_at_retirement += 100000.0  # ₹1 Lakh annual savings contribution

    return {
        "success_rate": round(success_rate, 1),
        "median_final_value": round(final_values[num_simulations // 2], 2),
        "percentile_10": round(final_values[num_simulations // 10], 2),  # 10th percentile (worst case)
        "percentile_90": round(final_values[9 * num_simulations // 10], 2),  # 90th percentile (best case)
        "average_years_lasted": round(sum(years_lasted) / len(years_lasted), 1),
        "expected_value_at_retirement": round(expected_value_at_retirement, 2),
    }


def generate_projections(
    current_value: float,
    years_until_retirement: int,
    asset_allocation: Dict[str, float],
    current_age: int,
) -> list:
    """
    Generates deterministic growth projections at 5-year milestones.
    This creates the data points for the retirement readiness timeline.
    
    Expected returns are set slightly lower than Monte Carlo averages to simulate conservative returns:
    - Equity: 7%, Bonds: 4%, Real Estate: 6%, Cash: 2%.
    """
    expected_return = (
        asset_allocation["equity"] * 0.07
        + asset_allocation["bonds"] * 0.04
        + asset_allocation["real_estate"] * 0.06
        + asset_allocation.get("commodities", 0.0) * 0.05
        + asset_allocation["cash"] * 0.02
    )

    projections = []
    portfolio_value = current_value
    # Generate data points every 5 years up to retirement + 30 years in retirement
    milestone_years = list(range(0, years_until_retirement + 31, 5))

    for year in milestone_years:
        age = current_age + year

        if year <= years_until_retirement:
            # Accumulation phase milestones
            for _ in range(min(5, year)):
                portfolio_value *= 1 + expected_return
                portfolio_value += 100000.0  # ₹1 Lakh annual contribution
            phase = "accumulation"
            annual_income = 0.0
        else:
            # Retirement phase milestones (assumes initial 4.5% withdrawal rate rule)
            withdrawal_rate = 0.045
            annual_income = portfolio_value * withdrawal_rate
            years_in_retirement = min(5, year - years_until_retirement)
            for _ in range(years_in_retirement):
                portfolio_value = portfolio_value * (1 + expected_return) - annual_income
            phase = "retirement"

        if portfolio_value > 0:
            projections.append(
                {
                    "year": year,
                    "age": age,
                    "portfolio_value": round(portfolio_value, 2),
                    "annual_income": round(annual_income, 2),
                    "phase": phase,
                }
            )

    return projections


def create_agent(
    job_id: str, portfolio_data: Dict[str, Any], user_preferences: Dict[str, Any], db=None
):
    """
    Gathers model configuration, compiles simulation outputs,
    and returns task strings formatted for the retirement specialist.
    """
    model_id = os.getenv("BEDROCK_MODEL_ID", "moonshotai.kimi-k2.5")
    bedrock_region = os.getenv("BEDROCK_REGION", "us-west-2")
    os.environ["AWS_REGION_NAME"] = bedrock_region

    model = f"bedrock/{model_id}"

    years_until_retirement = user_preferences.get("years_until_retirement", 30)
    target_income = user_preferences.get("target_retirement_income", 800000.0)
    current_age = user_preferences.get("current_age", 40)

    # Compute key figures (portfolio total and normalized class allocation ratios)
    portfolio_value = calculate_portfolio_value(portfolio_data)
    allocation = calculate_asset_allocation(portfolio_data)

    # Execute simulation (500 scenarios)
    monte_carlo = run_monte_carlo_simulation(
        portfolio_value, years_until_retirement, target_income, allocation, num_simulations=500
    )

    # Generate milestones list
    projections = generate_projections(
        portfolio_value, years_until_retirement, allocation, current_age
    )

    tools = []

    # Compile the final task prompt containing simulation findings.
    # This task prompt is loaded with the Monte Carlo results and Safe Withdrawal rate calculations,
    # and instructs the Retirement Specialist agent to draft the final narrative assessment.
    task = f"""
# Portfolio Analysis Context

## Current Situation
- Portfolio Value: ₹{portfolio_value:,.0f}
- Asset Allocation: {", ".join([f"{k.title()}: {v:.0%}" for k, v in allocation.items() if v > 0])}
- Years to Retirement: {years_until_retirement}
- Target Annual Income: ₹{target_income:,.0f}
- Current Age: {current_age}

## Monte Carlo Simulation Results (500 scenarios)
- Success Rate: {monte_carlo["success_rate"]}% (probability of sustaining retirement income for 30 years)
- Expected Portfolio Value at Retirement: ₹{monte_carlo["expected_value_at_retirement"]:,.0f}
- 10th Percentile Outcome: ₹{monte_carlo["percentile_10"]:,.0f} (worst case)
- Median Final Value: ₹{monte_carlo["median_final_value"]:,.0f}
- 90th Percentile Outcome: ₹{monte_carlo["percentile_90"]:,.0f} (best case)
- Average Years Portfolio Lasts: {monte_carlo["average_years_lasted"]} years

## Key Projections (Milestones)
"""

    for proj in projections[:6]:
        if proj["phase"] == "accumulation":
            task += f"- Age {proj['age']}: ₹{proj['portfolio_value']:,.0f} (building wealth)\n"
        else:
            task += f"- Age {proj['age']}: ₹{proj['portfolio_value']:,.0f} (annual income: ₹{proj['annual_income']:,.0f})\n"

    task += f"""

## Risk Factors to Consider
- Sequence of returns risk (poor returns early in retirement)
- Inflation impact (6% assumed)
- Healthcare costs in retirement
- Longevity risk (living beyond 30 years)
- Market volatility (equity standard deviation: 18%)

## Safe Withdrawal Rate Analysis
- 4.5% Rule: ₹{portfolio_value * 0.045:,.0f} initial annual income
- Target Income: ₹{target_income:,.0f}
- Gap: ₹{target_income - (portfolio_value * 0.045):,.0f}

Your task: Analyze this retirement readiness data and provide a comprehensive retirement analysis including:
1. Clear assessment of retirement readiness
2. Specific recommendations to improve success rate
3. Risk mitigation strategies
4. Action items with timeline

Provide your analysis in clear markdown format with specific numbers and actionable recommendations.
"""

    return model, tools, task

