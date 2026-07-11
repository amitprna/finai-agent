#!/usr/bin/env python3
"""
Seed Data Loader
Populates the database with initial financial instruments (popular Indian ETFs and stocks)
containing sector, geographic, and asset class allocation data.
All allocations sum up to exactly 100% per category as verified by Pydantic validators.
"""

import os
import json
import boto3
from botocore.exceptions import ClientError
from src.schemas import InstrumentCreate
from pydantic import ValidationError
from dotenv import load_dotenv

# Load local environment variables if available
load_dotenv(override=True)

# Fetch configuration variables
cluster_arn = os.environ.get("AURORA_CLUSTER_ARN")
secret_arn = os.environ.get("AURORA_SECRET_ARN")
database = os.environ.get("AURORA_DATABASE", "finai")
region = os.environ.get("DEFAULT_AWS_REGION", "us-east-1")

if not cluster_arn or not secret_arn:
    print("[ERROR] Missing AURORA_CLUSTER_ARN or AURORA_SECRET_ARN in env configuration")
    exit(1)

# Initialize AWS RDS Data API client
client = boto3.client("rds-data", region_name=region)

# Initial list of financial instruments mapping sectors, regions, and asset classes.
# These values will be parsed and validated using Pydantic before database entry.
INSTRUMENTS = [
    # Nippon India ETF Nifty 50 BeES (Large Cap Index)
    {
        "symbol": "NIFTYBEES.NS",
        "name": "Nippon India ETF Nifty 50 BeES",
        "instrument_type": "etf",
        "current_price": 260.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {
            "financials": 35,
            "technology": 15,
            "energy": 12,
            "consumer_staples": 10,
            "consumer_discretionary": 8,
            "industrials": 8,
            "materials": 6,
            "healthcare": 6,
        },
        "allocation_asset_class": {"equity": 100},
    },
    # Nippon India ETF Nifty Next 50 BeES (Mid Cap Index)
    {
        "symbol": "JUNIORBEES.NS",
        "name": "Nippon India ETF Nifty Next 50 BeES",
        "instrument_type": "etf",
        "current_price": 650.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {
            "financials": 25,
            "materials": 15,
            "consumer_staples": 12,
            "consumer_discretionary": 12,
            "industrials": 12,
            "healthcare": 10,
            "energy": 8,
            "technology": 6,
        },
        "allocation_asset_class": {"equity": 100},
    },
    # Nippon India ETF Nifty Bank BeES (Sectoral Financials)
    {
        "symbol": "BANKBEES.NS",
        "name": "Nippon India ETF Nifty Bank BeES",
        "instrument_type": "etf",
        "current_price": 520.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"financials": 100},
        "allocation_asset_class": {"equity": 100},
    },
    # Nippon India ETF Nifty IT BeES (Sectoral Tech)
    {
        "symbol": "ITBEES.NS",
        "name": "Nippon India ETF Nifty IT BeES",
        "instrument_type": "etf",
        "current_price": 410.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"technology": 100},
        "allocation_asset_class": {"equity": 100},
    },
    # Nippon India ETF Liquid BeES (Debt / Cash Equivalent)
    {
        "symbol": "LIQUIDBEES.NS",
        "name": "Nippon India ETF Liquid BeES",
        "instrument_type": "bond_fund",
        "current_price": 1000.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"other": 100},
        "allocation_asset_class": {"cash": 100},
    },
    # Nippon India ETF Nifty 5 yr Benchmark G-Sec (Government Bonds)
    {
        "symbol": "GILT5YBEES.NS",
        "name": "Nippon India ETF Nifty 5 yr Benchmark G-Sec",
        "instrument_type": "bond_fund",
        "current_price": 118.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"government_related": 100},
        "allocation_asset_class": {"fixed_income": 100},
    },
    # Nippon India ETF Gold BeES (Commodity - Gold)
    {
        "symbol": "GOLDBEES.NS",
        "name": "Nippon India ETF Gold BeES",
        "instrument_type": "etf",
        "current_price": 62.00,
        "allocation_regions": {"global": 100},
        "allocation_sectors": {"commodities": 100},
        "allocation_asset_class": {"commodities": 100},
    },
    # Nippon India ETF Silver BeES (Commodity - Silver)
    {
        "symbol": "SILVERBEES.NS",
        "name": "Nippon India ETF Silver BeES",
        "instrument_type": "etf",
        "current_price": 78.00,
        "allocation_regions": {"global": 100},
        "allocation_sectors": {"commodities": 100},
        "allocation_asset_class": {"commodities": 100},
    },
    # Reliance Industries Limited (Large Cap Energy/Retail/Telecom Stock)
    {
        "symbol": "RELIANCE.NS",
        "name": "Reliance Industries Limited",
        "instrument_type": "stock",
        "current_price": 2450.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {
            "energy": 60,
            "communication": 20,
            "consumer_discretionary": 20,
        },
        "allocation_asset_class": {"equity": 100},
    },
    # Tata Consultancy Services Limited (Large Cap Tech Stock)
    {
        "symbol": "TCS.NS",
        "name": "Tata Consultancy Services Limited",
        "instrument_type": "stock",
        "current_price": 3850.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"technology": 100},
        "allocation_asset_class": {"equity": 100},
    },
    # HDFC Bank Limited (Large Cap Financials Stock)
    {
        "symbol": "HDFCBANK.NS",
        "name": "HDFC Bank Limited",
        "instrument_type": "stock",
        "current_price": 1520.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"financials": 100},
        "allocation_asset_class": {"equity": 100},
    },
    # Infosys Limited (Large Cap Tech Stock)
    {
        "symbol": "INFY.NS",
        "name": "Infosys Limited",
        "instrument_type": "stock",
        "current_price": 1430.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"technology": 100},
        "allocation_asset_class": {"equity": 100},
    },
    # ICICI Bank Limited (Large Cap Financials Stock)
    {
        "symbol": "ICICIBANK.NS",
        "name": "ICICI Bank Limited",
        "instrument_type": "stock",
        "current_price": 1050.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"financials": 100},
        "allocation_asset_class": {"equity": 100},
    },
    # Hindustan Unilever Limited (Large Cap Consumer Goods Stock)
    {
        "symbol": "HINDUNILVR.NS",
        "name": "Hindustan Unilever Limited",
        "instrument_type": "stock",
        "current_price": 2350.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"consumer_staples": 100},
        "allocation_asset_class": {"equity": 100},
    },
    # ITC Limited (Large Cap Consumer Goods Stock)
    {
        "symbol": "ITC.NS",
        "name": "ITC Limited",
        "instrument_type": "stock",
        "current_price": 430.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"consumer_staples": 70, "other": 30},
        "allocation_asset_class": {"equity": 100},
    },
    # State Bank of India (Large Cap Financials Stock)
    {
        "symbol": "SBI.NS",
        "name": "State Bank of India",
        "instrument_type": "stock",
        "current_price": 780.00,
        "allocation_regions": {"asia": 100},
        "allocation_sectors": {"financials": 100},
        "allocation_asset_class": {"equity": 100},
    },
]


