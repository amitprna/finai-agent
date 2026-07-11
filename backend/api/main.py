"""
FinAI - FastAPI Backend API Server
==================================
This application runs a FastAPI REST server that handles user dashboard actions.
It supports:
1. User registration/login profile syncing.
2. Portfolio Accounts CRUD operations (EPF/PPF, NPS, Demat cash accounts).
3. Positions CRUD operations (seeding, adding stock/ETFs, validation).
4. Running the Multi-Agent analysis pipeline asynchronously (either via local
   background threads or AWS SQS Queue executions in Lambda).
"""

import os
import sys
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from decimal import Decimal
import uuid

# ----------------------------------------------------
# 1. Sys.path setup for Module Imports
# ----------------------------------------------------
# Because this project runs in separate microservices folders, we dynamically add the 
# parent directory `backend` to sys.path. This allows FastAPI to import from sibling 
# packages (like the 'planner' orchestrator) during local executions.
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Import database connection objects and validation schemas from internal packages
from src import Database
from src.schemas import AccountCreate, PositionCreate, InstrumentCreate

# Load environment variables
load_dotenv(override=True)

# Set up backend logging parameters
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------------------------------
# 2. FastAPI Application Initialization
# ----------------------------------------------------
app = FastAPI(
    title="FinAI API",
    description="Backend API Server for FinAI - AI-powered portfolio advisor",
    version="1.0.0"
)

# Enable CORS (Cross-Origin Resource Sharing) middleware.
# This permits our Streamlit container running on Fargate (or locally) to securely execute
# AJAX/fetch requests against this API from the user's web browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------
# 3. Cognito User JWT Token Verification (Security Guard)
# ----------------------------------------------------
# Why do we need python-jose?
# Users authenticate with AWS Cognito on the frontend. Once signed in, Cognito issues a
# cryptographically signed JSON Web Token (JWT). The frontend includes this token in the
# 'Authorization' header of every request.
#
# We use python-jose (jose module) to:
# 1. Parse JWT headers to extract the Key ID ('kid') that signed the token.
# 2. Reconstruct the Cognito RSA public key using standard jwk specifications.
# 3. Cryptographically verify the token signature (proving the token is authentic).
# 4. Check token claims such as expiration time ('exp') and audience ('aud').
# 5. Safely decode the token payload to retrieve the user's Cognito UUID ('sub').
import time
from jose import jwt, jwk
from jose.utils import base64url_decode
import httpx
from pydantic import ConfigDict

class CognitoCredentials(HTTPAuthorizationCredentials):
    """Extends standard bearer credentials to cache verified claims metadata."""
    model_config = ConfigDict(extra='allow')
    decoded: dict = {}

# MOCK_AUTH allows developers to run and test the application locally without deploying Cognito
MOCK_AUTH = os.getenv("MOCK_AUTH", "false").lower() == "true"

