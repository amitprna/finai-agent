"""
MCP Server Configurations for the FinAI Researcher
Defines parameters for setting up study/research MCP servers like Playwright
running Chromium in resources-constrained Lambda containers.
"""

import glob
import json
import logging

from agents.mcp import MCPServerStdio

logger = logging.getLogger(__name__)

# Desktop Chrome User-Agent components (prevents bot detection):
# - Mozilla/5.0: Legacy prefix for general web server compatibility.
# - Windows NT 10.0; Win64; x64: Simulates 64-bit Windows 10/11 operating system.
# - AppleWebKit/537.36: Simulates Apple's WebKit rendering engine lineage.
# - KHTML, like Gecko: Simulates compatibility with KHTML & Firefox Gecko engines.
# - Chrome/125.0: The actual Google Chrome browser version being simulated.
# - Safari/537.36: Simulates Safari compatibility to receive modern CSS & media styles.
PLAYWRIGHT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def create_playwright_mcp_server(timeout_seconds=120):
    """
    Spawns a Playwright Chromium MCP server process in standard-input/output mode.
    
    Args:
        timeout_seconds: Client session timeout duration (defaults to 120s)
        
    Returns:
        MCPServerStdio construct configured for Playwright Chromium executions
    """
    # Scan file system paths inside Lambda container to find the Chromium binary.
    # Playwright installs browsers in `/ms-playwright/` directory.
    chrome_paths = glob.glob("/ms-playwright/chromium-*/chrome-linux*/chrome")
    chrome_path = (
        chrome_paths[0]
        if chrome_paths
        else "/ms-playwright/chromium-1208/chrome-linux64/chrome"
    )

    # Configure Chromium resource args optimized for AWS Lambda runtime memory restrictions.
    config_path = "/tmp/playwright-mcp.config.json"
    config = {
        "browser": {
            "launchOptions": {
                "args": [
                    "--single-process",  # Forces Chromium to run everything in a single process to save RAM
                    "--no-zygote",       # Disables zygote page pre-allocation to optimize startup memory
                    "--disable-gpu",     # Disables GPU graphics pipelines since server containers lack displays
                ]
            }
        }
    }
    
    # Save the configuration dictionary as a JSON file in the temporary directory
    with open(config_path, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file)

    # Compile the command line arguments list for launching Playwright MCP
    args = [
        "--headless",
        "--isolated",
        "--no-sandbox",          # Disables Chrome's security sandboxing layer since Lambda is already isolated
        "--ignore-https-errors",
        "--user-agent",
        PLAYWRIGHT_USER_AGENT,
        "--executable-path",
        chrome_path,
        "--config",
        config_path,
    ]

    # Assemble parameter map for process creation
    params = {
        "command": "playwright-mcp",
        "args": args,
        "env": {
            "DEBUG": "pw:api,pw:browser*",
        },
    }

    logger.info("Creating Playwright MCP server with Chrome path: %s", chrome_path)
    
    return MCPServerStdio(params=params, client_session_timeout_seconds=timeout_seconds)
