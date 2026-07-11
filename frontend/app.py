"""
FinAI - Streamlit Frontend Application
======================================
This file serves as the interactive dashboard for the FinAI Portfolio Advisor.
It is built with Streamlit and communicates directly with the FastAPI backend 
using REST API calls (HTTP GET, POST, PUT, DELETE).

Web communication flow:
  [Streamlit UI in Browser] 
          │ (User interface controls & clicks)
          ▼
  [REST requests to API_BASE_URL]
          │ (Forwarded securely through CloudFront)
          ▼
  [FastAPI Backend Lambda / Local FastAPI Server]
"""

import os
import time
import requests
import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

# Load configurations from the project's .env file (overrides existing environment values)
load_dotenv(override=True)

# ----------------------------------------------------
# 1. Environment & Server Routing Properties
# ----------------------------------------------------
# API_BASE_URL: points to our FastAPI backend. If deployed in production,
# this is the CloudFront secure HTTPS URL; locally it falls back to port 8000.
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

# Cognito environment variables needed to perform user registration & login.
# In a local development environment, these can be left blank, in which case the app
# runs in "Mock Auth" mode using a hardcoded developer user ID.
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
COGNITO_REGION = os.getenv("COGNITO_REGION") or os.getenv("DEFAULT_AWS_REGION", "us-east-1")


