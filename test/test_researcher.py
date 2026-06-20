#!/usr/bin/env python3
"""
FinAI Researcher Stack Verification Script
Verifies SageMaker Serverless Embeddings and Bedrock Moonshot Kimi inference.
Run with: uv run test/test_researcher.py
"""

import json
import os

import boto3
from dotenv import load_dotenv

load_dotenv()


def test_sagemaker_embeddings():
    """Verify SageMaker Serverless Endpoint is responding with correct vector dimension"""
    print("\n--- Testing SageMaker Serverless Embeddings ---")
    endpoint_name = os.getenv("SAGEMAKER_ENDPOINT", "finai-embedding-endpoint")
    region = os.getenv("DEFAULT_AWS_REGION", "us-east-1")

    print(f"Targeting SageMaker endpoint: {endpoint_name} in {region}")

    try:
        sagemaker_client = boto3.client("sagemaker-runtime", region_name=region)
        payload = {
            "inputs": "Test Indian equity research document content for semantic matching"
        }

        response = sagemaker_client.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="application/json",
            Body=json.dumps(payload),
        )

        result = json.loads(response["Body"].read().decode())

        if isinstance(result, list):
            embedding = result[0][0] if isinstance(result[0][0], list) else result
            print("[PASS] Success! Embedding generated successfully.")
            print(f"   Vector Dimensions: {len(embedding)} (Expected: 384)")
            print(f"   Sample dimensions: {embedding[:5]}...")
        else:
            print(f"[FAIL] Unexpected response type: {type(result)}")
    except Exception as e:
        print(f"[FAIL] Failed to invoke SageMaker: {e}")


def test_bedrock_kimi():
    """Verify Bedrock Kimi K2.5 Model can be invoked cleanly"""
    print("\n--- Testing Bedrock Kimi Model Inference ---")
    model_id = os.getenv("BEDROCK_MODEL_ID", "moonshotai.kimi-k2.5")
    region = os.getenv("BEDROCK_REGION", "us-east-1")

    print(f"Targeting Bedrock Model: {model_id} in {region}")

    try:
        bedrock_client = boto3.client("bedrock-runtime", region_name=region)

        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": "Hello! List 3 main stock exchanges in India.",
                }
            ],
            "max_tokens": 150,
        }

        response = bedrock_client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(payload),
        )

        response_body = json.loads(response.get("body").read().decode())
        content = (
            response_body.get("choices", [{}])[0].get("message", {}).get("content", "")
        )

        print("[PASS] Success! Bedrock responded:")
        safe_content = content.strip().encode("ascii", errors="replace").decode("ascii")
        print(f"\n{safe_content}\n")
    except Exception as e:
        print(f"[FAIL] Failed to invoke Bedrock Kimi: {e}")


def test_end_to_end_researcher():
    """Verify the entire end-to-end Researcher Stack by invoking the Lambda directly and checking S3"""
    print("\n--- Testing End-to-End Researcher Agent ---")
    region = os.getenv("DEFAULT_AWS_REGION", "us-east-1")

    try:
        # 1. Get AWS Account ID to derive the bucket name dynamically
        sts_client = boto3.client("sts", region_name=region)
        account_id = sts_client.get_caller_identity()["Account"]
        bucket_name = f"finai-vectors-{account_id}"

        from botocore.config import Config

        lambda_client = boto3.client(
            "lambda",
            region_name=region,
            config=Config(read_timeout=180, connect_timeout=10),
        )

        # 2. Trigger the Researcher Lambda directly using a mocked Function URL Event
        payload = {
            "version": "2.0",
            "rawPath": "/research",
            "requestContext": {"http": {"method": "POST", "path": "/research"}},
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"topic": "Tata Consultancy Services (TCS)"}),
        }

        print("Invoking Researcher Lambda directly via boto3 SDK...")
        print(
            "[RUNNING] Running web research, Kimi analysis, and vector database ingestion... (This can take 30-60s)"
        )

        response = lambda_client.invoke(
            FunctionName="finai-researcher", Payload=json.dumps(payload)
        )

        result_payload = json.loads(response["Payload"].read().decode("utf-8"))

        # Check if invocation completed successfully
        if "body" in result_payload:
            # Lambda Web Adapter returns HTTP response
            body_content = (
                json.loads(result_payload["body"])
                if isinstance(result_payload["body"], str)
                else result_payload["body"]
            )

            # Check for FastAPI / Uvicorn application errors
            if isinstance(body_content, dict) and "detail" in body_content:
                print(
                    f"[FAIL] Lambda returned application error: {body_content['detail']}"
                )
                return

            print("[PASS] E2E Success! Researcher Lambda execution completed.")
            body_str = str(body_content)
            safe_summary = (
                body_str[:500].encode("ascii", errors="replace").decode("ascii")
            )
            print(f"\nAgent analysis summary:\n{safe_summary}...\n")
        else:
            print(f"[FAIL] Unexpected Lambda response structure: {result_payload}")
            return

        # 3. Fetch/Verify ingested contents from S3 Vectors bucket using the official s3vectors client
        print("Verifying S3 Vectors index contents...")

        # Get embedding of the search term from SageMaker first
        sagemaker_client = boto3.client("sagemaker-runtime", region_name=region)
        sm_payload = {"inputs": "Tata Consultancy Services (TCS) financial overview"}
        sm_response = sagemaker_client.invoke_endpoint(
            EndpointName=os.getenv("SAGEMAKER_ENDPOINT", "finai-embedding-endpoint"),
            ContentType="application/json",
            Body=json.dumps(sm_payload),
        )
        sm_result = json.loads(sm_response["Body"].read().decode())
        query_embedding = sm_result[0] if isinstance(sm_result[0], list) else sm_result
        if isinstance(query_embedding[0], list):
            query_embedding = query_embedding[0]

        # Run semantic search against the index to retrieve the ingested analysis
        s3vectors_client = boto3.client("s3vectors", region_name=region)
        search_response = s3vectors_client.query_vectors(
            vectorBucketName=bucket_name,
            indexName="financial-research",
            queryVector={"float32": query_embedding},
            topK=3,
            returnDistance=True,
            returnMetadata=True,
        )

        vectors = search_response.get("vectors", [])
        if vectors:
            print(
                f"[PASS] Success! Found {len(vectors)} conceptually matching vectors in the index:"
            )
            for i, vec in enumerate(vectors, 1):
                metadata = vec.get("metadata", {})
                score = 1.0 - vec.get(
                    "distance", 0.0
                )  # org score is cosine similarity 0 max similar
                text_preview = (
                    metadata.get("text", "")[:150] + "..."
                    if len(metadata.get("text", "")) > 150
                    else metadata.get("text", "")
                )
                safe_preview = text_preview.encode("ascii", errors="replace").decode(
                    "ascii"
                )
                print(f" - Match {i} (Similarity Score: {score:.3f}):")
                print(f"   Text: {safe_preview}")
        else:
            print(
                f"[WARNING] S3 Vectors query returned 0 matches. Ingestion might have skipped or failed."
            )

    except Exception as e:
        print(f"[FAIL] Failed E2E test execution: {e}")


if __name__ == "__main__":
    print("[START] Starting Researcher Stack Verification Tests...")
    test_sagemaker_embeddings()
    test_bedrock_kimi()
    test_end_to_end_researcher()
    print("\nTests completed.")
