"""
Ingestion Tool for the Researcher Agent
Defines the tool function exposed to the LLM agent, allowing it to invoke
the ingestion Lambda pipeline directly with the compiled research summaries.
"""

import os
from typing import Dict, Any
from datetime import datetime, UTC
import json
import boto3
from agents import function_tool

# ----------------------------------------------------
# Configuration & Environment Reading
# ----------------------------------------------------
# Read physical Lambda name and target region parameters from environment variables
INGEST_LAMBDA_NAME = os.getenv("INGEST_LAMBDA_NAME", "finai-ingest")
AWS_REGION_NAME = os.getenv("AWS_REGION_NAME", "us-east-1")


@function_tool
def ingest_financial_document(topic: str, analysis: str) -> Dict[str, Any]:
    """
    Ingest a financial document containing parsed news/data into the vector database.
    This function is wrapped as a tool and exposed to the LLM agent.
    
    Args:
        topic: The topic/subject identifier of the research (e.g. "Tata Q1 Report")
        analysis: Plaintext markdown summary compiled by the LLM scraping run
        
    Returns:
        Dictionary mapping success status and document identifier uuid
    """
    # Verify that the target ingestion Lambda name is configured
    if not INGEST_LAMBDA_NAME:
        return {
            "success": False,
            "error": "Ingestion pipeline function is not configured"
        }
    
    # Construct the JSON payload mapping keys expected by the Ingestion Lambda
    document = {
        "text": analysis,
        "metadata": {
            "topic": topic,
            "timestamp": datetime.now(UTC).isoformat()
        }
    }
    
    try:
        # Initialize Boto3 Lambda client using targeted region properties
        lambda_client = boto3.client("lambda", region_name=AWS_REGION_NAME)
        
        # Invoke the Ingest Lambda function synchronously
        response = lambda_client.invoke(
            FunctionName=INGEST_LAMBDA_NAME,
            InvocationType="RequestResponse",  # Synchronous execute
            Payload=json.dumps({"body": document})
        )
        
        # Check HTTP response code
        status_code = response.get("StatusCode", 0)
        if status_code != 200:
            raise Exception(f"Lambda execution failed with HTTP status: {status_code}")
            
        # Parse return payload binary stream
        result_payload = json.loads(response["Payload"].read().decode("utf-8"))
        
        # Check if the execution returned a handled downstream error
        if isinstance(result_payload, dict) and "error" in result_payload:
            raise Exception(result_payload["error"])
            
        # Extract response fields depending on structure
        if isinstance(result_payload, dict) and "body" in result_payload:
            if isinstance(result_payload["body"], str):
                result_data = json.loads(result_payload["body"])
            else:
                result_data = result_payload["body"]
        else:
            result_data = result_payload
            
        return {
            "success": True,
            "document_id": result_data.get("document_id"),
            "message": f"Successfully ingested analysis for {topic}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }