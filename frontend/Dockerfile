FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    streamlit>=1.30.0 \
    pandas>=2.0.0 \
    plotly>=5.15.0 \
    requests>=2.31.0 \
    python-dotenv>=1.0.0 \
    boto3>=1.34.0

# Copy application files
COPY . .

# Expose port
EXPOSE 8501

ENTRYPOINT ["streamlit", "run", "app.py"]
