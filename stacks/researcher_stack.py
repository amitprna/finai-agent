import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Size,
    Stack,
)
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3vectors as s3vectors
from aws_cdk import aws_sagemaker as sagemaker
from constructs import Construct


class ResearcherStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. Configuration & Env Variables
        # ----------------------------------------------------
        ingest_lambda_name = os.getenv("INGEST_LAMBDA_NAME", "finai-ingest")
        bedrock_region = os.getenv("BEDROCK_REGION", "us-east-1")
        researcher_model = os.getenv("RESEARCHER_MODEL", "bedrock/moonshotai.kimi-k2.5")
        mcp_logging = os.getenv("MCP_LOGGING", "false")

        # Langfuse Environment Configuration for LLM Observability
        langfuse_public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        langfuse_secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
        langfuse_host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        # SageMaker HuggingFace container, comes prepakcged with PyTorch HuggingFace Transformers(oad the model and tokenize inputs.)
        sagemaker_image_uri = os.getenv(
            "SAGEMAKER_IMAGE_URI",
            f"763104351884.dkr.ecr.{self.region}.amazonaws.com/huggingface-pytorch-inference:1.13.1-transformers4.26.0-cpu-py39-ubuntu20.04",
        )

        # text embedding model (384 dimensional vector)
        embedding_model_name = os.getenv(
            "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
        )

        # ----------------------------------------------------
        # 2. SageMaker Serverless Inference Endpoint (Embeddings)
        # ----------------------------------------------------
        sagemaker_role = iam.Role(
            self,
            "SageMakerRole",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            role_name="finai-sagemaker-role",
        )
        sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerFullAccess")
        )

        model = sagemaker.CfnModel(
            self,
            "EmbeddingModel",
            model_name="finai-embedding-model",
            execution_role_arn=sagemaker_role.role_arn,
            primary_container=sagemaker.CfnModel.ContainerDefinitionProperty(
                image=sagemaker_image_uri,
                environment={
                    "HF_MODEL_ID": embedding_model_name,
                    "HF_TASK": "feature-extraction",
                },
            ),
        )

        endpoint_config = sagemaker.CfnEndpointConfig(
            self,
            "ServerlessConfig",
            endpoint_config_name="finai-embedding-serverless-config",
            production_variants=[
                sagemaker.CfnEndpointConfig.ProductionVariantProperty(
                    variant_name="AllTraffic",
                    model_name=model.model_name,
                    initial_variant_weight=1.0,
                    serverless_config=sagemaker.CfnEndpointConfig.ServerlessConfigProperty(
                        memory_size_in_mb=3072, max_concurrency=2
                    ),
                )
            ],
        )
        endpoint_config.add_dependency(model)

        self.sagemaker_endpoint = sagemaker.CfnEndpoint(
            self,
            "EmbeddingEndpoint",
            endpoint_name="finai-embedding-endpoint",
            endpoint_config_name=endpoint_config.endpoint_config_name,
        )
        self.sagemaker_endpoint.add_dependency(endpoint_config)

        # ----------------------------------------------------
        # 3. S3 Vectors Database (Cost-Optimized Vector DB)
        # ----------------------------------------------------
        self.vectors_bucket = s3vectors.CfnVectorBucket(
            self, "VectorsBucket", vector_bucket_name=f"finai-vectors-{self.account}"
        )

        # Bucket: database; Index: table
        # Inside this table, we will store a list of documents. Each document consists of three parts: a Unique ID, a Vector (embeddings), and a Metadata JSON.
        """
        s3://finai-vectors-bucket/index/financial-research/
            ├── vectors/
            │    └── data_part1.bin       <-- Binary file containing raw 384-dimension floats
            ├── metadata/
            │    ├── doc_1.json           <-- Plaintext metadata for document 1
            │    └── doc_2.json           <-- Plaintext metadata for document 2
            └── index/
                └── hnsw_graph.idx       <-- Search index structure (spatial graph connecting vectors that are close to each other: )
                
        {
            "id": "doc-uuid-9876",
            "vector": [0.012, -0.089, 0.451, ..., 0.003],  // 384 dimensions from SageMaker
            "metadata": {
                "text": "Tata Consultancy Services (TCS.NS) reported a YoY net profit increase of 8.7% for Q1 2026, beating estimates.",
                "topic": "TCS.NS Q1 Analysis",
                "timestamp": "2026-06-20T17:00:00Z"
            }
        }

        """
        self.vectors_index = s3vectors.CfnIndex(
            self,
            "VectorsIndex",
            vector_bucket_arn=self.vectors_bucket.attr_vector_bucket_arn,
            index_name="financial-research",
            data_type="float32",
            dimension=384,  # miniLM output vector size
            distance_metric="cosine",
        )
        self.vectors_index.node.add_dependency(self.vectors_bucket)

        # ----------------------------------------------------
        # 4. Ingestion Pipeline Lambda Function
        # ----------------------------------------------------
        ingest_role = iam.Role(
            self,
            "IngestLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            role_name="finai-ingest-lambda-role",
        )
        ingest_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )
        ingest_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sagemaker:InvokeEndpoint"],
                resources=[
                    f"arn:aws:sagemaker:{self.region}:{self.account}:endpoint/{self.sagemaker_endpoint.endpoint_name}"
                ],
            )
        )
        ingest_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3vectors:PutVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:DeleteVectors",
                ],
                resources=[
                    f"arn:aws:s3vectors:{self.region}:{self.account}:bucket/{self.vectors_bucket.vector_bucket_name}/index/*"
                ],
            )
        )

        ingest_code_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backend", "ingest")
        )

        ingest_code = _lambda.Code.from_asset(ingest_code_dir)

        ingest_log_group = logs.LogGroup(
            self,
            "IngestLambdaLogs",
            log_group_name="/aws/lambda/finai-ingest",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.ingest_lambda = _lambda.Function(
            self,
            "IngestLambda",
            function_name="finai-ingest",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="ingest_s3vectors.lambda_handler",
            code=ingest_code,
            role=ingest_role,
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                "VECTOR_BUCKET": self.vectors_bucket.vector_bucket_name,
                "SAGEMAKER_ENDPOINT": self.sagemaker_endpoint.endpoint_name,
            },
            log_group=ingest_log_group,
        )

        # ----------------------------------------------------
        # 5. Researcher Lambda Agent (Automated Local Docker Build)
        # ----------------------------------------------------

        researcher_role = iam.Role(
            self,
            "ResearcherLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            role_name="finai-researcher-lambda-role",
        )
        researcher_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        # Bedrock permissions for Moonshot Kimi K2.5 execution
        researcher_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ListFoundationModels",
                ],
                resources=["*"],
            )
        )

        # S3 Vectors permissions for semantic text retrieval
        researcher_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3vectors:QueryVectors", "s3vectors:GetVectors"],
                resources=[
                    f"arn:aws:s3vectors:{self.region}:{self.account}:bucket/{self.vectors_bucket.vector_bucket_name}/index/*"
                ],
            )
        )

        # Ingestion Lambda direct invocation permissions
        researcher_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                resources=[self.ingest_lambda.function_arn],
            )
        )

        researcher_log_group = logs.LogGroup(
            self,
            "ResearcherLogs",
            log_group_name="/aws/lambda/finai-researcher",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        researcher_code_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backend", "researcher")
        )

        self.researcher_lambda = _lambda.DockerImageFunction(
            self,
            "ResearcherLambda",
            function_name="finai-researcher",
            code=_lambda.DockerImageCode.from_image_asset(researcher_code_dir),
            role=researcher_role,
            timeout=Duration.seconds(300),
            memory_size=2048,
            # Playwright needs to download and extract headless browser binaries (Chromium) which exceed 512 MB, we increase this to 2048 MB (2 GB) of ephemeral storage.
            ephemeral_storage_size=Size.mebibytes(2048),  # Disk space, (/tmp directory)
            environment={
                "INGEST_LAMBDA_NAME": ingest_lambda_name,
                "BEDROCK_REGION": bedrock_region,
                "RESEARCHER_MODEL": researcher_model,
                "MCP_LOGGING": mcp_logging,
                # Langfuse Configuration
                "LANGFUSE_PUBLIC_KEY": langfuse_public_key,
                "LANGFUSE_SECRET_KEY": langfuse_secret_key,
                "LANGFUSE_HOST": langfuse_host,
                "AWS_REGION_NAME": bedrock_region,
            },
            log_group=researcher_log_group,
        )

        # Public Function URL for direct triggers without API Gateway
        self.function_url = self.researcher_lambda.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE
        )

        # ----------------------------------------------------
        # 6. Outputs
        # ----------------------------------------------------

        CfnOutput(
            self,
            "ResearcherFunctionUrl",
            value=self.function_url.url,
            description="Public URL of the Researcher Lambda function",
        )

        CfnOutput(
            self,
            "SageMakerEndpointName",
            value=self.sagemaker_endpoint.endpoint_name,
            description="SageMaker serverless embeddings endpoint name",
        )
        CfnOutput(
            self,
            "VectorsBucketName",
            value=self.vectors_bucket.vector_bucket_name,
            description="S3 Vectors bucket name",
        )