def insert_instrument(instrument_data):
    """
    Inserts a single financial instrument into the database.
    Performs validation via Pydantic and executes an UPSERT SQL statement.
    """
    try:
        # Validate data against constraints (sums must equal 100%)
        instrument = InstrumentCreate(**instrument_data)
    except ValidationError as e:
        print(f"    [ERROR] Validation error for {instrument_data.get('symbol')}: {e}")
        return False

    validated = instrument.model_dump()

    # Construct UPSERT SQL command
    sql = """
        INSERT INTO instruments (
            symbol, name, instrument_type, current_price,
            allocation_regions, allocation_sectors, allocation_asset_class
        ) VALUES (
            :symbol, :name, :instrument_type, :current_price::numeric,
            :allocation_regions::jsonb, :allocation_sectors::jsonb, :allocation_asset_class::jsonb
        )
        ON CONFLICT (symbol) DO UPDATE SET
            name = EXCLUDED.name,
            instrument_type = EXCLUDED.instrument_type,
            current_price = EXCLUDED.current_price,
            allocation_regions = EXCLUDED.allocation_regions,
            allocation_sectors = EXCLUDED.allocation_sectors,
            updated_at = NOW()
    """

    try:
        # Bind arguments matching AWS RDS Data API format spec
        client.execute_statement(
            resourceArn=cluster_arn,
            secretArn=secret_arn,
            database=database,
            sql=sql,
            parameters=[
                {"name": "symbol", "value": {"stringValue": validated["symbol"]}},
                {"name": "name", "value": {"stringValue": validated["name"]}},
                {"name": "instrument_type", "value": {"stringValue": validated["instrument_type"]}},
                {"name": "current_price", "value": {"stringValue": str(validated.get("current_price", 0))}},
                {"name": "allocation_regions", "value": {"stringValue": json.dumps(validated["allocation_regions"])}},
                {"name": "allocation_sectors", "value": {"stringValue": json.dumps(validated["allocation_sectors"])}},
                {"name": "allocation_asset_class", "value": {"stringValue": json.dumps(validated["allocation_asset_class"])}},
            ],
        )
        print(f"    [OK] Seeded {validated['symbol']}")
        return True
    except ClientError as e:
        print(f"    [ERROR] DB insertion failed for {validated['symbol']}: {e}")
        return False


if __name__ == "__main__":
    print("Loading initial seed instruments data...")
    print("=" * 50)
    success = 0
    for idx, inst in enumerate(INSTRUMENTS, 1):
        print(f"[{idx}/{len(INSTRUMENTS)}] Processing {inst['symbol']}...")
        if insert_instrument(inst):
            success += 1
    print("=" * 50)
    print(f"Seeding completed: {success}/{len(INSTRUMENTS)} instruments loaded successfully.")
