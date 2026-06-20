import os
from typing import Dict, Any
from datetime import datetime, UTC
import json
import boto3
from agents import function_tool

# Configuration from environment
INGEST_LAMBDA_NAME = os.getenv("INGEST_LAMBDA_NAME", "finai-ingest")
AWS_REGION_NAME = os.getenv("AWS_REGION_NAME", "us-east-1")


@function_tool
def ingest_financial_document(topic: str, analysis: str) -> Dict[str, Any]:
    """
    Ingest a financial document into the FinAI knowledge base.
    
    Args:
        topic: The topic or subject of the analysis (e.g., "AAPL Stock Analysis", "Retirement Planning Guide")
        analysis: Detailed analysis or advice with specific data and insights
    
    Returns:
        Dictionary with success status and document ID
    """
    if not INGEST_LAMBDA_NAME:
        return {
            "success": False,
            "error": "Ingestion Lambda not configured. Running in local mode."
        }
    
    document = {
        "text": analysis,
        "metadata": {
            "topic": topic,
            "timestamp": datetime.now(UTC).isoformat()
        }
    }
    
    try:
        lambda_client = boto3.client("lambda", region_name=AWS_REGION_NAME)
        response = lambda_client.invoke(
            FunctionName=INGEST_LAMBDA_NAME,
            InvocationType="RequestResponse",
            Payload=json.dumps({"body": document})
        )
        
        status_code = response.get("StatusCode", 0)
        if status_code != 200:
            raise Exception(f"Lambda invocation failed with status code {status_code}")
            
        result_payload = json.loads(response["Payload"].read().decode("utf-8"))
        
        if isinstance(result_payload, dict) and "error" in result_payload:
            raise Exception(result_payload["error"])
            
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