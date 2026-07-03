import os
from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_secretsmanager as secretsmanager,
    aws_sqs as sqs,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_lambda_event_sources as event_sources,
)
from constructs import Construct

class DatabaseAgentsStack(Stack):
    """
    AWS CDK Stack deploying the database agents infrastructure.
    This stack provisions the database storage layers and downstream agents.
    It builds a serverless relational database (Aurora PostgreSQL) and deploys
    Docker-based Lambdas that query the DB to analyze user portfolios.
    """
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        """
        Constructor function for the CDK stack.
        
        Args:
            scope: The parent construct tree context (the App object).
            construct_id: Unique string identifier for this stack.
            kwargs: Extra parameters passed to stack properties.
        """
        super().__init__(scope, construct_id, **kwargs)

        # ----------------------------------------------------
        # 1. Database Infrastructure Setup
        # ----------------------------------------------------
        # The database needs to sit inside a Virtual Private Cloud (VPC) network.
        # Instead of creating a new VPC (which is slow and costs money for NAT gateways),
        # we look up the AWS account's pre-existing default VPC.
        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        # db_secret: An AWS Secrets Manager secret.
        # This securely stores and auto-generates credentials (username and password)
        # for our Aurora PostgreSQL administrative user, preventing hardcoded credentials.
        self.db_secret = secretsmanager.Secret(
            self, "DbSecret",
            # secret_name: Physical name of the secret in AWS Secrets Manager.
            secret_name="finai-aurora-credentials",
            # generate_secret_string: Tells AWS to automatically generate a random administrative password.
            generate_secret_string=secretsmanager.SecretStringGenerator(
                # secret_string_template: JSON template that will hold the generated password.
                secret_string_template='{"username": "finaiadmin"}',
                # generate_string_key: Key inside the JSON that receives the generated password string.
                generate_string_key="password",
                # exclude_characters: Characters excluded from the password to avoid SQL connection string escapes.
                exclude_characters='"@/\\'
            ),
            # removal_policy: Destroy means the secret will be deleted immediately when the stack is torn down.
            removal_policy=RemovalPolicy.DESTROY
        )

        # sg: Security Group (virtual firewall) for the Aurora Serverless DB.
        # Restricts network access so only allowed resources (like our Lambda functions)
        # can talk to the PostgreSQL port.
        sg = ec2.SecurityGroup(
            self, "AuroraSG",
            # vpc: Deploys this firewall inside the default VPC we looked up.
            vpc=vpc,
            # security_group_name: Physical name of the group.
            security_group_name="finai-aurora-sg",
            # description: User-friendly description of this firewall's purpose.
            description="Security group for Finai Aurora cluster",
            # allow_all_outbound: Allows resources behind this firewall to make outbound queries (like fetching packages).
            allow_all_outbound=True
        )
        
        # add_ingress_rule: Configures an incoming firewall rule.
        # This rule permits TCP connections on port 5432 (PostgreSQL default port)
        # from any resource running within the VPC's internal network range.
        sg.add_ingress_rule(
            # Peer.ipv4: Limits access to traffic coming from within the VPC's internal CIDR block range.
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            # Port.tcp: Standard PostgreSQL connection port.
            ec2.Port.tcp(5432),
            # description: Explanation of the ingress rule's purpose.
            "Allow PostgreSQL access from within VPC CIDR block"
        )

        # Configure serverless capacity settings using environment variables.
        # ACUs (Aurora Capacity Units) measure combined CPU and RAM: 1 ACU is approx 2GB RAM.
        # Min capacity is set to 0.5 ACUs to minimize idle base pricing.
        min_capacity = float(os.getenv("MIN_CAPACITY", "0.5"))
        # Max capacity is set to 2.0 ACUs (approx 4GB RAM) to allow scaling under load.
        max_capacity = float(os.getenv("MAX_CAPACITY", "2.0"))

        # cluster: The Aurora Serverless v2 PostgreSQL database cluster.
        # This runs a fully-managed SQL database cluster that automatically scales up
        # during peak analysis queries and down during idle hours.
        self.cluster = rds.DatabaseCluster(
            self, "AuroraCluster",
            # cluster_identifier: Physical identifier in the RDS console.
            cluster_identifier="finai-aurora-cluster",
            # engine: Specifies PostgreSQL version 15.12 as the cluster engine.
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.of("15.12", "15")
            ),
            # credentials: Links database credentials to the secret we created in Secrets Manager above.
            credentials=rds.Credentials.from_secret(self.db_secret),
            # default_database_name: The name of the SQL database created on startup.
            default_database_name="finai",
            # vpc: Deploys the database nodes inside the default VPC.
            vpc=vpc,
            # vpc_subnets: Configures the subnet placements.
            # Public subnets keep deployment simple and free of NAT gateway costs.
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC
            ),
            # security_groups: Attaches the firewall security group we configured above.
            security_groups=[sg],
            # serverless_v2_min_capacity: Minimum capacity scaling limit.
            serverless_v2_min_capacity=min_capacity,
            # serverless_v2_max_capacity: Maximum capacity scaling limit.
            serverless_v2_max_capacity=max_capacity,
            # writer: Defines the serverless database node instance.
            writer=rds.ClusterInstance.serverless_v2(
                id="WriterInstance",
                instance_identifier="finai-aurora-instance-1"
            ),
            # enable_data_api: Enables RDS Data API HTTP connections.
            # This allows Python code to execute SQL queries using simple boto3 HTTPS calls,
            # completely removing the need to manage persistent VPC connection pools inside Lambda functions.
            enable_data_api=True,
            # removal_policy: Destroy deletes the database volume when the stack is destroyed.
            removal_policy=RemovalPolicy.DESTROY
        )

        # ----------------------------------------------------
        # 2. Agents Orchestration SQS Queues Setup
        # ----------------------------------------------------
        # SQS (Simple Queue Service) queues allow us to schedule analysis jobs asynchronously.
        # This decouples client triggers from execution, providing resilient processing pools.

        # dlq: Dead Letter Queue.
        # If an analysis job fails 3 times consecutively, it is moved here
        # for debugging and diagnostic review instead of clogging the main queue.
        self.dlq = sqs.Queue(
            self, "AnalysisJobsDlq",
            # queue_name: Physical name of the Dead Letter Queue.
            queue_name="finai-analysis-jobs-dlq",
            removal_policy=RemovalPolicy.DESTROY
        )

        # queue: The main SQS queue for scheduled analysis runs.
        # The frontend pushes job payloads containing user IDs here.
        # This queue triggers the Planner/Orchestrator Lambda function to start.
        self.queue = sqs.Queue(
            self, "AnalysisJobsQueue",
            # queue_name: Physical name of the main queue.
            queue_name="finai-analysis-jobs",
            # visibility_timeout: Time in seconds a message remains hidden after a consumer pulls it.
            # Must exceed the Planner Lambda's execution timeout (900s) to prevent duplicate processing.
            visibility_timeout=Duration.seconds(910),
            # receive_message_wait_time: Enables long-polling (10 seconds) to reduce API request costs.
            receive_message_wait_time=Duration.seconds(10),
            # dead_letter_queue: Configures retry policies.
            dead_letter_queue=sqs.DeadLetterQueue(
                # max_receive_count: Number of times a message can fail before being moved to the DLQ.
                max_receive_count=3,
                queue=self.dlq
            ),
            removal_policy=RemovalPolicy.DESTROY
        )

        # ----------------------------------------------------
        # 3. IAM Permissions Role for Lambda Agents
        # ----------------------------------------------------
        # Rather than managing unique roles for each agent, we build a single shared IAM Role.
        # This role grants the permissions needed for our agents to talk to other AWS resources.

        # agents_role: The IAM Role assumed by the Lambda functions.
        # This grants our Python code permission to access Bedrock, SageMaker, S3, SQS, and RDS.
        self.agents_role = iam.Role(
            self, "LambdaAgentsRole",
            # assumed_by: Permits the AWS Lambda service (lambda.amazonaws.com) to assume this role.
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            # role_name: Physical name of the role in the AWS console.
            role_name="finai-lambda-agents-role"
        )
        
        # add_managed_policy: Attaches the standard AWS policy allowing Lambdas to write basic logs.
        self.agents_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )

        # add_to_policy: Inline policy giving permissions to pull, delete, and view messages in SQS.
        # Allows our Lambda functions to process job items from our SQS queue.
        self.agents_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes"
                ],
                # resources: Limits permissions to our main SQS queue ARN.
                resources=[self.queue.queue_arn]
            )
        )

        # add_to_policy: Inline policy giving permissions to invoke lambda functions.
        # This allows our Planner (Orchestrator) function to call downstream agents directly.
        self.agents_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                # resources: Restricts invocation to functions matching the 'finai-' prefix.
                resources=[f"arn:aws:lambda:{self.region}:{self.account}:function:finai-*"]
            )
        )

        # add_to_policy: Inline policy giving RDS Data API permissions.
        # Allows executing SQL queries against the Aurora Serverless PostgreSQL cluster.
        self.agents_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "rds-data:ExecuteStatement",
                    "rds-data:BatchExecuteStatement",
                    "rds-data:BeginTransaction",
                    "rds-data:CommitTransaction",
                    "rds-data:RollbackTransaction"
                ],
                # resources: Limits access to our specific database cluster ARN.
                resources=[self.cluster.cluster_arn]
            )
        )

        # add_to_policy: Inline policy giving database administrative credentials read permissions.
        # Allows our Python database client to load the administrative username and password at runtime.
        self.agents_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                # resources: Restricts secret retrieval to our database credentials secret ARN.
                resources=[self.db_secret.secret_arn]
            )
        )

        # Configure variables for vector storage and AI models.
        # vector_bucket_name: The physical S3 vectors bucket containing our index databases.
        vector_bucket_name = os.getenv("VECTOR_BUCKET", f"finai-vectors-{self.account}")
        # sagemaker_endpoint_name: The physical name of the SageMaker embeddings endpoint.
        sagemaker_endpoint_name = os.getenv("SAGEMAKER_ENDPOINT", "finai-embedding-endpoint")
        # bedrock_model_id: The foundational LLM model identifier string.
        bedrock_model_id = os.getenv("BEDROCK_MODEL_ID", "moonshotai.kimi-k2.5")
        # bedrock_region: The AWS region hosting Bedrock models.
        bedrock_region = os.getenv("BEDROCK_REGION", "us-east-1")

        # add_to_policy: Inline policy giving read permissions to our vectors S3 bucket.
        # Allows the agents to download index files when querying.
        self.agents_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetObject",
                    "s3:ListBucket"
                ],
                # resources: Allows reading the bucket and any nested directories within it.
                resources=[
                    f"arn:aws:s3:::{vector_bucket_name}",
                    f"arn:aws:s3:::{vector_bucket_name}/*"
                ]
            )
        )

        # add_to_policy: Inline policy giving query permissions to the S3 vectors indexes.
        # Allows the agents to perform vector similarity lookups during research runs.
        self.agents_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3vectors:QueryVectors",
                    "s3vectors:GetVectors"
                ],
                # resources: Restricts permissions to vector index paths inside the vectors bucket.
                resources=[
                    f"arn:aws:s3vectors:{self.region}:{self.account}:bucket/{vector_bucket_name}/index/*"
                ]
            )
        )

        # add_to_policy: Inline policy giving permission to invoke the SageMaker Embeddings Endpoint.
        # This allows our agents to fetch text embeddings dynamically at runtime.
        self.agents_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["sagemaker:InvokeEndpoint"],
                # resources: Limits execution permission to the SageMaker embedding endpoint we configured.
                resources=[
                    f"arn:aws:sagemaker:{self.region}:{self.account}:endpoint/{sagemaker_endpoint_name}"
                ]
            )
        )

        # add_to_policy: Inline policy giving AWS Bedrock model invocation permissions.
        # This is needed to run all LLM completions during portfolio analysis.
        self.agents_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream"
                ],
                # resources: Allows invoking foundational models or inference profiles.
                resources=[
                    "arn:aws:bedrock:*::foundation-model/*",
                    "arn:aws:bedrock:*:*:inference-profile/*"
                ]
            )
        )

        # ----------------------------------------------------
        # 4. Lambda Agents Container Deployments
        # ----------------------------------------------------
        # We package each agent (planner, reporter, retirement, tagger, charter) in a Docker container.
        # This is because they depend on large ML libraries like LiteLLM and Pydantic.
        # Using containers allows us to bypass Lambda's standard 250MB size limit.

        # backend_dir: Local path referencing our `backend/` directory context.
        # Resolves dynamically relative to this CDK script location.
        backend_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backend")
        )

        # shared_env: Environment variables passed to every agent container.
        # Maps database cluster ARNs, secrets, model targets, and Langfuse tracing keys.
        shared_env = {
            "AURORA_CLUSTER_ARN": self.cluster.cluster_arn,
            "AURORA_SECRET_ARN": self.db_secret.secret_arn,
            "DATABASE_NAME": "finai",
            "AURORA_DATABASE": "finai",
            "BEDROCK_MODEL_ID": bedrock_model_id,
            "BEDROCK_REGION": bedrock_region,
            "AWS_REGION_NAME": bedrock_region,   # Required by LiteLLM internally
            "DEFAULT_AWS_REGION": self.region,
            "LANGFUSE_PUBLIC_KEY": os.getenv("LANGFUSE_PUBLIC_KEY", ""),
            "LANGFUSE_SECRET_KEY": os.getenv("LANGFUSE_SECRET_KEY", ""),
            "LANGFUSE_HOST": os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        }

        def make_docker_agent(
            logical_id: str,
            function_name: str,
            agent_name: str,
            log_group_name: str,
            timeout_seconds: int = 300,
            memory_mb: int = 1024,
            extra_env: dict = None,
        ) -> _lambda.DockerImageFunction:
            """
            CDK helper function to build Docker-based Lambda functions.
            
            Args:
                logical_id: Unique logical ID for the CDK construct.
                function_name: Physical name of the Lambda function in AWS.
                agent_name: Build argument identifying which folder (e.g. 'planner') to copy.
                log_group_name: Physical log group path in CloudWatch.
                timeout_seconds: Maximum time the function is allowed to run before timeout.
                memory_mb: Memory size allocated to the execution container.
                extra_env: Optional custom environment variables for the agent.
                
            Returns:
                An AWS Lambda DockerImageFunction construct.
            """
            # Create a dedicated CloudWatch Log Group with 1 week retention.
            log_group = logs.LogGroup(
                self, f"{logical_id}Logs",
                log_group_name=log_group_name,
                retention=logs.RetentionDays.ONE_WEEK,
                removal_policy=RemovalPolicy.DESTROY
            )

            # Assemble environment variables map
            env = shared_env.copy()
            if extra_env:
                env.update(extra_env)

            # Return the Lambda function configuration.
            # CDK triggers a local `docker build` using `Dockerfile.lambda` in `backend_dir`,
            # passing the `AGENT_NAME` build argument to copy only the target agent's code.
            return _lambda.DockerImageFunction(
                self, logical_id,
                function_name=function_name,
                code=_lambda.DockerImageCode.from_image_asset(
                    directory=backend_dir,
                    file="Dockerfile.lambda",
                    build_args={"AGENT_NAME": agent_name},
                ),
                role=self.agents_role,
                timeout=Duration.seconds(timeout_seconds),
                memory_size=memory_mb,
                environment=env,
                log_group=log_group,
            )

        # A. Planner (Orchestrator Agent)
        # Timeout is set to the maximum (15 minutes = 900 seconds) because the Planner
        # coordinates and waits for downstream sub-agents to complete.
        self.planner = make_docker_agent(
            logical_id="PlannerLambda",
            function_name="finai-planner",
            agent_name="planner",
            log_group_name="/aws/lambda/finai-planner",
            timeout_seconds=900,
            memory_mb=2048,
            extra_env={
                "VECTOR_BUCKET": vector_bucket_name,
                "SAGEMAKER_ENDPOINT": sagemaker_endpoint_name,
            },
        )

        # add_event_source: Configures the main SQS queue as a trigger for the Planner.
        # batch_size=1 means the Lambda will process one message at a time to prevent concurrency spikes.
        self.planner.add_event_source(
            event_sources.SqsEventSource(self.queue, batch_size=1)
        )

        # B. Tagger Agent
        # Classifies instruments (tickers) and updates their sector/region profiles in the database.
        self.tagger = make_docker_agent(
            logical_id="TaggerLambda",
            function_name="finai-tagger",
            agent_name="tagger",
            log_group_name="/aws/lambda/finai-tagger",
        )

        # C. Reporter Agent
        # Generates detailed markdown reports based on user portfolios and retirement goals.
        self.reporter = make_docker_agent(
            logical_id="ReporterLambda",
            function_name="finai-reporter",
            agent_name="reporter",
            log_group_name="/aws/lambda/finai-reporter",
            extra_env={"SAGEMAKER_ENDPOINT": sagemaker_endpoint_name},
        )

        # D. Charter Agent
        # Generates structured JSON chart definitions (Plotly specs) for the Streamlit dashboard.
        self.charter = make_docker_agent(
            logical_id="CharterLambda",
            function_name="finai-charter",
            agent_name="charter",
            log_group_name="/aws/lambda/finai-charter",
        )

        # E. Retirement Specialist Agent
        # Runs Monte Carlo simulations to calculate the success probability of a retirement plan.
        self.retirement = make_docker_agent(
            logical_id="RetirementLambda",
            function_name="finai-retirement",
            agent_name="retirement",
            log_group_name="/aws/lambda/finai-retirement",
        )

        # ----------------------------------------------------
        # 5. Stack Outputs
        # ----------------------------------------------------
        # Display key configuration settings in the console upon deployment.
        
        # AuroraClusterArn: The database cluster ARN.
        CfnOutput(
            self, "AuroraClusterArn",
            value=self.cluster.cluster_arn,
            description="ARN of the Aurora Serverless v2 PostgreSQL database cluster"
        )

        # AuroraClusterEndpoint: The SQL endpoint hostname.
        CfnOutput(
            self, "AuroraClusterEndpoint",
            value=self.cluster.cluster_endpoint.hostname,
            description="Writer endpoint for SQL connection strings"
        )

        # AuroraSecretArn: The Secrets Manager ARN.
        CfnOutput(
            self, "AuroraSecretArn",
            value=self.db_secret.secret_arn,
            description="ARN of database credentials secret"
        )

        # SqsQueueUrl: The main SQS queue URL.
        CfnOutput(
            self, "SqsQueueUrl",
            value=self.queue.queue_url,
            description="URL of SQS queue triggering job analysis runs"
        )

        # SqsQueueArn: The main SQS queue ARN.
        CfnOutput(
            self, "SqsQueueArn",
            value=self.queue.queue_arn,
            description="ARN of SQS queue triggering job analysis runs"
        )

        # LambdaRoleArn: The execution IAM Role ARN.
        CfnOutput(
            self, "LambdaRoleArn",
            value=self.agents_role.role_arn,
            description="ARN of the shared IAM Execution Role assigned to Lambda functions"
        )