# ----------------------------------------------------
# 2. Page Configuration & Custom CSS Styling
# ----------------------------------------------------
# st.set_page_config must be called as the very first Streamlit command.
# It configures browser tab metadata and default sidebar behaviors.
st.set_page_config(
    page_title="FinAI — Indian Wealth Advisor",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Theme Injection (Japandi Contrast Style):
# Streamlit's default components can look generic. We inject custom CSS into the HTML body
# to style app background colors, cards, font families (Outfit/Inter), metric blocks,
# and give our navigation tabs custom color highlights (Sage Green, Sand Orange, Slate Blue, Rust Red).
st.markdown("""
<style>
    /* Premium neutral white theme */
    .stApp { 
        background-color: #ffffff; 
        color: #1e293b; 
        font-family: 'Outfit', 'Inter', -apple-system, sans-serif;
    }
    
    /* Minimal card containers for report blocks and advisor status cards */
    .cosmic-card { 
        background: #ffffff; 
        border: 1px solid #e2e8f0;
        border-radius: 8px; 
        padding: 20px; 
        margin-bottom: 18px; 
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px 0 rgba(0, 0, 0, 0.06);
    }
    
    /* Clean typography classes */
    .neon-title  { 
        color: #0f172a; 
        font-weight: 800; 
        font-size: 2.8rem; 
        margin: 0; 
        letter-spacing: -0.5px;
    }
    .neon-sub    { 
        color: #64748b; 
        font-size: 0.9rem; 
        letter-spacing: 1.5px;
        text-transform: uppercase; 
        margin-bottom: 24px; 
        font-weight: 600;
    }
    
    /* Sidebar styling: light grey background with a clean border */
    section[data-testid="stSidebar"] { 
        background: #f8fafc !important;
        border-right: 1px solid #e2e8f0 !important; 
    }
    
    /* Premium metric blocks */
    div[data-testid="stMetricValue"] { 
        font-size: 1.8rem !important; 
        font-weight: 700 !important;
        color: #0f172a !important; 
    }
    div[data-testid="stMetricLabel"] { 
        color: #64748b !important; 
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        font-size: 0.75rem !important;
    }
    
    /* Japandi Contrast Tabs Styling */
    div[data-baseweb="tab-highlight"] {
        display: none !important;
    }
    div[role="tablist"] {
        border-bottom: none !important;
        margin-bottom: 15px !important;
    }
    button[data-baseweb="tab"] { 
        font-size: 0.95rem !important; 
        font-weight: 600 !important;
        padding: 8px 16px !important;
        transition: all 0.3s ease;
        border-radius: 6px !important;
        border: 1px solid #e2e8f0 !important;
        margin: 0 4px !important;
    }
    
    /* Inactive tab states: stone/sand background with distinct colored text */
    button[data-baseweb="tab"]:nth-of-type(1) { background-color: #FAF8F6 !important; color: #7A8B75 !important; }
    button[data-baseweb="tab"]:nth-of-type(2) { background-color: #FAF8F6 !important; color: #D49B48 !important; }
    button[data-baseweb="tab"]:nth-of-type(3) { background-color: #FAF8F6 !important; color: #455A6F !important; }
    button[data-baseweb="tab"]:nth-of-type(4) { background-color: #FAF8F6 !important; color: #B85C43 !important; }
    
    /* Hover tab states: darken background slightly */
    button[data-baseweb="tab"]:hover { 
        background-color: #EAE5E0 !important; 
    }

    /* Active tab states: distinct solid Japandi colors with white text */
    button[data-baseweb="tab"]:nth-of-type(1)[aria-selected="true"] { 
        background-color: #7A8B75 !important; 
        color: #FFFFFF !important; 
        border-color: #7A8B75 !important;
    }
    button[data-baseweb="tab"]:nth-of-type(2)[aria-selected="true"] { 
        background-color: #D49B48 !important; 
        color: #FFFFFF !important; 
        border-color: #D49B48 !important;
    }
    button[data-baseweb="tab"]:nth-of-type(3)[aria-selected="true"] { 
        background-color: #455A6F !important; 
        color: #FFFFFF !important; 
        border-color: #455A6F !important;
    }
    button[data-baseweb="tab"]:nth-of-type(4)[aria-selected="true"] { 
        background-color: #B85C43 !important; 
        color: #FFFFFF !important; 
        border-color: #B85C43 !important;
    }
    
    /* Subtle highlighting for elements */
    .highlight-badge {
        background-color: #f1f5f9;
        color: #0f172a;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 0.85rem;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------
# 3. Session State Management
# ----------------------------------------------------
# Streamlit runs from top-to-bottom on every user interaction. To persist data (like the
# logged-in user's token or the currently running job ID) across reruns, we use st.session_state.
if "user_id"      not in st.session_state: st.session_state.user_id      = "test_user_001"
if "active_job_id" not in st.session_state: st.session_state.active_job_id = None

# Render main header elements
st.markdown("<h1 class='neon-title'>FinAI</h1>", unsafe_allow_html=True)
st.markdown("<p class='neon-sub'>Agentic Indian Equity Portfolio Advisor</p>", unsafe_allow_html=True)


# ----------------------------------------------------
# 4. REST API Wrapper Methods
# ----------------------------------------------------
# These helper functions perform HTTP requests to our FastAPI backend.
# They automatically inject the current session's user ID/JWT token under the 
# 'Authorization' header to pass through the Cognito verification guard on the API gateway.

def headers():
    """Build Authorization bearer headers for secure backend routes."""
    return {"Authorization": f"Bearer {st.session_state.user_id}", "Content-Type": "application/json"}

def api_get(path, **kw):
    """Execute a GET request against the API server."""
    try:
        r = requests.get(f"{API_BASE_URL}{path}", headers=headers(), timeout=5, **kw)
        return r.json() if r.ok else None
    except Exception:
        return None

def api_post(path, **kw):
    """Execute a POST request against the API server."""
    try:
        r = requests.post(f"{API_BASE_URL}{path}", headers=headers(), timeout=10, **kw)
        if r.ok:
            return r.json()
        else:
            try:
                err = r.json()
                if isinstance(err, dict) and "detail" in err:
                    return {"error_detail": err["detail"]}
            except Exception:
                pass
            return None
    except Exception:
        return None

def api_put(path, **kw):
    """Execute a PUT request against the API server."""
    try:
        r = requests.put(f"{API_BASE_URL}{path}", headers=headers(), timeout=5, **kw)
        if r.ok:
            return r.json()
        else:
            try:
                err = r.json()
                if isinstance(err, dict) and "detail" in err:
                    return {"error_detail": err["detail"]}
            except Exception:
                pass
            return None
    except Exception:
        return None

def api_delete(path, **kw):
    """Execute a DELETE request against the API server."""
    try:
        r = requests.delete(f"{API_BASE_URL}{path}", headers=headers(), timeout=5, **kw)
        if r.ok:
            return r.json()
        else:
            try:
                err = r.json()
                if isinstance(err, dict) and "detail" in err:
                    return {"error_detail": err["detail"]}
            except Exception:
                pass
            return None
    except Exception:
        return None


# ----------------------------------------------------
# 5. AWS Cognito Authentication Helpers
# ----------------------------------------------------
# These connect directly to AWS Cognito Identity Provider service to handle user registration,
# sign-in, and verification code confirmations.
# Config(signature_version=UNSIGNED) is used so we don't need local AWS credentials to make these calls.
import boto3
from botocore import UNSIGNED
from botocore.config import Config

def get_cognito_client():
    """Returns an unsigned Cognito boto3 client instance."""
    return boto3.client("cognito-idp", region_name=COGNITO_REGION, config=Config(signature_version=UNSIGNED))

def cognito_sign_in(username, password):
    """Logs user in using Cognito USER_PASSWORD_AUTH flow. Returns IdToken on success."""
    try:
        client = get_cognito_client()
        resp = client.initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password
            }
        )
        return resp.get("AuthenticationResult", {}).get("IdToken")
    except Exception as e:
        st.error(f"Sign in failed: {str(e)}")
        return None

def cognito_sign_up(username, email, password):
    """Registers a new user in Cognito. Note: Pools are configured to auto-confirm emails in local mode."""
    try:
        client = get_cognito_client()
        client.sign_up(
            ClientId=COGNITO_CLIENT_ID,
            Username=username,
            Password=password,
            UserAttributes=[
                {"Name": "email", "Value": email}
            ]
        )
        return True
    except Exception as e:
        st.error(f"Sign up failed: {str(e)}")
        return False

def cognito_confirm_sign_up(username, code):
    """Submits verification code to Cognito to confirm account registration."""
    try:
        client = get_cognito_client()
        client.confirm_sign_up(
            ClientId=COGNITO_CLIENT_ID,
            Username=username,
            ConfirmationCode=code
        )
        return True
    except Exception as e:
        st.error(f"Confirmation failed: {str(e)}")
        return False


# ----------------------------------------------------
# 6. Sidebar (Auth Configuration & User Settings)
# ----------------------------------------------------
with st.sidebar:
    st.markdown("### 🔑 Authentication Mode")
    
    # Check if Cognito environment variables are present before exposing production login UI
    auth_modes = ["Mock Auth (Local Dev)"]
    if COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID:
        auth_modes.append("Cognito Auth (Production)")
        
    selected_auth_mode = st.radio("Select Auth Mode", options=auth_modes, index=0, label_visibility="collapsed")
    
    st.markdown("---")
    
    # ── Cognito Authentication Flows ──
    if selected_auth_mode == "Cognito Auth (Production)":
        is_logged_in = st.session_state.user_id and st.session_state.user_id != "test_user_001" and len(st.session_state.user_id) > 50
        if is_logged_in:
            st.success("Signed in with Cognito!")
            if st.button("🚪 Sign Out"):
                st.session_state.user_id = "test_user_001"
                st.rerun()
        else:
            cog_action = st.selectbox("Action", ["Log In", "Sign Up", "Confirm Code"])
            
            if cog_action == "Log In":
                user_in = st.text_input("Username / Email", key="cog_user")
                pass_in = st.text_input("Password", type="password", key="cog_pass")
                if st.button("Log In", key="cog_login_btn", type="primary"):
                    if not user_in or not pass_in:
                        st.error("Fields cannot be empty")
                    else:
                        with st.spinner("Logging in..."):
                            token = cognito_sign_in(user_in, pass_in)
                            if token:
                                st.session_state.user_id = token
                                st.success("Logged in successfully!")
                                st.rerun()
                                
            elif cog_action == "Sign Up":
                user_in = st.text_input("Username", key="cog_reg_user")
                email_in = st.text_input("Email Address", key="cog_reg_email")
                pass_in = st.text_input("Password (min 8 characters)", type="password", key="cog_reg_pass")
                if st.button("Sign Up", key="cog_reg_btn"):
                    if not user_in or not email_in or not pass_in:
                        st.error("Fields cannot be empty")
                    else:
                        with st.spinner("Signing up..."):
                            if cognito_sign_up(user_in, email_in, pass_in):
                                st.success("Registration successful! Please switch 'Action' above to 'Log In' or 'Confirm Code' if required.")
                                
            elif cog_action == "Confirm Code":
                user_in = st.text_input("Username", key="cog_conf_user")
                code_in = st.text_input("Verification Code", key="cog_conf_code")
                if st.button("Confirm Registration", key="cog_conf_btn"):
                    if not user_in or not code_in:
                        st.error("Fields cannot be empty")
                    else:
                        with st.spinner("Confirming account..."):
                            if cognito_confirm_sign_up(user_in, code_in):
                                st.success("Account confirmed! You can now Log In.")
    
    # ── Mock Authentication (Default) ──
    else:
        st.markdown("#### 👤 Local Developer Profile")
        uid_input = st.text_input("User ID / Mock Token", value=st.session_state.user_id)
        if uid_input != st.session_state.user_id:
            st.session_state.user_id = uid_input
            st.rerun()

    # Fetch user demographic settings from backend API
    profile_resp = api_get("/api/user")
    user_profile = profile_resp.get("user") if profile_resp else None

    # Render target retirement income forms
    if user_profile:
        st.markdown(f"**👤 {user_profile.get('display_name', 'Advisor')}**")
        st.caption(f"ID: {user_profile.get('user_id')}")
        st.markdown("---")
        st.markdown("### 🎯 Retirement Goals")
        yrs = st.slider("Years to Retirement", 1, 50, int(user_profile.get("years_until_retirement", 25)))
        income = st.number_input("Target Annual Income (₹)", value=float(user_profile.get("target_retirement_income", 800000)), step=50000.0, format="%.0f")
        if st.button("💾 Save Goals"):
            api_put("/api/user", json={"years_until_retirement": yrs, "target_retirement_income": income})
            st.success("Saved!")
            st.rerun()
    else:
        st.info("Backend offline or first run — seed data to get started.")

    st.markdown("---")
    st.markdown("### 🛠️ Quick Actions")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🌱 Seed Data", help="Load sample Indian portfolio"):
            result = api_post("/api/populate-test-data")
            if result:
                st.success("Portfolio seeded!")
                st.rerun()
            else:
                st.error("Seed failed — is the API running?")
    with col2:
        if st.button("🗑️ Reset", help="Delete all accounts"):
            result = api_delete("/api/reset-accounts")
            if result:
                st.success("Reset done")
                st.rerun()
            else:
                st.error("Reset failed")


# ----------------------------------------------------
# 7. Portfolio Valuation Aggregation
# ----------------------------------------------------
# We pull user accounts and position assets from backend REST APIs.
# We iterate through cash balances and stock positions (multiplying qty * price)
# to compute the total net worth of the portfolio.
accounts = api_get("/api/accounts") or []
positions_by_account = {}
total_cash = total_invested = 0.0

for acc in accounts:
    acc_name = acc.get("account_name", "")
    acc_cash = float(acc.get("cash_balance", 0))
    if acc_name == "Demat Account":
        # Uninvested cash resides inside Demat Account cash balance
        total_cash += acc_cash
    else:
        # Non-demat accounts (PPF, NPS, Gold, Silver) are treated directly as invested assets
        total_invested += acc_cash

    # Fetch positions list for this account
    pos_data = api_get(f"/api/accounts/{acc['id']}/positions")
    positions = (pos_data or {}).get("positions", [])
    positions_by_account[acc["id"]] = positions
    
    # Calculate position values based on ticker quantity * current price
    for pos in positions:
        qty = float(pos.get("quantity") or 0)
        price = float((pos.get("instrument") or {}).get("current_price") or 0)
        total_invested += qty * price

total_value = total_cash + total_invested


# ----------------------------------------------------
# 8. Interactive Dashboard Tabs
# ----------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "💼 Accounts", "🤖 AI Advisor", "📝 Report"])


# ====================================================================
# TAB 1 — DASHBOARD
# ====================================================================
with tab1:
    st.markdown("### 📈 Portfolio Overview")

    # Render net worth metrics in four horizontal columns
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Net Worth",   f"₹{total_value:,.0f}")
    c2.metric("Invested Assets",   f"₹{total_invested:,.0f}")
    c3.metric("Cash Balance",      f"₹{total_cash:,.0f}")
    c4.metric("Accounts",          len(accounts))
    st.markdown("---")

    if not accounts:
        st.info("💡 No portfolio found. Click **🌱 Seed Data** in the sidebar to load a sample Indian equity portfolio.")
    else:
        # Allocate categories (Asset Classes, Sectors, Regions)
        # We loop through positions and cash balances to distribute fractional valuations.
        asset_data, sector_data, region_data = {}, {}, {}
        holdings, account_dist = [], []

        for acc in accounts:
            acc_name = acc.get("account_name", "Unnamed")
            cash = float(acc.get("cash_balance", 0))
            
            # Map cash balance of special accounts to their asset classes
            if acc_name == "Demat Account":
                if cash > 0:
                    asset_data["Cash"] = asset_data.get("Cash", 0.0) + cash
            elif acc_name == "NPS":
                asset_data["Fixed Income"] = asset_data.get("Fixed Income", 0.0) + cash * 0.5
                asset_data["Equity"] = asset_data.get("Equity", 0.0) + cash * 0.5
                sector_data["Retirement Fund"] = sector_data.get("Retirement Fund", 0.0) + cash
                region_data["Asia"] = region_data.get("Asia", 0.0) + cash
            elif acc_name == "PPF":
                asset_data["Fixed Income"] = asset_data.get("Fixed Income", 0.0) + cash
                sector_data["Government Debt"] = sector_data.get("Government Debt", 0.0) + cash
                region_data["Asia"] = region_data.get("Asia", 0.0) + cash
            elif acc_name in ("Gold", "Silver"):
                asset_data["Commodities"] = asset_data.get("Commodities", 0.0) + cash
                sector_data["Precious Metals"] = sector_data.get("Precious Metals", 0.0) + cash
                region_data["Global"] = region_data.get("Global", 0.0) + cash
            else:
                if cash > 0:
                    asset_data["Cash"] = asset_data.get("Cash", 0.0) + cash

            acc_val = cash
            for pos in positions_by_account.get(acc["id"], []):
                qty   = float(pos.get("quantity") or 0)
                inst  = pos.get("instrument") or {}
                price = float(inst.get("current_price") or 0)
                val   = qty * price
                acc_val += val
                sym   = pos.get("symbol", "")
                name  = inst.get("name", sym)
                
                # Top holdings breakdown
                holdings.append({"Symbol": sym, "Name": name, "Value (₹)": val})
                
                # Sum asset class percentages
                for cls, pct in (inst.get("allocation_asset_class") or {}).items():
                    k = cls.replace("_", " ").title()
                    asset_data[k] = asset_data.get(k, 0) + val * float(pct) / 100
                # Sum sector exposure percentages
                for sec, pct in (inst.get("allocation_sectors") or {}).items():
                    k = sec.replace("_", " ").title()
                    sector_data[k] = sector_data.get(k, 0) + val * float(pct) / 100
                # Sum geographic region percentages
                for reg, pct in (inst.get("allocation_regions") or {}).items():
                    k = reg.replace("_", " ").title()
                    region_data[k] = region_data.get(k, 0) + val * float(pct) / 100
            
            # Account balance totals mapping
            account_dist.append({"Account": acc_name, "Value": acc_val})

        # Premium JAPANDI color sequence
        JAPANDI_PALETTE = ["#B85C43", "#7A8B75", "#455A6F", "#D49B48", "#9A8A78", "#CBBFB4", "#3C4048"]

        col_l, col_r = st.columns(2)

        # Render Pie Charts in the Left Column
        with col_l:
            if asset_data:
                fig = px.pie(pd.DataFrame(asset_data.items(), columns=["Class", "Value"]),
                             names="Class", values="Value", hole=0.4, title="Asset Class Allocation",
                             color_discrete_sequence=JAPANDI_PALETTE)
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#1e293b")
                st.plotly_chart(fig, width="stretch")

            if account_dist:
                fig = px.pie(pd.DataFrame(account_dist), names="Account", values="Value", hole=0.3,
                             title="Account Breakdown", color_discrete_sequence=JAPANDI_PALETTE[2:])
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#1e293b")
                st.plotly_chart(fig, width="stretch")

        # Render Bar Charts in the Right Column
        with col_r:
            if sector_data:
                df_sec = pd.DataFrame(sector_data.items(), columns=["Sector", "Value"]).sort_values("Value", ascending=False)
                fig = px.bar(df_sec, x="Value", y="Sector", orientation="h", title="Sector Exposure",
                             color="Sector", color_discrete_sequence=JAPANDI_PALETTE)
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#1e293b", showlegend=False)
                st.plotly_chart(fig, width="stretch")

            if holdings:
                df_h = pd.DataFrame(holdings).groupby(["Symbol", "Name"])["Value (₹)"].sum().reset_index()
                df_h = df_h.sort_values("Value (₹)", ascending=False).head(6)
                fig = px.bar(df_h, x="Symbol", y="Value (₹)", hover_data=["Name"], title="Top Holdings",
                             color="Symbol", color_discrete_sequence=JAPANDI_PALETTE)
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#1e293b", showlegend=False)
                st.plotly_chart(fig, width="stretch")


# ====================================================================
# TAB 2 — ACCOUNTS MANAGER
# ====================================================================
with tab2:
    st.markdown("### 💼 Portfolio Accounts & Positions")

    if not accounts:
        st.info("No accounts yet. Use **🌱 Seed Data** in the sidebar.")
    else:
        # Loop through active accounts and render a collapsible expander for each
        for acc in accounts:
            acc_id   = acc["id"]
            acc_name = acc.get("account_name", "Unnamed")
            cash     = float(acc.get("cash_balance", 0))
            positions = positions_by_account.get(acc_id, [])

            # Format the account header label clearly
            label = f"📁 {acc_name}  —  Amount Invested: ₹{cash:,.2f}" if acc_name != "Demat Account" else f"📁 {acc_name}  —  Cash: ₹{cash:,.2f}  |  {len(positions)} positions"
            
            with st.expander(label, expanded=False):
                if acc_name == "Demat Account":
                    # Demat Account allows trading positions
                    if positions:
                        rows = []
                        for pos in positions:
                            qty   = float(pos.get("quantity") or 0)
                            inst  = pos.get("instrument") or {}
                            price = float(inst.get("current_price") or 0)
                            rows.append({
                                "Symbol": pos.get("symbol", ""),
                                "Name": inst.get("name", "—"),
                                "Quantity": qty,
                                "Price (₹)": f"₹{price:,.2f}",
                                "Value (₹)": f"₹{qty * price:,.2f}",
                                "_id": pos.get("id", ""),
                            })
                        df = pd.DataFrame(rows)
                        st.dataframe(df.drop(columns=["_id"]), width="stretch")

                        # Remove positions form controls
                        sym_options = [r["Symbol"] for r in rows]
                        del_col, btn_col = st.columns([3, 1])
                        with del_col:
                            del_sym = st.selectbox("Remove position", sym_options, key=f"del_{acc_id}")
                        with btn_col:
                            st.markdown("<br>", unsafe_allow_html=True)
                            if st.button("🗑️ Remove", key=f"delbtn_{acc_id}"):
                                target_id = next(r["_id"] for r in rows if r["Symbol"] == del_sym)
                                res = api_delete(f"/api/positions/{target_id}")
                                if res:
                                    st.success(f"Removed {del_sym}")
                                    st.rerun()
                    else:
                        st.caption("No holdings yet.")
                    
                    st.markdown("---")
                    
                    # Form to ADD positions (updates cash balance automatically in backend logic)
                    st.markdown("##### ➕ Add Position")
                    sym_in = st.text_input("Ticker (e.g. RELIANCE.NS)", key=f"sym_{acc_id}")
                    qty_in = st.number_input("Quantity (shares)", min_value=0.01, step=1.0, key=f"qty_{acc_id}")
                    if st.button("Add", key=f"addbtn_{acc_id}"):
                        if not sym_in:
                            st.error("Enter a ticker symbol")
                        else:
                            res = api_post("/api/positions", json={"account_id": acc_id, "symbol": sym_in.upper(), "quantity": qty_in})
                            if res:
                                if isinstance(res, dict) and "error_detail" in res:
                                    st.error(f"Failed to add position: {res['error_detail']}")
                                else:
                                    st.success(f"Added {qty_in} × {sym_in.upper()}")
                                    st.rerun()
                            else:
                                st.error("Failed to add position")

                    st.markdown("---")
                    
                    # Form to update remaining cash balance in the account
                    st.markdown("##### 💵 Cash Balance")
                    new_cash = st.number_input("Update Cash Balance (₹)", min_value=0.0, value=cash, step=1000.0, key=f"cash_{acc_id}")
                    if st.button("Update Cash Balance", key=f"cashbtn_{acc_id}"):
                        res = api_put(f"/api/accounts/{acc_id}", json={"cash_balance": new_cash, "account_name": acc_name})
                        if res:
                            if isinstance(res, dict) and "error_detail" in res:
                                st.error(f"Failed to update cash: {res['error_detail']}")
                            else:
                                st.success(f"Cash balance updated to ₹{new_cash:,.2f}")
                                st.rerun()
                        else:
                            st.error("Failed to update cash balance")
                else:
                    # Non-Demat accounts (e.g. NPS, PPF): directly input the cash invested amount
                    st.markdown("##### 💵 Amount Invested")
                    new_amount = st.number_input("Update Amount (₹)", min_value=0.0, value=cash, step=1000.0, key=f"amount_{acc_id}")
                    if st.button("Update Amount", key=f"amountbtn_{acc_id}"):
                        res = api_put(f"/api/accounts/{acc_id}", json={"cash_balance": new_amount, "account_name": acc_name})
                        if res:
                            st.success(f"Amount invested updated to ₹{new_amount:,.0f}")
                            st.rerun()

    # Create new account form fields
    st.markdown("---")
    st.markdown("### ➕ Open New Account")
    new_name = st.selectbox("Account Name", ["Demat Account", "NPS", "PPF", "Gold", "Silver"])
    
    if new_name == "Demat Account":
        new_purpose = st.selectbox("Type", ["investment", "retirement", "savings", "tax_shield"])
        new_cash = st.number_input("Initial Cash (₹)", min_value=0.0, step=5000.0, key="new_acc_cash")
    else:
        purpose_map = {"NPS": "retirement", "PPF": "tax_shield", "Gold": "investment", "Silver": "investment"}
        new_purpose = purpose_map.get(new_name, "investment")
        new_cash = st.number_input("Amount Invested (₹)", min_value=0.0, step=5000.0, key="new_acc_cash")
        
    if st.button("Create Account"):
        res = api_post("/api/accounts", json={"account_name": new_name, "account_purpose": new_purpose, "cash_balance": new_cash})
        if res:
            st.success(f"Account '{new_name}' created!")
            st.rerun()
        else:
            st.error("Failed to create account")


# ====================================================================
# TAB 3 — AI ADVISOR TEAM
# ====================================================================
with tab3:
    st.markdown("### 🤖 AI Agent Team")
    st.markdown(
        "FinAI uses **4 specialized AI agents** collaborating to analyse your portfolio:\n"
        "1. **🗺️ Planner** — orchestrates all other agents via tool calls  \n"
        "2. **📝 Reporter** — writes a comprehensive portfolio analysis in markdown  \n"
        "3. **📊 Charter** — generates chart data (pie/bar visualizations)  \n"
        "4. **🎯 Retirement** — runs Monte Carlo simulations and retirement projections"
    )
    st.markdown("---")

    trig_col, status_col = st.columns([1, 2])

    with trig_col:
        st.markdown("<div class='cosmic-card'>", unsafe_allow_html=True)
        st.markdown("#### ▶ Run Analysis")
        st.caption("Triggers the full agent pipeline on your current portfolio. Takes 1-3 minutes.")
        if st.button("🚀 Run AI Team Analysis", type="primary"):
            if not accounts:
                st.error("No portfolio found. Seed data first (sidebar).")
            else:
                # Trigger the FastAPI analyze route, which initiates the async orchestrator pipeline
                result = api_post("/api/analyze", json={"analysis_type": "portfolio"})
                if result and result.get("job_id"):
                    st.session_state.active_job_id = result["job_id"]
                    st.success(f"Job started! ID: {result['job_id'][:8]}…")
                    st.rerun()
                else:
                    st.error("Failed to start analysis")
        st.markdown("</div>", unsafe_allow_html=True)

    with status_col:
        st.markdown("<div class='cosmic-card'>", unsafe_allow_html=True)
        st.markdown("#### 📡 Live Status")

        # If a job has been started, we enter a polling loop to query its status
        if st.session_state.active_job_id:
            job_id = st.session_state.active_job_id
            st.markdown(f"**Active Job:** `{job_id[:16]}…`")

            status_area = st.empty()
            progress    = st.progress(0)

            # Poll the job status every 3 seconds for up to 3 minutes
            for step in range(60):
                job = api_get(f"/api/jobs/{job_id}")
                status = (job or {}).get("status", "pending")

                if status == "completed":
                    status_area.success("✅ All agents finished!")
                    progress.progress(100)
                    st.info("Navigate to **📝 Report** tab to view results.")
                    break
                elif status == "failed":
                    err = (job or {}).get("error_message", "unknown error")
                    status_area.error(f"❌ Pipeline failed: {err}")
                    progress.progress(0)
                    break
                elif status == "running":
                    status_area.markdown(f"🏃 **Running…** (polling {step + 1}/60)")
                    progress.progress(min(10 + step * 2, 92))
                else:
                    status_area.markdown(f"⏳ **Pending** — agents queuing…")
                    progress.progress(5)

                time.sleep(3)
        else:
            st.caption("No active job. Click **Run AI Team Analysis** to start.")
        st.markdown("</div>", unsafe_allow_html=True)

    # Render a historical jobs log table
    st.markdown("---")
    st.markdown("#### ⏳ Analysis History")
    jobs_data = api_get("/api/jobs") or {}
    jobs = jobs_data.get("jobs", [])
    if jobs:
        df_jobs = pd.DataFrame([{
            "Job ID": j["id"][:16] + "…",
            "Status": j.get("status", "?").upper(),
            "Created": j.get("created_at", "?"),
            "Completed": j.get("completed_at", "—"),
        } for j in jobs[:10]])
        st.dataframe(df_jobs, width="stretch")
    else:
        st.caption("No past jobs.")


# ====================================================================
# TAB 4 — ANALYSIS REPORT
# ====================================================================
with tab4:
    st.markdown("### 📝 AI Analysis Report & Projections")

    # Find the latest completed job to load results
    completed_job = None
    jobs_data = api_get("/api/jobs") or {}
    for j in (jobs_data.get("jobs") or []):
        if j.get("status") == "completed":
            if st.session_state.active_job_id and j["id"] == st.session_state.active_job_id:
                completed_job = j
                break
            if not completed_job:
                completed_job = j  # Fall back to most recent completed job

    if not completed_job:
        st.info("💡 No completed analysis yet. Go to **🤖 AI Advisor** tab and run an analysis first.")
    else:
        st.markdown(f"**Analysis from job** `{completed_job['id'][:16]}…` "
                    f"(completed: {completed_job.get('completed_at', 'N/A')})")
        st.markdown("---")

        # ── 1. Narrative Report ──
        # Display the Markdown text report generated by the Reporter sub-agent
        report_payload = completed_job.get("report_payload") or {}
        report_content = report_payload.get("content") if isinstance(report_payload, dict) else None
        if report_content:
            st.markdown("<div class='cosmic-card'>", unsafe_allow_html=True)
            st.markdown("#### 📝 Portfolio Analysis Report")
            st.markdown(report_content)
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.warning("Report not found in this job.")

        # ── 2. Retirement Projection Narrative ──
        # Renders the Monte Carlo projections narrative written by the Retirement Specialist
        ret_payload = completed_job.get("retirement_payload") or {}
        ret_content = ret_payload.get("analysis") if isinstance(ret_payload, dict) else None
        if ret_content:
            st.markdown("<div class='cosmic-card'>", unsafe_allow_html=True)
            st.markdown("#### 🎯 Retirement Readiness Analysis")
            st.markdown(ret_content)
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.warning("Retirement projection not found in this job.")

        # ── 3. Dynamic Visual Charts ──
        # Extracts Plotly configurations generated by the Charter agent and renders them.
        # Charts are displayed in a clean two-column grid.
        charts_payload = completed_job.get("charts_payload") or {}
        if isinstance(charts_payload, dict) and charts_payload:
            st.markdown("<div class='cosmic-card'>", unsafe_allow_html=True)
            st.markdown("#### 📊 AI-Generated Portfolio Charts")
            col_a, col_b = st.columns(2)

            for idx, (key, info) in enumerate(charts_payload.items()):
                col = col_a if idx % 2 == 0 else col_b
                with col:
                    title  = info.get("title", key.replace("_", " ").title())
                    c_type = info.get("type", "pie")
                    c_data = info.get("data", [])
                    desc   = info.get("description", "")

                    if c_data:
                        df_c = pd.DataFrame(c_data)
                        if "name" in df_c.columns and "value" in df_c.columns:
                            JAPANDI_PALETTE = ["#B85C43", "#7A8B75", "#455A6F", "#D49B48", "#9A8A78", "#CBBFB4", "#3C4048"]
                            colors = df_c["color"].tolist() if "color" in df_c.columns else JAPANDI_PALETTE
                            if c_type in ("pie", "donut"):
                                fig = px.pie(df_c, names="name", values="value", title=title,
                                             hole=0.4 if c_type == "donut" else 0,
                                             color_discrete_sequence=colors)
                            elif c_type == "horizontalBar":
                                fig = px.bar(df_c, x="value", y="name", title=title, orientation="h",
                                             color_discrete_sequence=colors)
                            else:
                                fig = px.bar(df_c, x="name", y="value", title=title,
                                             color_discrete_sequence=colors)
                            # Customize layout margins and backgrounds for a premium feel
                            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#1e293b",
                                              margin=dict(l=10, r=10, t=40, b=10))
                            st.plotly_chart(fig, width="stretch")
                            if desc:
                                st.caption(desc)
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.warning("Chart data not found in this job.")
