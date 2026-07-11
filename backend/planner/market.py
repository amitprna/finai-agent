"""
Market Data Integration
Scrapes and updates current market prices for user portfolio assets.
Uses Yahoo Finance ticker chart endpoints through prices.py helper methods.
"""

import logging
from typing import Set
from prices import get_share_price

# Use the default root logger to capture warning and error messages in cloud environments
logger = logging.getLogger()


def update_instrument_prices(job_id: str, db) -> None:
    """
    Look up all instruments in the user's active holdings and trigger price updates.
    
    Why is this function used?
    To analyze a portfolio, we need the most up-to-date pricing. This function:
    1. Fetches the active analysis job to identify the user.
    2. Gathers all unique stock symbols/tickers across all of that user's accounts.
    3. Triggers price scraping and updates the database.

    Args:
        job_id: The active analysis job UUID
        db: Database instance wrapper
    """
    try:
        logger.info(f"Market: Executing market price refresh for job {job_id}")

        # Fetch job record to get the target user ID
        job = db.jobs.find_by_id(job_id)
        if not job:
            logger.error(f"Market: Job {job_id} not found in database")
            return

        user_id = job['user_id']

        # Extract all unique holdings/symbols from user's active portfolios
        # We use a set (symbols) to ensure that even if a stock is held in multiple accounts,
        # we only scrape its price once.
        accounts = db.accounts.find_by_user(user_id)
        symbols = set()

        for account in accounts:
            positions = db.positions.find_by_account(account['id'])
            for position in positions:
                symbols.add(position['symbol'])

        if not symbols:
            logger.info("Market: User portfolio has no assets/symbols to update")
            return

        logger.info(f"Market: Scraping quotes for {len(symbols)} tickers: {symbols}")

        # Trigger database price table updates for these symbols
        update_prices_for_symbols(symbols, db)
        logger.info("Market: Price refresh process completed successfully")

    except Exception as e:
        logger.error(f"Market: Failed to execute instrument price refresh: {e}")
        # Continue with pipeline even if price update encounters issues (resilient fallback)
        # This prevents a temporary network scrape failure from failing the entire analysis stack.


def update_prices_for_symbols(symbols: Set[str], db) -> None:
    """
    Scrapes and updates current market pricing information inside the database for multiple symbols.
    
    How does it work?
    1. It iterates over every ticker symbol.
    2. Calls 'get_share_price' (which hits the Yahoo Finance API).
    3. Saves the successfully fetched price into a local dictionary mapping 'symbol -> price'.
    4. Performs an UPDATE query in the database's 'instruments' table to refresh the 'current_price'.
    """
    if not symbols:
        logger.info("Market: Empty symbol list, skipping refresh")
        return

    symbols_list = list(symbols)
    price_map = {}

    # Scrape the Yahoo Finance price quote for each symbol sequentially
    for symbol in symbols_list:
        try:
            # get_share_price is imported from prices.py and handles the actual HTTP requests
            price = get_share_price(symbol)
            if price > 0:
                price_map[symbol] = price
                logger.info(f"Market: Scraped quote for {symbol} = ₹{price:.2f}")
            else:
                logger.warning(f"Market: Returned invalid price quote for {symbol}")
        except Exception as e:
            logger.warning(f"Market: Could not resolve current price for {symbol}: {e}")

    logger.info(f"Market: Retrieved price data for {len(price_map)}/{len(symbols_list)} tickers")

    # Perform updates inside the database for each retrieved ticker price
    for symbol, price in price_map.items():
        try:
            # Check if the instrument is already registered in our database
            instrument = db.instruments.find_by_symbol(symbol)
            if instrument:
                update_data = {'current_price': price}
                
                # Execute the database UPDATE statement on the 'instruments' table.
                # It updates 'current_price' for the record matching the ticker symbol.
                success = db.client.update(
                    'instruments',
                    update_data,
                    "symbol = :symbol",
                    {'symbol': symbol}
                )
                if success:
                    logger.info(f"Market: Database updated for {symbol} = ₹{price:.2f}")
                else:
                    logger.warning(f"Market: Update statement failed to apply for {symbol}")
            else:
                logger.warning(f"Market: Symbol {symbol} has not been tag-indexed in database yet")
        except Exception as e:
            logger.error(f"Market: Database update error for symbol {symbol}: {e}")

    # Track and log any symbols that failed to update so we can debug them later
    missing = set(symbols_list) - set(price_map.keys())
    if missing:
        logger.warning(f"Market: Failed to retrieve pricing updates for: {missing}")


def get_all_portfolio_symbols(db) -> Set[str]:
    """
    Batch retrieve all unique asset tickers across all users.
    Useful for system-wide index pre-fetching jobs or bulk cron updates.
    """
    symbols = set()

    try:
        # Run raw select query to pull distinct symbols list across the entire table
        all_positions = db.db.execute(
            "SELECT DISTINCT symbol FROM positions"
        )

        for position in all_positions:
            if position['symbol']:
                symbols.add(position['symbol'])

    except Exception as e:
        logger.error(f"Market: Failed to query distinct positions: {e}")

    return symbols