"""
FinAI Instrument Tagger Agent - Asset Allocation Classification
Uses LiteLLM to query Bedrock models and return structured asset distributions
for equity tickers without existing allocations.
Validates the sum of generated allocations (must sum to exactly 100% per category).
"""

import os
import asyncio
import logging
from typing import List
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator, ConfigDict
import litellm
from dotenv import load_dotenv

# Load local environment variables if available
load_dotenv(override=True)

logger = logging.getLogger(__name__)

# Fetch Bedrock configuration variables
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "moonshotai.kimi-k2.5")
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-west-2")

from src.schemas import InstrumentCreate
from templates import TAGGER_INSTRUCTIONS, CLASSIFICATION_PROMPT


# ----------------------------------------------------
# Pydantic Target Output Models
# ----------------------------------------------------
# These Pydantic models define the structured schema that LiteLLM/Bedrock
# will populate. Extraneous parameters are forbidden.

class AllocationBreakdown(BaseModel):
    """Structured asset class percentage splits for the tagger agent output."""
    model_config = ConfigDict(extra="forbid")

    equity: float = Field(default=0.0, ge=0, le=100, description="Equity class percentage")
    fixed_income: float = Field(default=0.0, ge=0, le=100, description="Fixed income class percentage")
    real_estate: float = Field(default=0.0, ge=0, le=100, description="Real estate class percentage")
    commodities: float = Field(default=0.0, ge=0, le=100, description="Commodities class percentage")
    cash: float = Field(default=0.0, ge=0, le=100, description="Cash holdings percentage")
    alternatives: float = Field(default=0.0, ge=0, le=100, description="Alternative investments percentage")


