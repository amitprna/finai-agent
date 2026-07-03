"""
Vector Ingestion Pipeline Service
Receives raw text documents, routes them to SageMaker serverless endpoints
to compute 384-dimensional mathematical embeddings, and saves vectors to the S3 index.
"""

import datetime
import json
import os
import uuid
import boto3

# ----------------------------------------------------
# Configuration & AWS Clients Setup
# ----------------------------------------------------
# Read S3 vector storage location and endpoint config variables from AWS environment
VECTOR_BUCKET = os.environ.get("VECTOR_BUCKET")
SAGEMAKER_ENDPOINT = os.environ.get("SAGEMAKER_ENDPOINT")
INDEX_NAME = os.environ.get("INDEX_NAME", "financial-research")

# sagemaker_runtime: Boto3 client to invoke model endpoints for real-time inferences.
sagemaker_runtime = boto3.client("sagemaker-runtime")
# s3_vectors: Boto3 client wrapper for interacting with the S3 Vectors index.
s3_vectors = boto3.client("s3vectors")


def get_embedding(text: str) -> list:
    """
    Send text strings to the SageMaker HuggingFace serverless endpoint to get embeddings.
    
    Args:
        text: Plaintext content snippet to tokenize and represent as a vector array.
        
    Returns:
        List of floats (384 dimensions) representing the semantic vector of the text.
    """
    # Invoke the SageMaker endpoint with the JSON payload
    response = sagemaker_runtime.invoke_endpoint(
        EndpointName=SAGEMAKER_ENDPOINT,
        ContentType="application/json",
        Body=json.dumps({"inputs": text}),
    )

    # Decode and parse the JSON response body
    result = json.loads(response["Body"].read().decode())

    # HuggingFace sentence-transformers return nested array formats depending on task shape.
    # We recursively check arrays to extract the core float array list.
    if isinstance(result, list) and len(result) > 0:
        if isinstance(result[0], list) and len(result[0]) > 0:
            if isinstance(result[0][0], list):
                # Format: [[[embedding]]] -> Extract innermost list
                return result[0][0]
            # Format: [[embedding]] -> Extract inner list
            return result[0]
            
    # Fallback: return array list directly
    return result


def lambda_handler(event, context):
    """
    AWS Lambda entry point for document vector ingestion.
    Parses incoming event payloads, invokes SageMaker embeddings, and writes to S3 Vectors.
    
    Expected payload schema (event):
    {
        "body": {
            "text": "AAPL stock outperformed market expectations in Q2 2026...",
            "metadata": {
                "source": "CNBC",
                "ticker": "AAPL"
            }
        }
    }
    """
    try:
        # Check if payload body is passed as string or pre-parsed dict
        if isinstance(event.get("body"), str):
            body = json.loads(event["body"])
        else:
            body = event.get("body", {})

        text = body.get("text")
        metadata = body.get("metadata", {})

        # Validate that the text payload is not empty
        if not text:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing required field: text"}),
            }

        # Stage 1: Call SageMaker to get embeddings
        print(f"Ingest: Fetching embeddings from SageMaker endpoint for: {text[:100]}...")
        embedding = get_embedding(text)

        # Stage 2: Create a unique identifier key for this document vector
        vector_id = str(uuid.uuid4())

        # Stage 3: Save vector array and metadata keys to S3 Vector Index
        print(f"Ingest: Storing document in bucket {VECTOR_BUCKET}, index: {INDEX_NAME}")
        s3_vectors.put_vectors(
            vectorBucketName=VECTOR_BUCKET,
            indexName=INDEX_NAME,
            vectors=[
                {
                    "key": vector_id,
                    "data": {"float32": embedding},
                    "metadata": {
                        "text": text,
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                        **metadata,  # Merge optional additional metadata (e.g. source, ticker)
                    },
                }
            ],
        )

        # Return successful indexing confirmation
        return {
            "statusCode": 200,
            "body": json.dumps(
                {"message": "Document indexed successfully", "document_id": vector_id}
            ),
        }
    except Exception as e:
        print(f"Ingest: Ingestion pipeline execution failed: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