class CognitoGuard(HTTPBearer):
    """
    Security guard that intercepts incoming request headers, extracts Cognito JWT
    tokens, fetches public JWKS certificates from AWS, and verifies signatures.
    """
    def __init__(self, auto_error: bool = True):
        super().__init__(auto_error=auto_error)
        self.user_pool_id = os.getenv("COGNITO_USER_POOL_ID", "")
        self.client_id = os.getenv("COGNITO_CLIENT_ID", "")
        self.region = os.getenv("COGNITO_REGION") or os.getenv("DEFAULT_AWS_REGION", "us-east-1")
        # JWKS URL: holds public signing keys used by AWS Cognito to sign JWTs
        self.jwks_url = f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}/.well-known/jwks.json"
        self.issuer = f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"
        self._keys = None

    async def _get_keys(self):
        """Fetch and cache public signing keys from AWS Cognito endpoint."""
        if self._keys is None and self.user_pool_id:
            try:
                # We perform an async GET call to Cognito to load the keys
                async with httpx.AsyncClient() as client:
                    r = await client.get(self.jwks_url)
                    if r.status_code == 200:
                        self._keys = r.json().get("keys", [])
                    else:
                        logger.error(f"Failed to fetch JWKS from {self.jwks_url}: {r.status_code}")
            except Exception as e:
                logger.error(f"Error fetching JWKS: {e}")
        return self._keys or []

    async def __call__(self, request: Request) -> Optional[HTTPAuthorizationCredentials]:
        # If running in local Mock mode, skip AWS Cognito verification checks
        if MOCK_AUTH:
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.split(" ")[1] if auth_header.startswith("Bearer ") else "test_user_001"
            
            sub = token
            name = "Mock User"
            # If the mock token looks like a valid JWT token structure, decode the claims
            if len(token) > 50 and token.count(".") == 2:
                try:
                    claims = jwt.get_unverified_claims(token)
                    sub = claims.get("sub", token)
                    name = claims.get("name") or claims.get("cognito:username") or claims.get("email", "").split("@")[0] or "Mock Cognito User"
                except Exception as e:
                    logger.warning(f"Mock auth parsing failed: {e}")
            
            if len(sub) > 255:
                sub = sub[:255]
                
            creds = CognitoCredentials(scheme="Bearer", credentials=token)
            creds.decoded = {"sub": sub, "name": name}
            return creds

        # Retrieve bearer token from Authorization headers
        creds_parent = await super().__call__(request)
        if not creds_parent:
            raise HTTPException(status_code=401, detail="Missing authorization header")
        
        creds = CognitoCredentials(scheme=creds_parent.scheme, credentials=creds_parent.credentials)
        token = creds.credentials
        try:
            # 1. Parse JWT header (using python-jose) to find which public key signed the token
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")
            if not kid:
                raise HTTPException(status_code=401, detail="Missing key ID in token header")

            # 2. Match token kid against active Cognito public keys
            keys = await self._get_keys()
            key_data = next((k for k in keys if k["kid"] == kid), None)
            if not key_data:
                # Force refresh JWKS keys and check again
                self._keys = None
                keys = await self._get_keys()
                key_data = next((k for k in keys if k["kid"] == kid), None)
                if not key_data:
                    raise HTTPException(status_code=401, detail="Public key not found in JWKS")

            # 3. Decrypt and verify signature
            # We construct the public RSA key using python-jose's jwk constructor, then verify the signature
            public_key = jwk.construct(key_data)
            message, encoded_sig = token.rsplit('.', 1)
            decoded_sig = base64url_decode(encoded_sig.encode('utf-8'))
            if not public_key.verify(message.encode("utf-8"), decoded_sig):
                raise HTTPException(status_code=401, detail="Signature verification failed")
            
            # 4. Check token expiration
            claims = jwt.get_unverified_claims(token)
            if claims.get("exp", 0) < time.time():
                raise HTTPException(status_code=401, detail="Token has expired")
            
            # 5. Verify issuer claims
            if claims.get("iss") != self.issuer:
                raise HTTPException(status_code=401, detail=f"Invalid token issuer: {claims.get('iss')}")
            
            # 6. Verify client app ID
            token_client_id = claims.get("client_id") or claims.get("aud")
            if self.client_id and token_client_id != self.client_id:
                raise HTTPException(status_code=401, detail="Token client ID mismatch")
                
            creds.decoded = claims
            if "name" not in creds.decoded:
                creds.decoded["name"] = claims.get("cognito:username") or claims.get("email", "").split("@")[0] or "Cognito User"
            
            return creds
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            logger.error(f"JWT verification error: {e}")
            raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

# Instantiate security guard dependency
auth_guard = CognitoGuard()

async def get_current_user_id(creds: HTTPAuthorizationCredentials = Depends(auth_guard)) -> str:
    """Dependency injection helper that returns the authenticated user's unique sub ID."""
    return creds.decoded["sub"]

# Initialize Database connection pool wrapper
db = Database()

# ----------------------------------------------------
# 4. Request / Response Pydantic Validation Models
# ----------------------------------------------------
class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    years_until_retirement: Optional[int] = None
    target_retirement_income: Optional[float] = None

class AccountUpdate(BaseModel):
    account_name: Optional[str] = None
    account_purpose: Optional[str] = None
    cash_balance: Optional[float] = None

class PositionUpdate(BaseModel):
    quantity: Optional[float] = None

class AnalyzeRequest(BaseModel):
    analysis_type: str = "portfolio"

# ----------------------------------------------------
# 5. API Routes Definitions
# ----------------------------------------------------

# Health check endpoints for load balancers
@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/api/health")
async def health_api():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

