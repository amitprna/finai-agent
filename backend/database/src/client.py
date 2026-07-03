"""
Aurora Data API Client Wrapper
Provides a simple interface for database operations using boto3 and AWS RDS Data API.
"""

import boto3
import json
import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import date, datetime
from decimal import Decimal
from botocore.exceptions import ClientError
import logging

# ----------------------------------------------------
# Environment Variables & Setup
# ----------------------------------------------------
# Try to load .env file if it exists (useful for local testing)
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass  # dotenv not installed, continue without it (AWS Lambda has env vars set directly)

logger = logging.getLogger(__name__)


class DataAPIClient:
    """
    Wrapper for AWS RDS Data API to simplify database operations.
    The Data API allows executing SQL queries over HTTP, meaning you do not need
    a persistent VPC connection pooling setup (like pg8000/psycopg2) inside Lambda.
    """

    def __init__(
        self,
        cluster_arn: str = None,
        secret_arn: str = None,
        database: str = None,
        region: str = None,
    ):
        """
        Initialize the Data API client.

        Args:
            cluster_arn: Aurora cluster ARN (or read from env: AURORA_CLUSTER_ARN)
            secret_arn: Secrets Manager Secret ARN (or read from env: AURORA_SECRET_ARN)
            database: Database name (or read from env: AURORA_DATABASE, defaults to 'finai')
            region: AWS region (or read from env: DEFAULT_AWS_REGION)
        """
        # Read parameters from argument list or fall back to system environment variables
        self.cluster_arn = cluster_arn or os.environ.get("AURORA_CLUSTER_ARN")
        self.secret_arn = secret_arn or os.environ.get("AURORA_SECRET_ARN")
        self.database = database or os.environ.get("AURORA_DATABASE", "finai")

        # Raise exception if required credentials are missing
        if not self.cluster_arn or not self.secret_arn:
            raise ValueError(
                "Missing required Aurora configuration. "
                "Set AURORA_CLUSTER_ARN and AURORA_SECRET_ARN environment variables."
            )

        # Initialize boto3 client for RDS Data API
        self.region = os.environ.get("DEFAULT_AWS_REGION", "us-east-1")
        self.client = boto3.client("rds-data", region_name=self.region)

    def execute(self, sql: str, parameters: List[Dict] = None) -> Dict:
        """
        Execute a single SQL statement (INSERT, UPDATE, DELETE, or SELECT)

        Args:
            sql: SQL statement to execute
            parameters: Optional list of parameters for prepared statements to prevent SQL injection

        Returns:
            Dictionary containing the raw response from AWS RDS Data API
        """
        try:
            # Build parameter dictionary for boto3 call
            kwargs = {
                "resourceArn": self.cluster_arn,
                "secretArn": self.secret_arn,
                "database": self.database,
                "sql": sql,
                "includeResultMetadata": True,  # Ensures column names/types are returned
            }

            if parameters:
                kwargs["parameters"] = parameters

            # Call AWS Data API
            response = self.client.execute_statement(**kwargs)
            return response

        except ClientError as e:
            logger.error(f"Database execute error: {e}")
            raise

    def query(self, sql: str, parameters: List[Dict] = None) -> List[Dict]:
        """
        Execute a SELECT query and return results as a list of user-friendly dicts.

        Args:
            sql: SELECT statement
            parameters: Optional parameter dictionaries

        Returns:
            List of dictionaries, e.g., [{"col1": val1, "col2": val2}]
        """
        # Execute the statement
        response = self.execute(sql, parameters)

        if "records" not in response:
            return []

        # Extract column names from metadata to map list index to keys
        columns = [col["name"] for col in response.get("columnMetadata", [])]

        # Convert raw RDS Data API list records to dictionaries
        results = []
        for record in response["records"]:
            row = {}
            for i, col in enumerate(columns):
                # Extract and cast database-specific data types
                value = self._extract_value(record[i])
                row[col] = value
            results.append(row)

        return results

    def query_one(self, sql: str, parameters: List[Dict] = None) -> Optional[Dict]:
        """
        Execute a SELECT query and return the first result or None.

        Args:
            sql: SELECT query string
            parameters: Optional parameter bindings

        Returns:
            Dictionary of the first record, or None if empty
        """
        results = self.query(sql, parameters)
        return results[0] if results else None

    def insert(self, table: str, data: Dict, returning: str = None) -> str:
        """
        Helper method to construct and execute an INSERT statement.

        Args:
            table: Target database table name
            data: Key-value dictionary of values to insert
            returning: Optional column name to return (e.g. 'id')

        Returns:
            The value of the RETURNING column (e.g. newly generated UUID string) or None
        """
        columns = list(data.keys())
        placeholders = []

        # Automatically insert PostgreSQL type casts based on Python types
        for col in columns:
            val = data[col]
            if isinstance(val, (dict, list)):
                placeholders.append(f":{col}::jsonb")
            elif isinstance(val, Decimal):
                placeholders.append(f":{col}::numeric")
            elif isinstance(val, date) and not isinstance(val, datetime):
                placeholders.append(f":{col}::date")
            elif isinstance(val, datetime):
                placeholders.append(f":{col}::timestamp")
            else:
                placeholders.append(f":{col}")

        # Construct SQL
        sql = f"""
            INSERT INTO {table} ({", ".join(columns)})
            VALUES ({", ".join(placeholders)})
        """

        if returning:
            sql += f" RETURNING {returning}"

        # Convert data dictionary to RDS Data API parameter array format
        parameters = self._build_parameters(data)
        response = self.execute(sql, parameters)

        # Parse returning value
        if returning and response.get("records"):
            return self._extract_value(response["records"][0][0])
        return None

    def update(self, table: str, data: Dict, where: str, where_params: Dict = None) -> int:
        """
        Helper method to construct and execute an UPDATE statement.

        Args:
            table: Target database table name
            data: Key-value updates dictionary
            where: WHERE condition string (e.g., "id = :id")
            where_params: Parameter bindings for the WHERE clause

        Returns:
            Integer representing the number of updated records
        """
        set_parts = []
        for col, val in data.items():
            if isinstance(val, (dict, list)):
                set_parts.append(f"{col} = :{col}::jsonb")
            elif isinstance(val, Decimal):
                set_parts.append(f"{col} = :{col}::numeric")
            elif isinstance(val, date) and not isinstance(val, datetime):
                set_parts.append(f"{col} = :{col}::date")
            elif isinstance(val, datetime):
                set_parts.append(f"{col} = :{col}::timestamp")
            else:
                set_parts.append(f"{col} = :{col}")

        set_clause = ", ".join(set_parts)

        # Construct SQL
        sql = f"""
            UPDATE {table}
            SET {set_clause}
            WHERE {where}
        """

        # Merge data parameters and where parameters
        all_params = {**data, **(where_params or {})}
        parameters = self._build_parameters(all_params)

        response = self.execute(sql, parameters)
        return response.get("numberOfRecordsUpdated", 0)

    def delete(self, table: str, where: str, where_params: Dict = None) -> int:
        """
        Helper method to construct and execute a DELETE statement.

        Args:
            table: Target database table name
            where: WHERE condition string
            where_params: Parameter bindings for the WHERE clause

        Returns:
            Integer representing the number of deleted records
        """
        sql = f"DELETE FROM {table} WHERE {where}"
        parameters = self._build_parameters(where_params) if where_params else None

        response = self.execute(sql, parameters)
        return response.get("numberOfRecordsUpdated", 0)

    def begin_transaction(self) -> str:
        """
        Begin a multi-statement transaction.

        Returns:
            String representing the transaction ID
        """
        response = self.client.begin_transaction(
            resourceArn=self.cluster_arn, secretArn=self.secret_arn, database=self.database
        )
        return response["transactionId"]

    def commit_transaction(self, transaction_id: str):
        """Commit an active transaction by ID"""
        self.client.commit_transaction(
            resourceArn=self.cluster_arn, secretArn=self.secret_arn, transactionId=transaction_id
        )

    def rollback_transaction(self, transaction_id: str):
        """Rollback an active transaction by ID"""
        self.client.rollback_transaction(
            resourceArn=self.cluster_arn, secretArn=self.secret_arn, transactionId=transaction_id
        )

    def _build_parameters(self, data: Dict) -> List[Dict]:
        """
        Convert python dictionary values into RDS Data API specific typing formats.
        The Data API expects strongly typed variables (e.g. stringValue, longValue).
        """
        if not data:
            return []

        parameters = []
        for key, value in data.items():
            param = {"name": key}

            if value is None:
                param["value"] = {"isNull": True}
            elif isinstance(value, bool):
                param["value"] = {"booleanValue": value}
            elif isinstance(value, int):
                param["value"] = {"longValue": value}
            elif isinstance(value, float):
                param["value"] = {"doubleValue": value}
            elif isinstance(value, Decimal):
                param["value"] = {"stringValue": str(value)}
            elif isinstance(value, (date, datetime)):
                param["value"] = {"stringValue": value.isoformat()}
            elif isinstance(value, (dict, list)):
                param["value"] = {"stringValue": json.dumps(value)}
            else:
                param["value"] = {"stringValue": str(value)}

            parameters.append(param)

        return parameters

    def _extract_value(self, field: Dict) -> Any:
        """
        Parse and return values from RDS Data API field structures.
        Converts strongly typed database outputs back to standard Python types.
        """
        if field.get("isNull"):
            return None
        elif "booleanValue" in field:
            return field["booleanValue"]
        elif "longValue" in field:
            return field["longValue"]
        elif "doubleValue" in field:
            return field["doubleValue"]
        elif "stringValue" in field:
            value = field["stringValue"]
            # Auto-deserialize JSON values from the database (like dicts and lists)
            if value and value[0] in ["{", "["]:
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    pass
            return value
        elif "blobValue" in field:
            return field["blobValue"]
        else:
            return None