class RegionAllocation(BaseModel):
    """Structured geographic region percentage splits for the tagger agent output."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    north_america: float = Field(default=0.0, ge=0, le=100)
    europe: float = Field(default=0.0, ge=0, le=100)
    asia: float = Field(default=0.0, ge=0, le=100)
    latin_america: float = Field(default=0.0, ge=0, le=100)
    africa: float = Field(default=0.0, ge=0, le=100)
    middle_east: float = Field(default=0.0, ge=0, le=100)
    oceania: float = Field(default=0.0, ge=0, le=100)
    global_: float = Field(default=0.0, ge=0, le=100, alias="global", description="Global/diversified index percentage")
    international: float = Field(default=0.0, ge=0, le=100, description="Developed international ex-US markets percentage")


class SectorAllocation(BaseModel):
    """Structured industry sector percentage splits for the tagger agent output."""
    model_config = ConfigDict(extra="forbid")

    technology: float = Field(default=0.0, ge=0, le=100)
    healthcare: float = Field(default=0.0, ge=0, le=100)
    financials: float = Field(default=0.0, ge=0, le=100)
    consumer_discretionary: float = Field(default=0.0, ge=0, le=100)
    consumer_staples: float = Field(default=0.0, ge=0, le=100)
    industrials: float = Field(default=0.0, ge=0, le=100)
    materials: float = Field(default=0.0, ge=0, le=100)
    energy: float = Field(default=0.0, ge=0, le=100)
    utilities: float = Field(default=0.0, ge=0, le=100)
    real_estate: float = Field(default=0.0, ge=0, le=100)
    communication: float = Field(default=0.0, ge=0, le=100)
    treasury: float = Field(default=0.0, ge=0, le=100, description="Government treasury bonds percentage")
    corporate: float = Field(default=0.0, ge=0, le=100, description="Corporate credit debt percentage")
    mortgage: float = Field(default=0.0, ge=0, le=100, description="Mortgage backed securities percentage")
    government_related: float = Field(default=0.0, ge=0, le=100, description="Government agency debt percentage")
    commodities: float = Field(default=0.0, ge=0, le=100)
    diversified: float = Field(default=0.0, ge=0, le=100, description="Diversified sectors mix percentage")
    other: float = Field(default=0.0, ge=0, le=100)


class InstrumentClassification(BaseModel):
    """
    Combined structured representation of an instrument's metadata.
    Enforces that allocations add up to exactly 100% per validation segment.
    """
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(description="Ticker symbol of the instrument")
    name: str = Field(description="Name of the instrument")
    instrument_type: str = Field(description="Type identifier (etf, stock, mutual_fund, bond_fund, etc.)")
    current_price: float = Field(description="Current price per share in USD", gt=0)

    allocation_asset_class: AllocationBreakdown = Field(description="Asset class breakdown")
    allocation_regions: RegionAllocation = Field(description="Regional breakdown")
    allocation_sectors: SectorAllocation = Field(description="Sector breakdown")

    @field_validator("allocation_asset_class")
    def validate_asset_class_sum(cls, v: AllocationBreakdown):
        total = v.equity + v.fixed_income + v.real_estate + v.commodities + v.cash + v.alternatives
        if abs(total - 100.0) > 3.0:
            raise ValueError(f"Asset class allocations must sum to 100.0, got {total}")
        return v

    @field_validator("allocation_regions")
    def validate_regions_sum(cls, v: RegionAllocation):
        total = (
            v.north_america
            + v.europe
            + v.asia
            + v.latin_america
            + v.africa
            + v.middle_east
            + v.oceania
            + v.global_
            + v.international
        )
        if abs(total - 100.0) > 3.0:
            raise ValueError(f"Regional allocations must sum to 100.0, got {total}")
        return v

    @field_validator("allocation_sectors")
    def validate_sectors_sum(cls, v: SectorAllocation):
        total = (
            v.technology
            + v.healthcare
            + v.financials
            + v.consumer_discretionary
            + v.consumer_staples
            + v.industrials
            + v.materials
            + v.energy
            + v.utilities
            + v.real_estate
            + v.communication
            + v.treasury
            + v.corporate
            + v.mortgage
            + v.government_related
            + v.commodities
            + v.diversified
            + v.other
        )
        if abs(total - 100.0) > 3.0:
            raise ValueError(f"Sector allocations must sum to 100.0, got {total}")
        return v


# ----------------------------------------------------
# Main Classification Logic
# ----------------------------------------------------

async def classify_instrument(
    symbol: str, name: str, instrument_type: str = "etf"
) -> InstrumentClassification:
    """
    Directly query Bedrock model via LiteLLM to analyze and tag a ticker.
    Enforces structured JSON output matching the InstrumentClassification Pydantic model.
    """
    try:
        model_id = BEDROCK_MODEL_ID
        os.environ["AWS_REGION_NAME"] = BEDROCK_REGION

        # Construct classification prompts
        task = CLASSIFICATION_PROMPT.format(
            symbol=symbol, name=name, instrument_type=instrument_type
        )

        # Call the Bedrock model using structured outputs
        response = await litellm.acompletion(
            model=f"bedrock/{model_id}",
            messages=[
                {"role": "system", "content": TAGGER_INSTRUCTIONS},
                {"role": "user", "content": task}
            ],
            response_format=InstrumentClassification,
            metadata={
                "trace_name": f"Instrument Tagger ({symbol})"
            }
        )

        content = response.choices[0].message.content
        if isinstance(content, str):
            return InstrumentClassification.model_validate_json(content)
        return content

    except Exception as e:
        logger.error(f"Error classifying symbol {symbol}: {e}")
        raise


async def tag_instruments(instruments: List[dict]) -> List[InstrumentClassification]:
    """
    Processes multiple instruments sequentially.
    Applies short delays between executions to maintain resilient API concurrency limits.
    """
    results = []
    for i, instrument in enumerate(instruments):
        # Prevent API throttling on batch executions
        if i > 0:
            await asyncio.sleep(0.5)

        symbol = instrument["symbol"]
        try:
            # Query instrument tag details directly
            classification = await classify_instrument(
                symbol=symbol,
                name=instrument.get("name", ""),
                instrument_type=instrument.get("instrument_type", "etf"),
            )
            logger.info(f"Successfully classified holding {symbol}")
            results.append(classification)
        except Exception as e:
            logger.error(f"Failed to classify holding {symbol}: {e}")
            results.append(None)

    # Filter out empty outcomes
    return [r for r in results if r is not None]


def classification_to_db_format(classification: InstrumentClassification) -> InstrumentCreate:
    """
    Reformats structured classification object into database-ready schema format,
    pruning entries where the allocated weights are zero.
    """
    asset_class_dict = {
        "equity": classification.allocation_asset_class.equity,
        "fixed_income": classification.allocation_asset_class.fixed_income,
        "real_estate": classification.allocation_asset_class.real_estate,
        "commodities": classification.allocation_asset_class.commodities,
        "cash": classification.allocation_asset_class.cash,
        "alternatives": classification.allocation_asset_class.alternatives,
    }
    # Keep non-zero allocations
    asset_class_dict = {k: v for k, v in asset_class_dict.items() if v > 0}

    regions_dict = {
        "north_america": classification.allocation_regions.north_america,
        "europe": classification.allocation_regions.europe,
        "asia": classification.allocation_regions.asia,
        "latin_america": classification.allocation_regions.latin_america,
        "africa": classification.allocation_regions.africa,
        "middle_east": classification.allocation_regions.middle_east,
        "oceania": classification.allocation_regions.oceania,
        "global": classification.allocation_regions.global_,
        "international": classification.allocation_regions.international,
    }
    regions_dict = {k: v for k, v in regions_dict.items() if v > 0}

    sectors_dict = {
        "technology": classification.allocation_sectors.technology,
        "healthcare": classification.allocation_sectors.healthcare,
        "financials": classification.allocation_sectors.financials,
        "consumer_discretionary": classification.allocation_sectors.consumer_discretionary,
        "consumer_staples": classification.allocation_sectors.consumer_staples,
        "industrials": classification.allocation_sectors.industrials,
        "materials": classification.allocation_sectors.materials,
        "energy": classification.allocation_sectors.energy,
        "utilities": classification.allocation_sectors.utilities,
        "real_estate": classification.allocation_sectors.real_estate,
        "communication": classification.allocation_sectors.communication,
        "treasury": classification.allocation_sectors.treasury,
        "corporate": classification.allocation_sectors.corporate,
        "mortgage": classification.allocation_sectors.mortgage,
        "government_related": classification.allocation_sectors.government_related,
        "commodities": classification.allocation_sectors.commodities,
        "diversified": classification.allocation_sectors.diversified,
        "other": classification.allocation_sectors.other,
    }
    sectors_dict = {k: v for k, v in sectors_dict.items() if v > 0}

    return InstrumentCreate(
        symbol=classification.symbol,
        name=classification.name,
        instrument_type=classification.instrument_type,
        current_price=Decimal(str(classification.current_price)),
        allocation_asset_class=asset_class_dict,
        allocation_regions=regions_dict,
        allocation_sectors=sectors_dict,
    )
