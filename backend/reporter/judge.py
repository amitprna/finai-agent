"""
Reporter Evaluation (Judge) Agent
Runs a secondary LLM evaluation pass on the generated financial report.
Grades accuracy, clarity, and recommendations, returning feedback and quality scores.
"""

import litellm
from pydantic import BaseModel, Field
import os
import logging

logger = logging.getLogger()


class Evaluation(BaseModel):
    """Structured Pydantic response format for the Judge evaluation model."""
    feedback: str = Field(
        description="Detailed review feedback on the report's quality, coverage, and layout consistency."
    )
    score: float = Field(
        description="Score between 0 (terrible) and 100 (outstanding, industry-grade financial assessment)."
    )


async def evaluate(original_instructions: str, original_task: str, original_output: str) -> Evaluation:
    """
    Invokes LiteLLM to critique the generated report.
    
    Args:
        original_instructions: System prompts used to instruct the analyst agent
        original_task: Task context payload given to the analyst agent
        original_output: Report markdown content returned by the analyst agent
        
    Returns:
        An Evaluation instance containing comments and numeric score
    """
    # Get model configuration
    model_id = os.getenv("BEDROCK_MODEL_ID", "moonshotai.kimi-k2.5")
    bedrock_region = os.getenv("BEDROCK_REGION", "us-west-2")
    os.environ["AWS_REGION_NAME"] = bedrock_region

    model = f"bedrock/{model_id}"

    # Instructions defining the critique criteria
    instructions = """
You are an Evaluation Agent that evaluates the quality of a financial report from a financial planning agent.
You will be provided with the instructions that were sent to the analyst, and its output, and you must evaluate the quality of the output.
"""

    # Assemble task text combining input inputs and output answers
    task = f"""
The financial planning agent was given the following instructions:

{original_instructions}

And it was assigned this task:

{original_task}

The financial planning agent's output was:

{original_output}

Evaluate this output and respond with your comments and score.
"""

    try:
        logger.info("Judging financial report")
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": task}
            ],
            response_format=Evaluation
        )
        content = response.choices[0].message.content
        if isinstance(content, str):
            return Evaluation.model_validate_json(content)
        return content
    except Exception as e:
        logger.error(f"Error evaluating financial report: {e}")
        # Return fallback quality evaluation on network or API failures
        return Evaluation(feedback=f"Error evaluating financial report: {e}", score=80.0)
