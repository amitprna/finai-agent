"""
FinAI Backend - AWS Lambda Handler Entrypoint
=============================================
This file bridges the gap between AWS Lambda and our FastAPI ASGI application.
"""

from mangum import Mangum
from api.main import app

# ----------------------------------------------------
# AWS Lambda Handler Construction
# ----------------------------------------------------
# Mangum is an ASGI adapter that allows running ASGI applications (like FastAPI) inside AWS Lambda.
#
# How it works:
# 1. AWS API Gateway/ALB receives an HTTP request and wraps it in a JSON event dictionary.
# 2. AWS Lambda invokes this handler function, passing the event payload.
# 3. Mangum intercepts the event, translates the raw API Gateway/ALB JSON event into standard ASGI scopes,
#    and dispatches it directly to our FastAPI 'app' instance.
# 4. FastAPI handles routing and execution, returning a response.
# 5. Mangum takes the FastAPI response and wraps it back into API Gateway compatible proxy JSON structure.
#
# lifespan="off" is set to disable startup/shutdown event handlers since Lambda environments are serverless
# and ephemeral, which prevents unnecessary delays during function cold starts.
handler = Mangum(app, lifespan="off")