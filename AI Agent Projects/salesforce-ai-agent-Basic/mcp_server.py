# -*- coding: utf-8 -*-
"""
mcp_server.py
-------------
Salesforce MCP Server built with FastMCP.

Supports two transports controlled by --transport flag:
  stdio  (default) -- for local use; agent spawns this as a child process
  http             -- for remote use; run as a standalone HTTP service

Usage
=====
  Local (stdio):
      python mcp_server.py
      python mcp_server.py --transport stdio

  Remote (HTTP):
      python mcp_server.py --transport http --port 8000

  Docker / cloud:
      CMD ["python", "mcp_server.py", "--transport", "http", "--port", "8000"]

Tools exposed
=============
  query_soql          -- run any SOQL SELECT
  get_accounts        -- Accounts with optional filters
  get_cases           -- Cases with optional filters
  get_opportunities   -- Opportunities with optional filters
  get_contacts        -- Contacts with optional filters
  get_leads           -- Leads with optional filters
  get_tasks           -- Tasks with optional filters
  aggregate_soql      -- aggregate / COUNT / GROUP BY queries
  get_object_schema   -- field list for any sObject
  create_record       -- create a new record
  update_record       -- update a record by Id
  delete_record       -- delete a record by Id

Auth
====
  Reads from environment (same .env as the main app):
    MCP_MODE=local or remote
    SF_USERNAME, SF_PASSWORD, SF_SECURITY_TOKEN  -> simple-salesforce REST
    SF_USERNAME only                             -> sf CLI fallback
    MCP_API_KEY                                  -> bearer token for HTTP transport
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

try:
    from fastmcp import FastMCP
except ImportError as exc:
    raise ImportError(
        "fastmcp is required: pip install fastmcp"
    ) from exc

from salesforce_client import SalesforceClient

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "Salesforce Tools",
    instructions=(
        "You have full access to a Salesforce org. "
        "Use get_object_schema before writing SOQL for unfamiliar objects. "
        "Prefer the specific get_* tools over raw query_soql when possible."
    ),
)

# Shared client -- instantiated once per process
_sf = SalesforceClient()


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@mcp.tool()
def query_soql(soql: str) -> dict[str, Any]:
    """
    Execute an arbitrary SOQL SELECT statement.
    Use this when the specific get_* tools don't cover the request.
    Always call get_object_schema first if unsure of field names.
    """
    result = _sf.query(soql)
    return {
        "soql": soql,
        "totalSize": result.get("totalSize", 0),
        "records": result.get("records", []),
    }


@mcp.tool()
def get_accounts(
    limit: int = 10,
    industry: str | None = None,
    country: str | None = None,
    name_like: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve Account records with optional filters.
    - industry: filter by Industry field value (e.g. 'Technology')
    - country: filter by BillingCountry
    - name_like: partial match on Account Name
    """
    records = _sf.get_accounts(
        limit=limit, industry=industry, country=country, name_like=name_like
    )
    return {"object": "Account", "totalSize": len(records), "records": records}