# User profile routes
@app.get("/api/user")
async def get_or_create_user(
    user_id: str = Depends(get_current_user_id),
    creds: HTTPAuthorizationCredentials = Depends(auth_guard)
):
    """Retrieve user details, automatically seeding profiles on first login."""
    user = db.users.find_by_user_id(user_id)
    if user:
        return {"user": user, "created": False}

    token_data = creds.decoded
    display_name = token_data.get("name") or token_data.get("email", "").split("@")[0] or "New User"
    user_data = {
        "user_id": user_id,
        "display_name": display_name,
        "years_until_retirement": 25,
        "target_retirement_income": 800000,
        "asset_class_targets": {"equity": 75, "fixed_income": 20, "commodities": 5},
        "region_targets": {"asia": 80, "global": 20},
    }
    # Auto-insert profile row in database
    db.users.db.insert("users", user_data, returning="user_id")
    created_user = db.users.find_by_user_id(user_id)
    return {"user": created_user, "created": True}

@app.put("/api/user")
async def update_user(data: UserUpdate, user_id: str = Depends(get_current_user_id)):
    """Update user retirement variables (years remaining, target income)."""
    update_data = data.model_dump(exclude_unset=True)
    db.users.db.update("users", update_data, "user_id = :cid", {"cid": user_id})
    return db.users.find_by_user_id(user_id)

# Account management routes
@app.get("/api/accounts")
async def list_accounts(user_id: str = Depends(get_current_user_id)):
    """List all accounts registered to the authenticated user ID."""
    return db.accounts.find_by_user(user_id)

@app.post("/api/accounts")
async def create_account(account: AccountCreate, user_id: str = Depends(get_current_user_id)):
    """Create a new portfolio account category."""
    if not db.users.find_by_user_id(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    account_id = db.accounts.create_account(
        user_id=user_id,
        account_name=account.account_name,
        account_purpose=account.account_purpose,
        cash_balance=getattr(account, "cash_balance", Decimal("0")),
    )
    return db.accounts.find_by_id(account_id)

@app.put("/api/accounts/{account_id}")
async def update_account(account_id: str, data: AccountUpdate, user_id: str = Depends(get_current_user_id)):
    """Update cash balances or names of an account."""
    account = db.accounts.find_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    db.accounts.update(account_id, data.model_dump(exclude_unset=True))
    return db.accounts.find_by_id(account_id)

@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: str, user_id: str = Depends(get_current_user_id)):
    """Delete an account and cascade delete all nested equity positions."""
    account = db.accounts.find_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    for pos in db.positions.find_by_account(account_id):
        db.positions.delete(pos["id"])
    db.accounts.delete(account_id)
    return {"message": "Account deleted"}

# Position management routes
@app.get("/api/accounts/{account_id}/positions")
async def list_positions(account_id: str, user_id: str = Depends(get_current_user_id)):
    """Get all positions inside an account. Self-heals instruments if price is zero."""
    account = db.accounts.find_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    positions = db.positions.find_by_account(account_id)
    
    enriched = []
    for pos in positions:
        inst = db.instruments.find_by_symbol(pos["symbol"])
        if inst:
            curr_price = float(inst.get("current_price") or 0)
            # Self-healing logic: if symbol price is unseeded, query live metrics and save
            if curr_price <= 0:
                try:
                    from planner.prices import get_share_price
                    new_price = get_share_price(pos["symbol"])
                    if new_price > 0:
                        db.client.update(
                            'instruments',
                            {'current_price': Decimal(str(new_price))},
                            "symbol = :symbol",
                            {'symbol': pos["symbol"]}
                        )
                        inst["current_price"] = new_price
                        logger.info(f"Self-healed price for {pos['symbol']} to {new_price}")
                except Exception as e:
                    logger.error(f"Failed to self-heal price for {pos['symbol']}: {e}")
        enriched.append({**pos, "instrument": inst})
        
    return {"positions": enriched}

