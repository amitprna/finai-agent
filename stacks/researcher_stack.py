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
    """
    CDK Infrastructure Stack for the Researcher Agent and SageMaker Embeddings.
    This stack provisions the resources needed to crawl web pages, compute
    mathematical vector representations (embeddings) of texts, store them in a
    cost-effective vector database, and trigger ingestion.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        """
        Constructor function for the CDK stack.
        
        Args:
            scope: The parent construct tree, usually the main App class.
            construct_id: A unique string ID for this stack (e.g. 'ResearcherStack').
            kwargs: Extra parameters passed to the base Stack class, like AWS account and region.
        """
        super().__init__(scope, construct_id, **kwargs)

        # ----------------------------------------------------
        # 1. Configuration & Environment Variables Setup
        # ----------------------------------------------------
        # We read values from the host operating system's environment variables (using os.getenv).
        # This allows us to easily customize names or models without changing the CDK code itself.

        # ingest_lambda_name: The physical name of the Lambda function that inserts vectors into the database.
        # Defaults to 'finai-ingest' if the environment variable is not defined.
        ingest_lambda_name = os.getenv("INGEST_LAMBDA_NAME", "finai-ingest")

        # bedrock_region: The AWS region where Bedrock model endpoints are hosted (e.g., 'us-east-1').
        # Defaults to 'us-east-1' which has wide availability of foundational models.
        bedrock_region = os.getenv("BEDROCK_REGION", "us-east-1")

        # researcher_model: The LLM model identifier string used by LiteLLM (e.g. 'bedrock/moonshotai.kimi-k2.5').
        # Defaults to the Bedrock wrapper for Moonshot's Kimi model.
        researcher_model = os.getenv("RESEARCHER_MODEL", "bedrock/moonshotai.kimi-k2.5")

        # langfuse_public_key: Public credential string for Langfuse LLM tracing/monitoring.
        # Read from environment variables; left blank if observability is not configured.
        langfuse_public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")

        # langfuse_secret_key: Secret credential string for Langfuse LLM tracing/monitoring.
        # Used to authenticate API calls to the Langfuse backend server.
        langfuse_secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")

        # langfuse_host: URL endpoint of the Langfuse server (e.g., 'https://cloud.langfuse.com').
        # Tells the LiteLLM callbacks where to send telemetry events.
        langfuse_host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        # sagemaker_image_uri: The Docker container image URI that SageMaker will download and run.
        # This comes pre-packaged by AWS with PyTorch and HuggingFace Transformers (transformers 4.26.0, Python 3.9).
        # It handles running the model server and tokenizing text inputs out-of-the-box.
        sagemaker_image_uri = os.getenv(
            "SAGEMAKER_IMAGE_URI",
            f"763104351884.dkr.ecr.{self.region}.amazonaws.com/huggingface-pytorch-inference:1.13.1-transformers4.26.0-cpu-py39-ubuntu20.04",
        )

        # embedding_model_name: The name of the transformer model to fetch from the HuggingFace Hub.
        # Defaults to 'sentence-transformers/all-MiniLM-L6-v2', a small, fast model generating 384-dimensional vectors.
        # These vectors represent the semantic meaning of sentences.
        embedding_model_name = os.getenv(
            "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
        )

        # ----------------------------------------------------
        # 2. SageMaker Serverless Inference Endpoint (Embeddings)
        # ----------------------------------------------------
        # We need a service that converts sentences into numerical arrays (embeddings).
        # SageMaker Serverless Inference is perfect because it scales down to 0 when not in use,
        # costing us nothing during idle periods.

        # sagemaker_role: IAM Role assumed by the SageMaker model service.
        # This tells AWS what permissions the model runner has while executing.
        sagemaker_role = iam.Role(
            self,
            "SageMakerRole",
            # assumed_by: Tells AWS that the SageMaker service (sagemaker.amazonaws.com) is allowed to assume this role.
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            # role_name: The physical name of the role created in the IAM console.
            role_name="finai-sagemaker-role",
        )
        
        # add_managed_policy: Attaches a pre-built AWS policy giving SageMaker full access to resources it needs,
        # such as pulling containers from ECR or reading model files.
        sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerFullAccess")
        )

        # model: A CloudFormation SageMaker Model resource configuration.
        # This registers the model metadata and container specifications with AWS SageMaker.
        model = sagemaker.CfnModel(
            self,
            "EmbeddingModel",
            # model_name: The name of the model registered in the SageMaker console.
            model_name="finai-embedding-model",
            # execution_role_arn: The ARN of the IAM Role we created above that SageMaker uses to run the container.
            execution_role_arn=sagemaker_role.role_arn,
            # primary_container: Defines the Docker container image and environment variables.
            primary_container=sagemaker.CfnModel.ContainerDefinitionProperty(
                # image: The URI of the HuggingFace PyTorch Docker container image on ECR.
                image=sagemaker_image_uri,
                # environment: Environment variables loaded inside the container when it boots.
                environment={
                    # HF_MODEL_ID: Tells HuggingFace helper code to fetch this specific model from the hub.
                    "HF_MODEL_ID": embedding_model_name,
                    # HF_TASK: Tells HuggingFace to load the model specifically for extracting numerical embeddings.
                    "HF_TASK": "feature-extraction",
                },
            ),
        )

        # endpoint_config: Registers endpoint variants and serverless capacity specs.
        # This defines HOW the model is deployed (e.g. serverless vs. instance types, memory bounds).
        endpoint_config = sagemaker.CfnEndpointConfig(
            self,
            "ServerlessConfig",
            # endpoint_config_name: The name of this endpoint config configuration.
            endpoint_config_name="finai-embedding-serverless-config",
            # production_variants: A list of deployment variants running behind the endpoint.
            production_variants=[
                sagemaker.CfnEndpointConfig.ProductionVariantProperty(
                    # variant_name: Identifier for the variant group.
                    variant_name="AllTraffic",
                    # model_name: The name of the SageMaker Model resource we registered above.
                    model_name=model.model_name,
                    # initial_variant_weight: Distribution weight of traffic. 1.0 means 100% of calls go here.
                    initial_variant_weight=1.0,
                    # serverless_config: Configures the endpoint to be serverless (scales down to 0).
                    serverless_config=sagemaker.CfnEndpointConfig.ServerlessConfigProperty(
                        # memory_size_in_mb: Memory size allocated to the container (3072 MB = 3 GB).
                        memory_size_in_mb=3072,
                        # max_concurrency: Maximum number of concurrent executions allowed at any one time.
                        max_concurrency=2
                    ),
                )
            ],
        )
        
        # add_dependency: Ensures CDK creates the Model resource BEFORE creating the EndpointConfig.
        # This prevents CloudFormation from failing due to missing dependencies.
        endpoint_config.add_dependency(model)

        # sagemaker_endpoint: The active serverless endpoint itself.
        # This generates a live HTTP HTTPS API URL that client SDKs can call to generate vector embeddings.
        self.sagemaker_endpoint = sagemaker.CfnEndpoint(
            self,
            "EmbeddingEndpoint",
            # endpoint_name: The physical name of the endpoint used in clients.
            endpoint_name="finai-embedding-endpoint",
            # endpoint_config_name: Associates this endpoint with the serverless configuration created above.
            endpoint_config_name=endpoint_config.endpoint_config_name,
        )
        
        # add_dependency: Ensures the EndpointConfig is fully created BEFORE creating the Endpoint.
        self.sagemaker_endpoint.add_dependency(endpoint_config)

        # ----------------------------------------------------
        # 3. S3 Vectors Database (Cost-Optimized Vector DB)
        # ----------------------------------------------------
        # Standard vector databases (Pinecone, pgvector) run 24/7 and are expensive.
        # Instead, we use an S3 Vectors bucket which stores HNSW indexes directly on S3.
        # This costs cents per month and allows querying vectors serverlessly.

        # vectors_bucket: A CloudFormation S3 Vector Bucket resource.
        # This acts as our database database volume.
        self.vectors_bucket = s3vectors.CfnVectorBucket(
            self, 
            "VectorsBucket", 
            # vector_bucket_name: The physical S3 bucket name (templated with the AWS account ID for uniqueness).
            vector_bucket_name=f"finai-vectors-{self.account}"
        )

        # vectors_index: A CloudFormation Index resource.
        # This acts like a database table. It maps text documents to their vector representations.
        self.vectors_index = s3vectors.CfnIndex(
            self,
            "VectorsIndex",
            # vector_bucket_arn: References the Amazon Resource Name of the Vector Bucket created above.
            vector_bucket_arn=self.vectors_bucket.attr_vector_bucket_arn,
            # index_name: The physical table name.
            index_name="financial-research",
            # data_type: The binary format of floats (float32 uses 4 bytes per number).
            data_type="float32",
            # dimension: The number of numbers in the vector array (MiniLM generates 384 dimensions).
            dimension=384,
            # distance_metric: The formula used to calculate semantic similarity (cosine similarity).
            distance_metric="cosine",
        )
        
        # add_dependency: Ensures the Vector Bucket is created BEFORE creating the Index table.
        self.vectors_index.node.add_dependency(self.vectors_bucket)

        # ----------------------------------------------------
        # 4. Ingestion Pipeline Lambda Function Setup
        # ----------------------------------------------------
        # When the Researcher agent collects textual documents, they must be sent to the Ingest Lambda.
        # This function calls the SageMaker endpoint to get a 384-float vector, then saves it to S3 Vectors.

        # ingest_role: IAM Execution Role assigned to the Ingest Lambda.
        # Tells AWS what external services this Lambda function is allowed to talk to.
        ingest_role = iam.Role(
            self,
            "IngestLambdaRole",
            # assumed_by: Permits the AWS Lambda service (lambda.amazonaws.com) to assume this role.
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            # role_name: The physical name of the role.
            role_name="finai-ingest-lambda-role",
        )
        
        # add_managed_policy: Gives the Lambda standard execution permissions to write logs to CloudWatch.
        ingest_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )
        
        # add_to_policy: Inline policy giving permissions to invoke the SageMaker Serverless Endpoint.
        ingest_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sagemaker:InvokeEndpoint"],
                # resources: References the specific SageMaker Endpoint ARN we built in section 2.
                resources=[
                    f"arn:aws:sagemaker:{self.region}:{self.account}:endpoint/{self.sagemaker_endpoint.endpoint_name}"
                ],
            )
        )
        
        # add_to_policy: Inline policy giving permissions to read, write, query, and delete vectors in the S3 index.
        ingest_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3vectors:PutVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:DeleteVectors",
                ],
                # resources: Limits access to the specific Vector Bucket index path.
                resources=[
                    f"arn:aws:s3vectors:{self.region}:{self.account}:bucket/{self.vectors_bucket.vector_bucket_name}/index/*"
                ],
            )
        )

        # ingest_code_dir: Local directory containing the python script files for the ingestion function.
        # Resolves to `backend/ingest/` relative to the CDK project location.
        ingest_code_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backend", "ingest")
        )

        # ingest_code: Tells CDK to package the files in the directory as a zip and upload to S3 for deployment.
        ingest_code = _lambda.Code.from_asset(ingest_code_dir)

        # ingest_log_group: Dedicated CloudWatch Log Group to capture standard output/error logs.
        # Automatically destroyed when stack is deleted, with retention set to 1 week.
        ingest_log_group = logs.LogGroup(
            self,
            "IngestLambdaLogs",
            # log_group_name: Physical name in CloudWatch matching Lambda standard patterns.
            log_group_name="/aws/lambda/finai-ingest",
            # retention: Automatically deletes logs older than 7 days to keep CloudWatch costs low.
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ingest_lambda: The ingestion Lambda function resource itself.
        self.ingest_lambda = _lambda.Function(
            self,
            "IngestLambda",
            # function_name: Physical name of the Lambda function in the AWS console.
            function_name="finai-ingest",
            # runtime: Defines the runtime context environment (Python 3.12).
            runtime=_lambda.Runtime.PYTHON_3_12,
            # handler: Entrypoint in code, maps to `lambda_handler` function in `ingest_s3vectors.py`.
            handler="ingest_s3vectors.lambda_handler",
            # code: Loads the packaged zip asset we prepared above.
            code=ingest_code,
            # role: Assigns the IAM execution role we built above.
            role=ingest_role,
            # timeout: Maximum execution time allowed before AWS cuts off the function (60 seconds).
            timeout=Duration.seconds(60),
            # memory_size: RAM allocation (512 MB). Scales CPU capacity proportionally.
            memory_size=512,
            # environment: Environment variables accessible inside python via `os.environ`.
            environment={
                # VECTOR_BUCKET: The physical name of the S3 vectors bucket target.
                "VECTOR_BUCKET": self.vectors_bucket.vector_bucket_name,
                # SAGEMAKER_ENDPOINT: The physical name of the SageMaker embeddings endpoint.
                "SAGEMAKER_ENDPOINT": self.sagemaker_endpoint.endpoint_name,
            },
            # log_group: Hooks this function to the dedicated CloudWatch Log Group we created.
            log_group=ingest_log_group,
        )

        # ----------------------------------------------------
        # 5. Researcher Lambda Agent (Automated Local Docker Build)
        # ----------------------------------------------------
        # The Researcher Agent runs a Python script that boots a Playwright browser to scrape financial sites.
        # Because Playwright and Chromium are too large for standard zip files, we deploy the Lambda as a Docker container.

        # researcher_role: IAM Execution Role assigned to the Researcher Lambda.
        researcher_role = iam.Role(
            self,
            "ResearcherLambdaRole",
            # assumed_by: Permits the AWS Lambda service (lambda.amazonaws.com) to assume this role.
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            # role_name: The physical name of the role.
            role_name="finai-researcher-lambda-role",
        )
        
        # add_managed_policy: Standard CloudWatch logging execution permissions.
        researcher_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        # add_to_policy: Inline policy giving Bedrock invoke model permissions.
        # This allows the Lambda function to make calls to LLM foundation models (e.g. kimi).
        researcher_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ListFoundationModels",
                ],
                # resources: '*' allows invoking any active foundation models configured in the account.
                resources=["*"],
            )
        )

        # add_to_policy: Inline policy giving S3 Vectors querying permissions.
        # This allows the Researcher function to fetch and match vectors during research runs.
        researcher_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3vectors:QueryVectors", "s3vectors:GetVectors"],
                # resources: Restricts access to the financial-research index of our Vector Bucket.
                resources=[
                    f"arn:aws:s3vectors:{self.region}:{self.account}:bucket/{self.vectors_bucket.vector_bucket_name}/index/*"
                ],
            )
        )

        # add_to_policy: Inline policy giving permission to invoke the Ingestion Lambda function.
        # This allows the Researcher agent to trigger ingestion directly when new data is parsed.
        researcher_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                # resources: References the ARN of the Ingestion Lambda function.
                resources=[self.ingest_lambda.function_arn],
            )
        )

        # researcher_log_group: Dedicated CloudWatch Log Group for researcher outputs.
        # Retains logs for 1 week and gets cleaned up on stack deletion.
        researcher_log_group = logs.LogGroup(
            self,
            "ResearcherLogs",
            # log_group_name: Physical name matching Lambda standard path patterns.
            log_group_name="/aws/lambda/finai-researcher",
            # retention: Keeps log files for a maximum of 7 days.
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # researcher_code_dir: Local directory containing the Researcher Dockerfile and script codes.
        # Resolves to `backend/researcher/` relative to the CDK project location.
        researcher_code_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backend", "researcher")
        )

        # researcher_lambda: Docker-based Lambda function resource.
        # CDK will automatically trigger a local `docker build` using the Dockerfile in `researcher_code_dir`,
        # push the resulting image to an ECR repository, and deploy it to Lambda.
        self.researcher_lambda = _lambda.DockerImageFunction(
            self,
            "ResearcherLambda",
            # function_name: Physical name of the Lambda function in the AWS console.
            function_name="finai-researcher",
            # code: Tells CDK to compile the container image using the specified local directory context.
            code=_lambda.DockerImageCode.from_image_asset(researcher_code_dir),
            # role: Assigns the IAM execution role we built above.
            role=researcher_role,
            # timeout: Maximum execution time allowed before cutoff (300 seconds = 5 minutes).
            timeout=Duration.seconds(300),
            # memory_size: RAM allocation (2048 MB = 2 GB) needed to run Chromium headless.
            memory_size=2048,
            # ephemeral_storage_size: Ephemeral disk storage space (/tmp directory) allocated to the execution environment.
            # Playwright must download and extract chromium binaries dynamically on cold start, which exceed 512 MB.
            # We scale this to 2048 MB (2 GB) of ephemeral storage.
            ephemeral_storage_size=Size.mebibytes(2048),
            # environment: Environment variables accessible inside container python.
            environment={
                # INGEST_LAMBDA_NAME: The physical name of the ingestion pipeline lambda function.
                "INGEST_LAMBDA_NAME": ingest_lambda_name,
                # BEDROCK_REGION: The AWS region hosting Bedrock models.
                "BEDROCK_REGION": bedrock_region,
                # RESEARCHER_MODEL: The LLM model identifier string used by LiteLLM.
                "RESEARCHER_MODEL": researcher_model,
                # LANGFUSE_PUBLIC_KEY: Public key for Langfuse LLM monitoring.
                "LANGFUSE_PUBLIC_KEY": langfuse_public_key,
                # LANGFUSE_SECRET_KEY: Secret key for Langfuse LLM monitoring.
                "LANGFUSE_SECRET_KEY": langfuse_secret_key,
                # LANGFUSE_HOST: Host URL of the Langfuse server.
                "LANGFUSE_HOST": langfuse_host,
                # AWS_REGION_NAME: AWS region used by LiteLLM internally.
                "AWS_REGION_NAME": bedrock_region,
            },
            # log_group: Hooks this function to the dedicated CloudWatch Log Group we created.
            log_group=researcher_log_group,
        )

        # function_url: Generates a public HTTPS endpoint for the Researcher Lambda function.
        # This allows triggering the agent directly from external web hooks or web forms.
        self.function_url = self.researcher_lambda.add_function_url(
            # auth_type: 'NONE' means the endpoint is public and does not require AWS signature authentication.
            auth_type=_lambda.FunctionUrlAuthType.NONE
        )

        # ----------------------------------------------------
        # 6. Stack Outputs
        # ----------------------------------------------------
        # CloudFormation Stack outputs display configuration parameters in the terminal
        # and AWS console upon successful stack deployment.

        # ResearcherFunctionUrl: The public HTTPS URL of the Researcher Lambda function.
        CfnOutput(
            self,
            "ResearcherFunctionUrl",
            value=self.function_url.url,
            description="Public URL endpoint of the Researcher Lambda function",
        )

        # SageMakerEndpointName: The physical name of the SageMaker embeddings endpoint.
        CfnOutput(
            self,
            "SageMakerEndpointName",
            value=self.sagemaker_endpoint.endpoint_name,
            description="SageMaker serverless embeddings endpoint name",
        )
        
        # VectorsBucketName: The physical name of the S3 vectors bucket target.
        CfnOutput(
            self,
            "VectorsBucketName",
            value=self.vectors_bucket.vector_bucket_name,
            description="S3 Vectors bucket name",
        )
