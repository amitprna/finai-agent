#!/usr/bin/env python3
"""
AWS CDK Entrypoint Application
==============================
This script acts as the entrypoint launcher for AWS CDK.
It reads configuration variables from .env and instantiates the three 
infrastructure stacks that make up the FinAI wealth advisor ecosystem.
"""

import os
import aws_cdk as cdk
from dotenv import load_dotenv

# Load local environment configuration parameters from .env
load_dotenv()

# Import the three stacks defined in the stacks/ directory
from stacks.researcher_stack import ResearcherStack
from stacks.database_agents_stack import DatabaseAgentsStack
from stacks.frontend_stack import FrontendStack

# Initialize the primary CDK App wrapper
app = cdk.App()

# ----------------------------------------------------
# Target AWS Environment Configuration
# ----------------------------------------------------
# Retrieve targeted AWS account ID and region from environment configurations.
# Fallback to CDK default environment variables if target keys are not explicitly set.
aws_account = os.getenv("AWS_ACCOUNT_ID") or os.getenv("CDK_DEFAULT_ACCOUNT")
aws_region = os.getenv("DEFAULT_AWS_REGION") or os.getenv("CDK_DEFAULT_REGION", "us-east-1")

# Create a unified CDK environment parameter map
env = cdk.Environment(account=aws_account, region=aws_region)

# ----------------------------------------------------
# Infrastructure Stack Deployments
# ----------------------------------------------------

# Stack 1: Semantic Researcher & Vector Ingestion
# Provisions the Playwright scraping agent and the S3 semantic knowledge vector index.
ResearcherStack(
    app, "FinaiResearcherStack",
    env=env
)

# Stack 2: Database & Multi-Agent Orchestration
# Provisions the Aurora Serverless PostgreSQL cluster and the Docker-based agent lambdas.
DatabaseAgentsStack(
    app, "FinaiDatabaseAgentsStack",
    env=env
)

# Stack 3: Frontend Web Hosting & FastAPI Backend
# Provisions Cognito authentication, Streamlit on Fargate, FastAPI Lambda, and CloudFront.
FrontendStack(
    app, "FinaiFrontendStack",
    env=env
)

# Synthesize all cloudformation templates
app.synth()
