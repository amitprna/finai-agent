import os
import logging
from dotenv import load_dotenv

# Enable all debug logs for Langfuse SDK
os.environ["LANGFUSE_DEBUG"] = "True"

# Load the environment keys
load_dotenv()

from langfuse import Langfuse

print("[KEY CHECK] Checking environment keys:")
print("LANGFUSE_PUBLIC_KEY:", os.getenv("LANGFUSE_PUBLIC_KEY"))
secret = os.getenv("LANGFUSE_SECRET_KEY")
print("LANGFUSE_SECRET_KEY:", secret[:12] if secret else None)
print("LANGFUSE_HOST:", os.getenv("LANGFUSE_HOST"))

# Initialize Langfuse client
try:
    langfuse = Langfuse()
    print("\n[OK] Langfuse client initialized.")

    # Create a dummy test trace
    trace = langfuse.trace(
        name="Bootcamp Diagnostic Test",
        metadata={"environment": "local_bootcamp_test"}
    )
    print("[OK] Created trace object locally.")

    # Log a generation span
    generation = trace.generation(
        name="Inference Test Step",
        model="bedrock/moonshotai.kimi-k2.5",
        input="Hello Langfuse",
        output="Hello Developer"
    )
    print("[OK] Created span object locally.")

    # Force flush to upload telemetry synchronously
    print("\n[FLUSH] Flushing telemetry data synchronously...")
    langfuse.flush()
    print("[OK] Flush completed. Check dashboard now!")

except Exception as e:
    print(f"\n[FAIL] Diagnostic test failed: {e}")