@app.post("/api/positions")
async def create_position(position: PositionCreate, user_id: str = Depends(get_current_user_id)):
    """
    Add a new asset position. Creates a skeleton instrument record if symbol is new.
    For Demat Cash accounts, validates and deducts corresponding cash balances.
    """
    account = db.accounts.find_by_id(position.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    symbol = position.symbol.upper().strip()

    # Query current market price using our prices helper
    price = 0.0
    try:
        from planner.prices import get_share_price
        price = get_share_price(symbol)
    except Exception as e:
        logger.error(f"Error fetching price for {symbol}: {e}")

    # Register instrument description if unknown
    instrument = db.instruments.find_by_symbol(symbol)
    if not instrument:
        instrument_type = "stock" if (len(symbol.split(".")[0]) <= 6 and symbol.split(".")[0].isalpha()) else "etf"
        db.instruments.create_instrument(InstrumentCreate(
            symbol=symbol,
            name=f"{symbol} (Added)",
            instrument_type=instrument_type,
            current_price=Decimal(str(price)),
            allocation_regions={"asia": 100.0},
            allocation_sectors={"other": 100.0},
            allocation_asset_class={"equity": 100.0},
        ))
    else:
        if price > 0:
            db.client.update(
                'instruments',
                {'current_price': Decimal(str(price))},
                "symbol = :symbol",
                {'symbol': symbol}
            )

    # Cash validation and deduction logic for cash Demat accounts
    if account.get("account_name") == "Demat Account":
        cost = Decimal(str(price)) * Decimal(str(position.quantity))
        cash_balance = Decimal(str(account.get("cash_balance", 0)))
        if cash_balance < cost:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient cash balance. Required: ₹{cost:,.2f}, Available: ₹{cash_balance:,.2f}."
            )
        new_cash = cash_balance - cost
        db.accounts.update(position.account_id, {"cash_balance": float(new_cash)})

    position_id = db.positions.add_position(account_id=position.account_id, symbol=symbol, quantity=position.quantity)
    return db.positions.find_by_id(position_id)

@app.delete("/api/positions/{position_id}")
async def delete_position(position_id: str, user_id: str = Depends(get_current_user_id)):
    """Remove a position and refund cash if deleting from a Demat Account."""
    position = db.positions.find_by_id(position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    account = db.accounts.find_by_id(position["account_id"])
    if not account or account.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Demat Account Cash Refund Logic
    if account.get("account_name") == "Demat Account":
        symbol = position.get("symbol", "")
        quantity = Decimal(str(position.get("quantity", 0)))
        price = 0.0
        try:
            from planner.prices import get_share_price
            price = get_share_price(symbol)
        except Exception as e:
            logger.error(f"Error fetching price: {e}")
        if price <= 0:
            inst = db.instruments.find_by_symbol(symbol)
            if inst:
                price = float(inst.get("current_price") or 0)
        refund = Decimal(str(price)) * quantity
        new_cash = Decimal(str(account.get("cash_balance", 0))) + refund
        db.accounts.update(position["account_id"], {"cash_balance": float(new_cash)})

    db.positions.delete(position_id)
    return {"message": "Position deleted"}

@app.get("/api/instruments")
async def list_instruments(user_id: str = Depends(get_current_user_id)):
    """List all registered instruments (used for autocomplete boxes)."""
    instruments = db.instruments.find_all()
    return [
        {"symbol": i["symbol"], "name": i["name"], "instrument_type": i["instrument_type"],
         "current_price": float(i["current_price"]) if i.get("current_price") else None}
        for i in instruments
    ]

# ----------------------------------------------------
# 6. AI Agent Pipeline Trigger Routing
# ----------------------------------------------------
@app.post("/api/analyze")
async def trigger_analysis(
    request: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
):
    """
    Trigger the AI agent pipeline execution.
    1. Creates a Job row in the database tracking 'pending' status.
    2. Checks if SQS_QUEUE_URL is configured:
       - In Production: Pushes the job payload onto SQS. The lambda-planner processes it asynchronously.
       - In Local Dev: Runs immediately on a background thread using FastAPI's BackgroundTasks.
    """
    user = db.users.find_by_user_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    job_id = db.jobs.create_job(
        user_id=user_id,
        job_type="portfolio_analysis",
        request_payload={"analysis_type": request.analysis_type},
    )

    sqs_queue_url = os.getenv("SQS_QUEUE_URL")
    if sqs_queue_url:
        logger.info(f"Production Env: Sending job {job_id} to SQS queue...")
        try:
            import boto3
            sqs = boto3.client("sqs")
            sqs.send_message(
                QueueUrl=sqs_queue_url,
                MessageBody=json.dumps({"job_id": str(job_id)})
            )
            logger.info(f"Job {job_id} successfully queued on SQS ✓")
            return {"job_id": str(job_id), "message": "Analysis started. Poll job status."}
        except Exception as e:
            logger.error(f"Failed to queue SQS: {e}. Falling back to background threads.")

    # Local Dev / Fallback execution loop using BackgroundTasks
    async def _run_agents(j_id: str):
        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from planner.lambda_handler import run_orchestrator
        from planner.observability import observe
        try:
            with observe():
                await run_orchestrator(j_id)
        except Exception as ex:
            logger.error(f"Agent pipeline failed for job {j_id}: {ex}", exc_info=True)
            db.jobs.update_status(j_id, "failed", error_message=str(ex))

    background_tasks.add_task(_run_agents, job_id)
    logger.info(f"Local dev: Job {job_id} queued via background tasks")

    return {"job_id": str(job_id), "message": "Analysis started. Poll job status."}

# Job Status Checking routes
@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, user_id: str = Depends(get_current_user_id)):
    """Query execution status and outputs of an analysis run."""
    job = db.jobs.find_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return job

