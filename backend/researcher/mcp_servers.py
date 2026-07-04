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
        # --headless: Runs the browser in the background without opening a visual window (Graphical User Interface).
        # This is mandatory for server environments like Lambda which have no screen/display output.
        "--headless",
        # --isolated: Creates independent, clean browser profiles for each run. 
        # Cookies, storage, and cache are not shared between sessions, avoiding cross-contamination.
        "--isolated",
        # --no-sandbox: Disables Chrome's security sandboxing layer since Lambda is already isolated
        "--no-sandbox",          
        # --ignore-https-errors: Prevents chromium from crashing or getting stuck when encountering bad/expired SSL certs on sites.
        "--ignore-https-errors",
        # --user-agent: Spoofs a standard Windows desktop browser agent to prevent bot-detection scripts from blocking scrapers.
        "--user-agent",
        PLAYWRIGHT_USER_AGENT,
        # --executable-path: Point Playwright to the specific Chromium binary directory we scanned in the container.
        "--executable-path",
        chrome_path,
        # --config: Tells Playwright MCP server to load memory-reduction configuration options from the temp JSON file.
        "--config",
        config_path,
    ]

    # Assemble parameter map for process creation
    params = {
        # command: The command that spawns the Node-based Playwright MCP server process.
        "command": "playwright-mcp",
        "args": args,
        # env: Inject environment variables into the Playwright MCP server process.
        "env": {
            # DEBUG: Enables detailed logging. 
            # - pw:api prints all actions taken via the Playwright API (clicks, input, navigations).
            # - pw:browser* prints console log updates and network browser events.
            "DEBUG": "pw:api,pw:browser*",
        },
    }

    logger.info("Creating Playwright MCP server with Chrome path: %s", chrome_path)
    
    # client_session_timeout_seconds: Closes the server and aborts the execution environment 
    # if the agent goes completely idle (no new tool calls or instructions) for 120 seconds.
    return MCPServerStdio(params=params, client_session_timeout_seconds=timeout_seconds)
