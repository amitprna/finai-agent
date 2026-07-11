#!/usr/bin/env python3
"""
Simple Migration Runner
Executes database creation scripts statement by statement on Aurora Serverless v2 PostgreSQL.
Since the RDS Data API does not allow running multi-statement scripts in a single call,
this parser cleanly breaks queries on semicolons while correctly keeping functions/triggers whole.
"""

import os
import re
import boto3
from pathlib import Path
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Load local environment variables if present
load_dotenv(override=True)

# Get configuration properties from environment variables
cluster_arn = os.environ.get("AURORA_CLUSTER_ARN")
secret_arn = os.environ.get("AURORA_SECRET_ARN")
database = os.environ.get("AURORA_DATABASE", "finai")
region = os.environ.get("DEFAULT_AWS_REGION", "us-east-1")

if not cluster_arn or not secret_arn:
    raise ValueError("Missing AURORA_CLUSTER_ARN or AURORA_SECRET_ARN in environment variables")

# Initialize the RDS Data API client
client = boto3.client("rds-data", region_name=region)

# Read the sql migration schema file
migration_path = Path(__file__).parent / "migrations" / "001_schema.sql"
with open(migration_path) as f:
    sql_content = f.read()

# Clean single-line comments (-- comment) out of the SQL file to prevent syntax issues
sql_content = re.sub(r'--.*$', '', sql_content, flags=re.MULTILINE)

# ----------------------------------------------------
# PL/pgSQL Dollar-Quote Statements Parser
# ----------------------------------------------------
# Example input parser logic:
# RDS Data API can execute exactly one SQL command at a time.
# Regular table creation sql statements can be split simply using semicolons (";").
# However, trigger and function structures (written in PL/pgSQL language) contain internal semicolons
# enclosed within dollar-quotes (e.g. $$ BEGIN a = 1; END; $$).
# Splitting on every semicolon would split a single function body into multiple invalid fragments.
#
# Solution:
# 1. We split the SQL file by double dollar symbols "$$".
# 2. Odd-numbered split indices represent content that was INSIDE function/trigger blocks.
#    We restore their "$$" quotes and keep their internal code intact (ignoring internal semicolons).
# 3. Even-numbered split indices represent statements OUTSIDE dollar quotes.
#    It is safe to split these segments by semicolons (";") to extract individual queries.
# 4. Re-assemble segments and build the executable queries list.

parts = sql_content.split("$$")
statements = []
current_statement = ""

for idx, part in enumerate(parts):
    if idx % 2 == 1:
        # Odd index: we are inside a dollar-quoted function block. Append as is.
        current_statement += "$$" + part + "$$"
    else:
        # Even index: we are outside function blocks. Semicolons denote statement completion.
        sub_parts = part.split(";")
        
        # Merge the first split section with the active statement
        current_statement += sub_parts[0]
        
        # All remaining segments are separate, self-contained queries
        for sub_part in sub_parts[1:]:
            stmt_clean = current_statement.strip()
            if stmt_clean:
                statements.append(stmt_clean)
            current_statement = sub_part

# Append remaining SQL if any
stmt_clean = current_statement.strip()
if stmt_clean:
    statements.append(stmt_clean)

print("Running database migrations...")
print("=" * 50)

success_count = 0
error_count = 0

# Execute each statement one by one on the Serverless DB
for i, stmt in enumerate(statements, 1):
    # Determine the query category for verbose outputs
    stmt_type = "statement"
    stmt_upper = stmt.upper()
    if "CREATE TABLE" in stmt_upper:
        stmt_type = "table"
    elif "CREATE INDEX" in stmt_upper:
        stmt_type = "index"
    elif "CREATE TRIGGER" in stmt_upper:
        stmt_type = "trigger"
    elif "CREATE FUNCTION" in stmt_upper:
        stmt_type = "function"
    elif "CREATE EXTENSION" in stmt_upper:
        stmt_type = "extension"

    # Fetch the first non-empty query line to display in logging console
    first_line = next(line for line in stmt.split("\n") if line.strip())[:60]
    print(f"\n[{i}/{len(statements)}] Creating {stmt_type}...")
    print(f"    {first_line}...")

    try:
        # Call RDS Data API to execute the statement
        response = client.execute_statement(
            resourceArn=cluster_arn, secretArn=secret_arn, database=database, sql=stmt
        )
        print(f"    [OK] Success")
        success_count += 1

    except ClientError as e:
        error_msg = e.response["Error"]["Message"]
        # If the object already exists in the schema, log warning and skip gracefully
        if "already exists" in error_msg.lower():
            print(f"    [WARN] Already exists (skipping)")
            success_count += 1
        else:
            print(f"    [ERROR] Error: {error_msg[:100]}")
            error_count += 1

print("\n" + "=" * 50)
print(f"Migration complete: {success_count} successful, {error_count} errors")

if error_count == 0:
    print("\n[OK] All migrations completed successfully!")
    print("\n[INFO] Next steps:")
    print("1. Load seed data: uv run seed_data.py")
    print("2. Test database operations: uv run test_db.py")
else:
    print(f"\n[WARN] Some statements failed. Check errors above.")
