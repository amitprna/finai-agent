#!/usr/bin/env python3
import os

import aws_cdk as cdk
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


from stacks.researcher_stack import ResearcherStack

app = cdk.App()

# Define target AWS account and region
aws_account = os.getenv("AWS_ACCOUNT_ID")
aws_region = os.getenv("DEFAULT_AWS_REGION", "us-east-1")

if not aws_account:
    aws_account = os.getenv("CDK_DEFAULT_ACCOUNT")

if not aws_region:
    aws_region = os.getenv("CDK_DEFAULT_REGION", "us-east-1")

env = cdk.Environment(account=aws_account, region=aws_region)

# Stack 1: Researcher & Vector Ingestion
ResearcherStack(app, "FinaiResearcherStack", env=env)


app.synth()
