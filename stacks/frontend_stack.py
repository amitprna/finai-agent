import os
from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_apigatewayv2 as apigw2,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_logs as logs,
    aws_cognito as cognito,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
)
from constructs import Construct

class FrontendStack(Stack):
    """
    CDK Infrastructure Stack for the Frontend & API Gateway Layer.
    This stack deploys Cognito for user authentication, runs a Streamlit frontend
    inside AWS ECS Fargate, launches a FastAPI backend on AWS Lambda, configures
    AWS API Gateway to expose the API, and puts everything behind a unified
    AWS CloudFront CDN to resolve CORS issues and host both under a single domain.
    """
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str, 
        **kwargs
    ) -> None:
        """
        Constructor function for the Frontend and API Gateway Stack.
        
        Args:
            scope: The parent CDK Construct, usually the App class.
            construct_id: A unique ID for the construct.
            kwargs: Extra parameters passed to stack properties.
        """
        super().__init__(scope, construct_id, **kwargs)

        # ----------------------------------------------------
        # 1. Load Resource Configuration Variables
        # ----------------------------------------------------
        # Retrieve resource configuration values from environment variables or use fallback defaults.
        # This keeps the infrastructure definition customizable without editing CDK code.
        aurora_cluster_arn = os.getenv("AURORA_CLUSTER_ARN", f"arn:aws:rds:{self.region}:{self.account}:cluster:finai-aurora-cluster")
        aurora_secret_arn = os.getenv("AURORA_SECRET_ARN", f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:finai-aurora-credentials-dummy")
        sqs_queue_url = os.getenv("SQS_QUEUE_URL", f"https://sqs.{self.region}.amazonaws.com/{self.account}/finai-analysis-jobs")
        sqs_queue_arn = f"arn:aws:sqs:{self.region}:{self.account}:finai-analysis-jobs"
        
        # Deterministic Lambda ARNs based on fixed names deployed in DatabaseAgentsStack
        planner_lambda_arn = f"arn:aws:lambda:{self.region}:{self.account}:function:finai-planner"
        tagger_lambda_arn = f"arn:aws:lambda:{self.region}:{self.account}:function:finai-tagger"
        reporter_lambda_arn = f"arn:aws:lambda:{self.region}:{self.account}:function:finai-reporter"
        charter_lambda_arn = f"arn:aws:lambda:{self.region}:{self.account}:function:finai-charter"
        retirement_lambda_arn = f"arn:aws:lambda:{self.region}:{self.account}:function:finai-retirement"
        
        # ----------------------------------------------------
        # 2. Cognito User Directory & Client Setup
        # ----------------------------------------------------
        # Cognito provides fully-managed user authentication, sign-ups, and password management.
        
        # user_pool: The central Cognito directory storing our users' credentials.
        user_pool = cognito.UserPool(
            self, "FinaiUserPool",
            user_pool_name="finai-user-pool",
            # self_sign_up_enabled: Permits public sign-ups on our streamlit login page else only admin can create.
            self_sign_up_enabled=True,
            # sign_in_aliases: Users can sign in using either their username or email address.
            sign_in_aliases=cognito.SignInAliases(username=True, email=True),
            # auto_verify: Automatically marks email as verified when they sign up.
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            # removal_policy: Deletes the pool when the CDK stack is destroyed.
            removal_policy=RemovalPolicy.DESTROY
        )

        # pre_sign_up_trigger: A helper Lambda trigger that auto-confirms user registrations.
        # students can register and immediately log in
        # without needing to configure AWS Simple Email Service (SES) to send verification emails.
        pre_sign_up_trigger = _lambda.Function(
            self, "PreSignUpTrigger",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=_lambda.Code.from_inline(
                "def handler(event, context):\n"
                "    event['response']['autoConfirmUser'] = True\n"
                "    return event"
            )
        )
        user_pool.add_trigger(cognito.UserPoolOperation.PRE_SIGN_UP, pre_sign_up_trigger)

        # user_pool_client: Exposes Cognito authentication flows to the Streamlit frontend.
        user_pool_client = cognito.UserPoolClient(
            self, "FinaiUserPoolClient",
            user_pool=user_pool,
            user_pool_client_name="finai-app-client",
            auth_flows=cognito.AuthFlow(
                user_srp=True,        # Secure Remote Password protocol (no plain-text passwords sent over network)
                user_password=True    # Simple username/password authentication flow
            )
        )

        # ----------------------------------------------------
        # 3. ECS Fargate Container Service (Streamlit Frontend)
        # ----------------------------------------------------
        # Streamlit requires a persistent server connection (WebSocket).
        # We containerize it and run it inside AWS Elastic Container Service (ECS) using AWS Fargate.
        
        # Look up default VPC - avoids creating a new VPC and NAT gateways ($32/month saving).
        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        # ecs_cluster: Logical boundary inside ECS where our container tasks run.
        self.ecs_cluster = ecs.Cluster(
            self, "FinaiEcsCluster",
            vpc=vpc,
            cluster_name="finai-ecs-cluster"
        )

        # frontend_image: Compiles the local frontend code into a Docker image automatically.
        frontend_image = ecs.ContainerImage.from_asset(
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend"))
        )

        # fargate_service: Sets up our Streamlit container, registers it with an Application Load Balancer,
        # and configures public IP allocations inside the default VPC.
        self.fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "FinaiFrontendService",
            cluster=self.ecs_cluster,
            service_name="finai-frontend-service",
            cpu=256,              # 0.25 vCPUs (sufficient for Streamlit)
            memory_limit_mib=512, # 512 MB RAM
            desired_count=1,      # Run exactly 1 instance
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=frontend_image,
                container_port=8501, # Default port where Streamlit runs
                environment={
                    "COGNITO_USER_POOL_ID": user_pool.user_pool_id,
                    "COGNITO_CLIENT_ID": user_pool_client.user_pool_client_id,
                    "COGNITO_REGION": self.region,
                    "DEFAULT_AWS_REGION": self.region,
                }
            ),
            public_load_balancer=True,
            assign_public_ip=True,
        )

        # set_attribute: Configures the Application Load Balancer timeout to 1800 seconds (30 minutes).
        # This is critical for Streamlit because it communicates via persistent WebSockets;
        # otherwise, AWS drops connections every 60 seconds of idle time.
        self.fargate_service.load_balancer.set_attribute("idle_timeout.timeout_seconds", "1800")

        # ----------------------------------------------------
        # 4. Backend FastAPI Lambda Permissions & IAM Role
        # ----------------------------------------------------
        # The backend runs serverlessly on AWS Lambda. It needs IAM policies to query
        # Aurora Serverless PostgreSQL, trigger SQS analysis jobs, and invoke sub-agents.
        api_role = iam.Role(
            self, "ApiLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            role_name="finai-api-lambda-role"
        )
        api_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )

        # Relational database access policies via RDS Data API
        api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "rds-data:ExecuteStatement",
                    "rds-data:BatchExecuteStatement",
                    "rds-data:BeginTransaction",
                    "rds-data:CommitTransaction",
                    "rds-data:RollbackTransaction"
                ],
                resources=[aurora_cluster_arn]
            )
        )
        # Secrets Manager access to read DB credentials
        api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[aurora_secret_arn]
            )
        )

        # SQS access to trigger analysis jobs asynchronously
        api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "sqs:SendMessage",
                    "sqs:GetQueueAttributes"
                ],
                resources=[sqs_queue_arn]
            )
        )

        # Lambda invoke permissions to trigger sub-agents directly if needed
        api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                resources=[
                    planner_lambda_arn,
                    tagger_lambda_arn,
                    reporter_lambda_arn,
                    charter_lambda_arn,
                    retirement_lambda_arn
                ]
            )
        )

        # ----------------------------------------------------
        # 5. Backend FastAPI Lambda Function Deployment
        # ----------------------------------------------------
        # Deploy the backend FastAPI app as a serverless Lambda function.
        # Fallback to a dummy inline handler if the deployment zip is not built.
        zip_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../backend/api/api_lambda.zip")
        )

        lambda_code = _lambda.Code.from_asset(zip_path)

        api_log_group = logs.LogGroup(
            self, "ApiLambdaLogs",
            log_group_name="/aws/lambda/finai-api",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )

        self.api_lambda = _lambda.Function(
            self, "ApiLambda",
            function_name="finai-api",
            runtime=_lambda.Runtime.PYTHON_3_12,
            # handler: Entrypoint inside python, mapping to 'handler' inside lambda_handler.py
            handler="lambda_handler.handler",
            code=lambda_code,
            role=api_role,
            # timeout: Timeout set to 30 seconds for typical REST API operations
            timeout=Duration.seconds(30),
            memory_size=512,
            environment={
                "AURORA_CLUSTER_ARN": aurora_cluster_arn,
                "AURORA_SECRET_ARN": aurora_secret_arn,
                "AURORA_DATABASE": "finai",
                "DEFAULT_AWS_REGION": self.region,
                "SQS_QUEUE_URL": sqs_queue_url,
                "COGNITO_USER_POOL_ID": user_pool.user_pool_id,
                "COGNITO_CLIENT_ID": user_pool_client.user_pool_client_id,
                "COGNITO_REGION": self.region,
                "LANGFUSE_PUBLIC_KEY": os.getenv("LANGFUSE_PUBLIC_KEY", ""),
                "LANGFUSE_SECRET_KEY": os.getenv("LANGFUSE_SECRET_KEY", ""),
                "LANGFUSE_HOST": os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            },
            log_group=api_log_group
        )

        # ----------------------------------------------------
        # 6. HTTP API Gateway Setup
        # ----------------------------------------------------
        # Expose the backend FastAPI Lambda function via HTTP API Gateway.
        self.api_gw = apigw2.CfnApi(
            self, "ApiGateway",
            name="finai-api-gateway",
            protocol_type="HTTP",
            cors_configuration=apigw2.CfnApi.CorsProperty(
                allow_headers=["authorization", "content-type", "x-amz-date", "x-api-key", "x-amz-security-token"],
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_origins=["*"]
            )
        )

        # stage: Default auto-deployed HTTP stage
        self.stage = apigw2.CfnStage(
            self, "ApiStage",
            api_id=self.api_gw.ref,
            stage_name="$default",
            auto_deploy=True,
            default_route_settings=apigw2.CfnStage.RouteSettingsProperty(
                throttling_burst_limit=100,
                throttling_rate_limit=100
            )
        )

        # integration: Connects the API Gateway to our FastAPI backend Lambda function
        self.integration = apigw2.CfnIntegration(
            self, "LambdaIntegration",
            api_id=self.api_gw.ref,
            integration_type="AWS_PROXY",
            integration_uri=self.api_lambda.function_arn,
            payload_format_version="2.0"
        )

        # routes: Route all incoming '/api/*' requests directly to our Lambda integration
        self.route_any = apigw2.CfnRoute(
            self, "RouteAny",
            api_id=self.api_gw.ref,
            route_key="ANY /api/{proxy+}",
            target=f"integrations/{self.integration.ref}"
        )

        self.route_options = apigw2.CfnRoute(
            self, "RouteOptions",
            api_id=self.api_gw.ref,
            route_key="OPTIONS /api/{proxy+}",
            target=f"integrations/{self.integration.ref}"
        )

        # add_permission: Authorizes the API Gateway service to invoke our API Lambda function
        self.api_lambda.add_permission(
            "AllowExecutionFromAPIGateway",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=f"arn:aws:execute-api:{self.region}:{self.account}:{self.api_gw.ref}/*/*"
        )

        # ----------------------------------------------------
        # 7. CloudFront CDN Unified Entrypoint (CORS Free)
        # ----------------------------------------------------
        # If the Streamlit frontend calls the backend on a different domain, we get CORS blocks.
        # CloudFront acts as a single secure origin:
        # - Default requests (e.g. '/') route to the Streamlit Fargate ALB.
        # - Requests starting with '/api/*' route to the API Gateway Endpoint.
        # Since both share the exact same domain, CORS errors are resolved naturally.
        api_domain = f"{self.api_gw.ref}.execute-api.{self.region}.amazonaws.com"

        self.distribution = cloudfront.Distribution(
            self, "CloudFrontDistribution",
            comment="Finai Financial Advisor Frontend Routing",
            # default_behavior: Routes standard web traffic to the Streamlit Load Balancer
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.LoadBalancerV2Origin(
                    self.fargate_service.load_balancer,
                    protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY
                ),
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER
            ),
            # additional_behaviors: Redirects '/api/*' traffic directly to API Gateway
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(api_domain),
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER
                )
            }
        )

        # ----------------------------------------------------
        # 8. Circular Dependency Resolution (Config injection)
        # ----------------------------------------------------
        # The Streamlit container needs to know the CloudFront URL to hit API endpoints.
        # Since CloudFront's URL is generated last, we dynamically inject it into the
        # Fargate Container and Lambda environments after the resources are created.
        self.fargate_service.task_definition.default_container.add_environment(
            "API_BASE_URL",
            f"https://{self.distribution.distribution_domain_name}"
        )

        self.api_lambda.add_environment(
            "CORS_ORIGINS",
            f"http://localhost:3000,https://{self.distribution.distribution_domain_name}"
        )

        # ----------------------------------------------------
        # 9. Stack Outputs
        # ----------------------------------------------------
        # Output URLs and IDs to the console upon successful deployment.
        
        # CloudFrontUrl: The master HTTPS domain that students use to access the app.
        CfnOutput(
            self, "CloudFrontUrl",
            value=f"https://{self.distribution.distribution_domain_name}",
            description="Master CloudFront URL for accessing the application"
        )

        CfnOutput(
            self, "ApiGatewayUrl",
            value=f"https://{api_domain}",
            description="Backend HTTP API Gateway URL"
        )

        CfnOutput(
            self, "LoadBalancerUrl",
            value=f"http://{self.fargate_service.load_balancer.load_balancer_dns_name}",
            description="ECS Streamlit Load Balancer hostname"
        )

        CfnOutput(
            self, "LambdaFunctionName",
            value=self.api_lambda.function_name,
            description="API Handler Lambda function name"
        )

        CfnOutput(
            self, "CognitoUserPoolId",
            value=user_pool.user_pool_id,
            description="Cognito User Pool ID"
        )

        CfnOutput(
            self, "CognitoUserPoolClientId",
            value=user_pool_client.user_pool_client_id,
            description="Cognito Client App ID"
        )
