import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

# Setup environment variables to suppress OpenAI SDK built-in platform telemetry errors
os.environ["OPENAI_API_KEY"] = "dummy-key-for-agents-sdk"
os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
os.environ["AWS_REGION_NAME"] = os.getenv("DEFAULT_AWS_REGION", "us-east-1")

async def test():
    import litellm
    from agents import Agent, Runner, trace, function_tool
    from agents.extensions.models.litellm_model import LitellmModel

    # Enable callbacks
    litellm.success_callback = ["langfuse"]

    model = LitellmModel(model="bedrock/moonshotai.kimi-k2.5")

    @function_tool
    def get_inflation_rate(year: int) -> str:
        """Retrieves the inflation rate for a year."""
        return "3.1%"

    agent = Agent(
        name="FinAI Research Assistant",
        instructions="Retrieve inflation data.",
        model=model,
        tools=[get_inflation_rate]
    )

    # Let's test passing metadata to the trace context manager
    print("Starting trace execution...")
    try:
        with trace("Researcher", metadata={"env": "bootcamp_demo", "user": "student_1"}):
            result = await Runner.run(agent, input="What was the inflation rate in 2025?")
            print("Final Output:", result.final_output)
    except Exception as e:
        print("Error setting metadata in trace manager:", e)

if __name__ == "__main__":
    asyncio.run(test())
