#!/usr/bin/env python3
"""
CDK Application Entrypoint Script
Boots up the AWS CDK App context, configures target account and region settings,
and instantiates the infrastructure stacks (e.g. ResearcherStack) for deployment.
"""

import os
import aws_cdk as cdk
from dotenv import load_dotenv

# ----------------------------------------------------
# Environment Configuration Loading
# ----------------------------------------------------
# Load environment settings from local .env files if present.
# This makes it easy to inject AWS account keys or custom region strings.
load_dotenv()

# Import the stack definition construct
from stacks.researcher_stack import ResearcherStack

# ----------------------------------------------------
# App Initialization & Environment Definition
# ----------------------------------------------------
# cdk.App: The primary construct tree node for any AWS CDK application.
# It acts as a root container for all stack objects deployed together.
app = cdk.App()

# Retrieve deployment targets from operating system environment variables
aws_account = os.getenv("AWS_ACCOUNT_ID")
aws_region = os.getenv("DEFAULT_AWS_REGION", "us-east-1")

# cdk.Environment: Explicitly defines the AWS account ID and region target
# where resources will be provisioned by CloudFormation.
env = cdk.Environment(account=aws_account, region=aws_region)

# ----------------------------------------------------
# Infrastructure Stack Instantiation
# ----------------------------------------------------
# ResearcherStack: Deploys the Playwright agent container environment,
# the serverless SageMaker embedding model, and the S3 vectors database index.
ResearcherStack(app, "FinaiResearcherStack", env=env)

# app.synth: Synthesizes CloudFormation template configuration JSONs.
# Converts our Python CDK classes into declarative templates ready for AWS deployment.
app.synth()
