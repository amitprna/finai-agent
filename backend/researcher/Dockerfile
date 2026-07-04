# --- Stage 1: Downloader Stage ---
# Pull the AWS Lambda Web Adapter binary image. We do this at the very beginning 
# using a Multi-stage build so we can copy only the required binary later, keeping 
# our final runtime image small and clean.
FROM public.ecr.aws/awsguru/aws-lambda-adapter:1.0.0 AS lambda-adapter

# --- Stage 2: Main Runtime Stage ---
# Pull the primary OS image (a minimal Debian Linux distribution) with Python 3.12 
# pre-installed. This is our final runtime environment.
FROM python:3.12-slim

# Set the working directory where all subsequent code and packages will reside.
# Any instructions run after this line will execute in the context of the `/app` folder.
WORKDIR /app

# Lambda's filesystem is read-only except for /tmp.
# 1. PLAYWRIGHT_BROWSERS_PATH directs Playwright to download and run Chromium from a specific root folder.
# 2. HOME and XDG_CACHE_HOME are set to /tmp to prevent libraries from writing cache files 
#    to read-only directories, which would crash the Lambda.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

# Install curl and Node.js (needed to run the Playwright MCP server).
# Then install the Node-based Playwright MCP server package globally.
RUN apt-get update \
    && apt-get install -y curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g @playwright/mcp@0.0.75 \
    && rm -rf /var/lib/apt/lists/*

# Install the Chromium browser. The `--with-deps` flag is critical: it automatically 
# downloads and installs all system-level Linux operating system libraries (like fonts, 
# graphics libraries, and renderers) required for Chromium to launch headlessly in this slim OS.
RUN npx -y playwright install --with-deps chromium

# Install uv for fast Python dependency management.
RUN pip install uv

# Copy the Python package manifest (pyproject.toml) and sync dependencies.
# We copy this first to leverage Docker's layer caching (it won't re-run unless dependencies change).
COPY pyproject.toml ./
RUN uv sync --no-install-project

# Prepend the virtual environment's bin folder to the system PATH.
# This "activates" the virtual environment, ensuring the container uses the libraries 
# (like FastAPI, uvicorn, litellm) installed in the .venv folder when we run CLI commands.
ENV PATH="/app/.venv/bin:$PATH"

# Copy the Python application code files.
COPY *.py ./

# Copy the Lambda Web Adapter binary from Stage 1 into the Lambda extensions directory.
# Lambda will automatically load this extension on startup to bridge Lambda invocations to HTTP requests.
COPY --from=lambda-adapter /lambda-adapter /opt/extensions/lambda-adapter

# Tells the Lambda Web Adapter which local port our internal FastAPI app is listening on.
# The adapter will proxy all incoming Lambda requests directly to this port.
ENV AWS_LWA_PORT=8000

# Expose port 8000 (informative documentation) and run the uvicorn web server.
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