@app.get("/api/jobs")
async def list_jobs(user_id: str = Depends(get_current_user_id)):
    """Fetch history of most recent analysis runs."""
    jobs = db.jobs.find_by_user(user_id, limit=20)
    jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"jobs": jobs}

# ----------------------------------------------------
# 7. Utilities & Test Data Seeding Routes
# ----------------------------------------------------
@app.delete("/api/reset-accounts")
async def reset_accounts(user_id: str = Depends(get_current_user_id)):
    """Clear all accounts (and positions) for testing."""
    accounts = db.accounts.find_by_user(user_id)
    for account in accounts:
        db.accounts.delete(account["id"])  # Cascade deletes nested rows
    return {"message": f"Deleted {len(accounts)} account(s)"}

@app.post("/api/populate-test-data")
async def populate_test_data(user_id: str = Depends(get_current_user_id)):
    """Seed sample Indian portfolios (EPF/PPF, NPS, Demat cash accounts) with symbols."""
    if not db.users.find_by_user_id(user_id):
        raise HTTPException(status_code=404, detail="User not found")

    extra_instruments = {
        "RELIANCE.NS": {"name": "Reliance Industries Limited", "type": "stock", "price": 2450.00,
                        "regions": {"asia": 100}, "sectors": {"energy": 60, "communication": 20, "consumer_discretionary": 20}, "asset_class": {"equity": 100}},
        "TCS.NS":      {"name": "Tata Consultancy Services", "type": "stock", "price": 3850.00,
                        "regions": {"asia": 100}, "sectors": {"technology": 100}, "asset_class": {"equity": 100}},
        "HDFCBANK.NS": {"name": "HDFC Bank Limited", "type": "stock", "price": 1520.00,
                        "regions": {"asia": 100}, "sectors": {"financials": 100}, "asset_class": {"equity": 100}},
        "INFY.NS":     {"name": "Infosys Limited", "type": "stock", "price": 1430.00,
                        "regions": {"asia": 100}, "sectors": {"technology": 100}, "asset_class": {"equity": 100}},
    }
    for symbol, info in extra_instruments.items():
        if not db.instruments.find_by_symbol(symbol):
            db.instruments.create_instrument(InstrumentCreate(
                symbol=symbol, name=info["name"], instrument_type=info["type"],
                current_price=Decimal(str(info["price"])),
                allocation_regions=info["regions"], allocation_sectors=info["sectors"],
                allocation_asset_class=info["asset_class"],
            ))

    accounts_spec = [
        {"name": "EPF/PPF", "purpose": "Provident Fund retirement savings", "cash": 500000.0, "positions": []},
        {"name": "NPS",     "purpose": "National Pension System", "cash": 200000.0,
         "positions": [("GILT5YBEES.NS", 500), ("LIQUIDBEES.NS", 100)]},
        {"name": "Demat Account", "purpose": "Taxable investment account for stocks & ETFs", "cash": 100000.0,
         "positions": [("NIFTYBEES.NS", 1000), ("JUNIORBEES.NS", 400), ("GOLDBEES.NS", 500),
                       ("RELIANCE.NS", 100), ("TCS.NS", 50), ("HDFCBANK.NS", 150), ("INFY.NS", 80)]},
    ]

    created = []
    for spec in accounts_spec:
        acc_id = db.accounts.create_account(
            user_id=user_id,
            account_name=spec["name"],
            account_purpose=spec["purpose"],
            cash_balance=Decimal(str(spec["cash"])),
        )
        for symbol, qty in spec["positions"]:
            try:
                db.positions.add_position(account_id=acc_id, symbol=symbol, quantity=Decimal(str(qty)))
            except Exception as e:
                logger.warning(f"Could not add {symbol}: {e}")
        created.append(spec["name"])

    return {"message": "Test portfolio seeded successfully", "accounts": created}

# ----------------------------------------------------
# 8. Local Server Launcher
# ----------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    # Boots server on localhost:8000 when running directly
    uvicorn.run(app, host="0.0.0.0", port=8000)