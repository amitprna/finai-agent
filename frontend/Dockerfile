# ----------------------------------------------------
# 1. Base Image Specification
# ----------------------------------------------------
# We use Python 3.12 slim-buster. The 'slim' variant is a minimal base image that includes
# only the essentials needed to run Python. This helps keep our final Docker container image size small
# (reducing build time, deployment time, and storage costs) compared to the full Python base image.
FROM python:3.12-slim

# ----------------------------------------------------
# 2. Environment Variables Configuration
# ----------------------------------------------------
# ENV lets us configure persistent environment variables inside the container.
#
# - PYTHONUNBUFFERED=1: Disables output buffering in Python. This ensures that stdout/stderr logs
#   are printed directly to the console immediately, making it easy to monitor container logs in AWS CloudWatch.
# - PYTHONDONTWRITEBYTECODE=1: Prevents Python from writing compiled bytecode (.pyc files) on disk.
#   Since containers are ephemeral and readonly, generating .pyc files is unnecessary overhead.
# - STREAMLIT_SERVER_PORT=8501: Sets the port on which Streamlit will run.
# - STREAMLIT_SERVER_ADDRESS=0.0.0.0: Binds the server to all network interfaces. This allows network traffic
#   from outside the container (like an AWS Load Balancer or local host) to reach the app.
# - STREAMLIT_SERVER_HEADLESS=true: Tells Streamlit not to open a new browser window when it starts up,
#   which is essential since there is no graphical display inside a Docker container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true

# ----------------------------------------------------
# 3. Working Directory Setup
# ----------------------------------------------------
# Set the current working directory inside the container's virtual filesystem to '/app'.
# Any subsequent commands (like RUN or COPY) will be executed relative to this path.
WORKDIR /app

# ----------------------------------------------------
# 4. Dependency Installation
# ----------------------------------------------------
# We install all frontend Python requirements.
# Using '--no-cache-dir' is a docker best practice: it prevents pip from caching the downloaded
# package installer archives (.whl or tar.gz) inside the image, saving valuable megabytes of disk space.
RUN pip install --no-cache-dir \
    streamlit>=1.30.0 \
    pandas>=2.0.0 \
    plotly>=5.15.0 \
    requests>=2.31.0 \
    python-dotenv>=1.0.0 \
    boto3>=1.34.0

# ----------------------------------------------------
# 5. Application Code Copying
# ----------------------------------------------------
# Copies all files and subfolders from the host workspace (frontend directory) into the container's
# active /app working directory. This copies app.py and the .streamlit/config.toml styling parameters.
COPY . .

# ----------------------------------------------------
# 6. Port Exposure Metadata
# ----------------------------------------------------
# EXPOSE serves as documentation telling Docker (and deployment scripts/AWS ECS) that the container
# will listen on port 8501. It doesn't actually publish the port; the user or balancer does that at runtime.
EXPOSE 8501

# ----------------------------------------------------
# 7. Startup Command Execution
# ----------------------------------------------------
# ENTRYPOINT defines the default command that runs when the container boots.
# We execute 'streamlit run app.py' to launch our dashboard.
ENTRYPOINT ["streamlit", "run", "app.py"]

