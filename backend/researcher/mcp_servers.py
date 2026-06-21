"""
MCP server configurations for the FinAI Researcher
"""

import glob
import json
import logging

from agents.mcp import MCPServerStdio

logger = logging.getLogger(__name__)

PLAYWRIGHT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def create_playwright_mcp_server(timeout_seconds=120):
    """Create a Playwright MCP server instance for web browsing.

    Args:
        timeout_seconds: Client session timeout in seconds (default: 120)

    Returns:
        MCPServerStdio instance configured for Playwright
    """
    # Locate Chrome binary inside the lambda environment
    chrome_paths = glob.glob("/ms-playwright/chromium-*/chrome-linux*/chrome")
    chrome_path = (
        chrome_paths[0]
        if chrome_paths
        else "/ms-playwright/chromium-1208/chrome-linux64/chrome"
    )

    # Configure browser launch options to be resource-friendly for Lambda environment
    config_path = "/tmp/playwright-mcp.config.json"
    config = {
        "browser": {
            "launchOptions": {
                "args": [
                    "--single-process",  # Forces Chromium to run everything in a single process saves RAM
                    "--no-zygote",  # Disables zygote(Chrome by default create resource in advance for new tab), another RAM saver
                    "--disable-gpu",  # Disables hardware graphics rendering since AWS Lambda servers do not have a GPU
                ]
            }
        }
    }
    with open(config_path, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file)

    args = [
        "--headless",
        "--isolated",
        "--no-sandbox",  # Disables Chrome's security sandboxing layer
        "--ignore-https-errors",
        "--user-agent",
        PLAYWRIGHT_USER_AGENT,
        "--executable-path",
        chrome_path,
        "--config",
        config_path,
    ]

    params = {
        "command": "playwright-mcp",
        "args": args,
        "env": {
            "DEBUG": "pw:api,pw:browser*",
        },
    }

    logger.info("Creating Playwright MCP server with Chrome path: %s", chrome_path)
    return MCPServerStdio(params=params, client_session_timeout_seconds=timeout_seconds)
