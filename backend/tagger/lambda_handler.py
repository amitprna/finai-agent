"""
FinAI Tagger - Asset Tagger Agent Lambda Handler
Loads missing instruments payload, runs them through the classifier model,
and registers/inserts the resulting allocation structures into the database.
"""

import os
import json
import asyncio
import logging
from typing import List, Dict, Any

from src import Database
from src.schemas import InstrumentCreate
from agent import tag_instruments, classification_to_db_format
from observability import observe

# Setup basic root level logger configuration
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize database wrapper
db = Database()


async def process_instruments(instruments: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Process, classify, and write tagging classifications to the database.
    
    Args:
        instruments: List of dicts representing raw holdings to tag: [{"symbol": "VTI", "name": "Vanguard..."}]
        
    Returns:
        Structured dictionary outlining tag statistics and database modifications
    """
    logger.info(f"Tagger: Triggering classification for {len(instruments)} holdings")
    classifications = await tag_instruments(instruments)
    
    updated = []
    errors = []
    
    # Process each successfully tagged outcome
    for classification in classifications:
        try:
            # Reformat to Pydantic database models
            db_instrument = classification_to_db_format(classification)
            
            # Check if instrument is already cataloged in database
            existing = db.instruments.find_by_symbol(classification.symbol)
            
            if existing:
                # Update existing record
                update_data = db_instrument.model_dump()
                # Remove symbol as it acts as primary lookup key
                del update_data['symbol']
                
                rows = db.client.update(
                    'instruments',
                    update_data,
                    "symbol = :symbol",
                    {'symbol': classification.symbol}
                )
                logger.info(f"Tagger: Updated {classification.symbol} inside database ({rows} rows modified)")
            else:
                # Create and insert new record
                db.instruments.create_instrument(db_instrument)
                logger.info(f"Tagger: Created and cataloged {classification.symbol} inside database")
            
            updated.append(classification.symbol)
            
        except Exception as e:
            logger.error(f"Tagger: Failed to register holding {classification.symbol} in DB: {e}")
            errors.append({
                'symbol': classification.symbol,
                'error': str(e)
            })
    
    # Prepare handler response statistics payload
    return {
        'tagged': len(classifications),
        'updated': updated,
        'errors': errors,
        'classifications': [
            {
                'symbol': c.symbol,
                'name': c.name,
                'type': c.instrument_type,
                'current_price': c.current_price,
                'asset_class': c.allocation_asset_class.model_dump(),
                'regions': c.allocation_regions.model_dump(),
                'sectors': c.allocation_sectors.model_dump()
            }
            for c in classifications
        ]
    }


def lambda_handler(event, context):
    """
    AWS Lambda entry point.
    Receives request payloads, starts the async processing runner, and returns execution status.
    """
    with observe():
        try:
            instruments = event.get('instruments', [])

            if not instruments:
                return {
                    'statusCode': 400,
                    'body': json.dumps({'error': 'No instruments list provided in event payload'})
                }

            # Start event loop processing
            result = asyncio.run(process_instruments(instruments))

            return {
                'statusCode': 200,
                'body': json.dumps(result)
            }

        except Exception as e:
            logger.error(f"Tagger: Lambda execution error: {e}")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': str(e)})
            }