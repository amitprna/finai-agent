"""
Observability Module
Integrates Langfuse monitoring client with LiteLLM callbacks.
"""

import os
import logging
from contextlib import contextmanager

# Use root logger for Lambda compatibility
logger = logging.getLogger()
logger.setLevel(logging.INFO)


@contextmanager
def observe():
    """
    Context manager that registers the Langfuse monitoring hook.
    Ensures that any model calls made inside this block are traced and automatically
    flushed to the Langfuse backend upon exit.
    """
    logger.info("🔍 Observability: Checking configuration...")

    # Check if the Langfuse configuration key is available
    has_langfuse = bool(os.getenv("LANGFUSE_SECRET_KEY"))

    logger.info(f"🔍 Observability: LANGFUSE_SECRET_KEY configured = {has_langfuse}")

    if has_langfuse:
        try:
            import litellm
            # Enable Langfuse integration for all LiteLLM API executions
            litellm.success_callback = ["langfuse"]
            logger.info("✅ Observability: LiteLLM Langfuse callback enabled successfully")
        except Exception as e:
            logger.error(f"❌ Observability: Failed to initialize LiteLLM Langfuse callback: {e}")

    try:
        yield
    finally:
        # Flush pending HTTP requests to Langfuse server before Lambda environment sleeps
        if has_langfuse:
            try:
                from langfuse import Langfuse
                Langfuse().flush()
                logger.info("✅ Observability: Langfuse client flushed successfully")
            except Exception as e:
                logger.warning(f"⚠️ Observability: Failed to flush Langfuse client on exit: {e}")
