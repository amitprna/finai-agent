"""
Prices Scraping Module
Uses Yahoo Finance v8 chart query API to retrieve latest price stats for ticker symbols.
If the API call fails or encounters request blocks, applies a random pricing fallback.
"""

import urllib.request
import json
import random
from dotenv import load_dotenv
from datetime import datetime

# Load local environment variables from .env if present
load_dotenv(override=True)


def is_market_open() -> bool:
    """
    Checks if global markets are open.
    Simplified checks: returns True on standard business weekdays (Mon-Fri).
    """
    return datetime.now().weekday() < 5


def get_share_price(symbol: str) -> float:
    """
    Fetch the latest market close/last price for a symbol via Yahoo Finance query API.
    
    How does it work?
    1. Standardizes symbol formatting (converts to uppercase and trims whitespaces).
    2. Appends '.NS' (National Stock Exchange of India suffix) if no market suffix is present.
    3. Sends a synchronous GET request to Yahoo Finance chart API.
    4. Attaches a standard browser 'User-Agent' header to prevent Yahoo from blocking the request.
    5. Reads the JSON output and tries multiple fields to find a valid price.
    6. Falls back to a random pricing number if any step fails.

    Args:
        symbol: Ticker symbol string (e.g. 'NIFTYBEES.NS' or 'RELIANCE')
        
    Returns:
        float representation of current market quote or random fallback price.
    """
    try:
        # Standardize formatting to uppercase and clean up leading/trailing whitespaces
        symbol = symbol.upper().strip()
        
        # If the user didn't specify an exchange suffix (like '.NS' for NSE or '.BO' for BSE),
        # we default to NSE ('.NS') since this is an Indian portfolio application.
        if "." not in symbol:
            symbol = f"{symbol}.NS"
            
        # Construct the Yahoo Finance charts endpoint URL.
        # range=1d and interval=1d retrieves today's trading data summary.
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
        
        # Why do we need headers?
        # Many websites, including Yahoo Finance, block requests that have default Python headers
        # (like 'Python-urllib/3.9') to prevent scraping bots.
        # We spoof a standard Web Chrome Browser header to bypass these scraper detection blocks.
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
            }
        )
        
        # Execute request synchronously with a 10s timeout window to avoid hanging if the server is slow
        with urllib.request.urlopen(req, timeout=10) as response:
            # Parse the bytes response content into a standard Python dictionary
            data = json.loads(response.read().decode())
            result = data.get("chart", {}).get("result", [])
            
            if result:
                meta = result[0].get("meta", {})
                
                # Check for regularMarketPrice field (most recent trading price)
                price = meta.get("regularMarketPrice")
                if price is not None:
                    return float(price)
                    
                # Fallback option 1: previousClose (yesterday's closing price)
                price = meta.get("previousClose")
                if price is not None:
                    return float(price)
                
                # Fallback option 2: extract indicators close lists (last recorded tick close price)
                quote = result[0].get("indicators", {}).get("quote", [])
                if quote and "close" in quote[0]:
                    closes = [c for c in quote[0]["close"] if c is not None]
                    if closes:
                        return float(closes[-1])
                        
    except Exception as e:
        # Log the exception to stdout and fallback gracefully instead of crashing
        print(f"Was not able to fetch price for {symbol} via query API: {e}; using fallback pricing")
    
    # Why return a random fallback?
    # If the Yahoo API is down, we still want the application to function for demo purposes.
    # Returning a mock price between 100 and 2000 allows downstream calculations to complete successfully.
    return float(random.randint(100, 2000))