@mcp.tool()
def get_cases(
    limit: int = 10,
    status: str | None = None,
    priority: str | None = None,
    is_closed: bool | None = None,
    account_name: str | None = None,
    owner_name: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve Case records with optional filters.
    - status: 'New', 'Working', 'Escalated', etc.
    - priority: 'High', 'Medium', 'Low'
    - is_closed: true for closed cases, false for open
    - account_name: partial match on Account Name
    - owner_name: partial match on Owner Name
    """
    records = _sf.get_cases(
        limit=limit,
        status=status,
        priority=priority,
        is_closed=is_closed,
        account_name=account_name,
        owner_name=owner_name,
    )
    return {"object": "Case", "totalSize": len(records), "records": records}


@mcp.tool()
def get_opportunities(
    limit: int = 10,
    stage: str | None = None,
    account_name: str | None = None,
    min_amount: float | None = None,
) -> dict[str, Any]:
    """
    Retrieve Opportunity records with optional filters.
    - stage: StageName value, e.g. 'Prospecting', 'Closed Won'
    - account_name: partial match on Account Name
    - min_amount: minimum Amount value
    """
    records = _sf.get_opportunities(
        limit=limit, stage=stage, account_name=account_name, min_amount=min_amount
    )
    return {"object": "Opportunity", "totalSize": len(records), "records": records}


@mcp.tool()
def get_contacts(
    limit: int = 10,
    account_name: str | None = None,
    name_like: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve Contact records with optional filters.
    - account_name: partial match on Account Name
    - name_like: partial match on first or last name
    """
    records = _sf.get_contacts(
        limit=limit, account_name=account_name, name_like=name_like
    )
    return {"object": "Contact", "totalSize": len(records), "records": records}


@mcp.tool()
def get_leads(
    limit: int = 10,
    status: str | None = None,
    company: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve unconverted Lead records with optional filters.
    - status: Lead Status value
    - company: partial match on Company name
    """
    records = _sf.get_leads(limit=limit, status=status, company=company)
    return {"object": "Lead", "totalSize": len(records), "records": records}


@mcp.tool()
def get_tasks(
    limit: int = 10,
    status: str | None = None,
    who_name: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve Task / activity records with optional filters.
    - status: 'Not Started', 'In Progress', 'Completed'
    - who_name: partial match on related Contact or Lead name
    """
    records = _sf.get_tasks(limit=limit, status=status, who_name=who_name)
    return {"object": "Task", "totalSize": len(records), "records": records}


@mcp.tool()
def aggregate_soql(soql: str) -> dict[str, Any]:
    """
    Run a SOQL aggregate query (COUNT, SUM, AVG, GROUP BY, HAVING).
    Use for analytics, totals, and grouped summaries.
    """
    records = _sf.aggregate_query(soql)
    return {"soql": soql, "records": records}


@mcp.tool()
def get_object_schema(sobject: str) -> dict[str, Any]:
    """
    Get field names and types for a Salesforce sObject.
    Call this before writing SOQL for an unfamiliar object to avoid
    hallucinating field names.
    """
    schema_text = _sf.get_compact_schema(sobject)
    return {"sobject": sobject, "schema": schema_text}


@mcp.tool()
def create_record(sobject: str, fields: dict[str, Any]) -> dict[str, Any]:
    """
    Create a new Salesforce record.
    - sobject: API name, e.g. 'Case', 'Account', 'CustomObject__c'
    - fields: key-value pairs of field API names and their values
    """
    result = _sf.create_record(sobject, fields)
    return {"action": "created", "sobject": sobject, "result": result}


@mcp.tool()
def update_record(
    sobject: str, record_id: str, fields: dict[str, Any]
) -> dict[str, Any]:
    """
    Update an existing Salesforce record by Id.
    - record_id: 18-character Salesforce record Id
    - fields: fields to update with their new values
    """
    result = _sf.update_record(sobject, record_id, fields)
    return {"action": "updated", "sobject": sobject, "record_id": record_id, "result": result}


@mcp.tool()
def delete_record(sobject: str, record_id: str) -> dict[str, Any]:
    """
    Permanently delete a Salesforce record by Id.
    WARNING: this is irreversible.
    """
    result = _sf.delete_record(sobject, record_id)
    return {"action": "deleted", "sobject": sobject, "record_id": record_id, "result": result}


# ---------------------------------------------------------------------------
# HTTP auth middleware (remote mode only)
# ---------------------------------------------------------------------------

def _make_auth_middleware(api_key: str):
    """
    Returns a simple bearer-token middleware for the FastMCP HTTP app.
    Only applied when MCP_API_KEY is set (i.e. remote mode).
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Health / ping endpoints skip auth
            if request.url.path in ("/health", "/ping", "/"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != api_key:
                return JSONResponse(
                    {"error": "Unauthorized -- invalid or missing MCP_API_KEY"},
                    status_code=401,
                )
            return await call_next(request)

    return BearerAuthMiddleware


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Salesforce MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        help="Transport: 'stdio' for local agent use, 'http' for remote/cloud",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_PORT", "8000")),
        help="Port for HTTP transport (default 8000)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HOST", "0.0.0.0"),
        help="Host for HTTP transport (default 0.0.0.0)",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        logger.info("Starting Salesforce MCP Server (stdio transport)")
        mcp.run(transport="stdio")

    else:
        api_key = os.getenv("MCP_API_KEY", "").strip()
        logger.info(
            "Starting Salesforce MCP Server (HTTP transport) on %s:%d  auth=%s",
            args.host, args.port, "enabled" if api_key else "DISABLED",
        )

        # Attach auth middleware if key is configured
        if api_key:
            middleware_cls = _make_auth_middleware(api_key)
            # FastMCP exposes the underlying Starlette app via .app
            app = mcp.http_app()
            app.add_middleware(middleware_cls)
            import uvicorn
            uvicorn.run(app, host=args.host, port=args.port)
        else:
            logger.warning(
                "MCP_API_KEY not set -- HTTP server is UNAUTHENTICATED. "
                "Set MCP_API_KEY in .env before exposing to the internet."
            )
            mcp.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
