# -*- coding: utf-8 -*-
"""
salesforce_client.py
--------------------
Salesforce data layer.  Supports two authentication backends:

  1. sf CLI (default, MCP_MODE=none or local)
     All communication goes through the `sf` CLI via subprocess.
     No REST credentials needed -- the CLI handles OAuth transparently.
     Requires: SF_USERNAME in env, sf CLI installed on PATH.

  2. simple-salesforce REST API (MCP_MODE=remote or SF_PASSWORD set)
     Connects directly to Salesforce REST API without the CLI.
     Requires: SF_USERNAME, SF_PASSWORD, SF_SECURITY_TOKEN in env.
     Works in any environment (Docker, cloud) without the sf CLI.

Backend is selected automatically:
  - If SF_PASSWORD is set -> use simple-salesforce REST
  - Otherwise            -> use sf CLI

Supports:
  - Arbitrary SOQL queries (read)
  - Record creation, update, deletion
  - Object schema introspection
  - List of all sObjects
  - Aggregate / count queries
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Try to import simple-salesforce (optional -- only needed for remote/REST mode)
try:
    from simple_salesforce import Salesforce as _SimpleSalesforce
    from simple_salesforce.exceptions import SalesforceError as _SFError
    _SIMPLE_SF_AVAILABLE = True
except ImportError:
    _SimpleSalesforce = None  # type: ignore[assignment,misc]
    _SFError = Exception      # type: ignore[assignment,misc]
    _SIMPLE_SF_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(args: list[str], *, timeout: int = 60) -> dict[str, Any]:
    """Run a Salesforce CLI command and return the parsed JSON result block."""
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        shell=False,
        timeout=timeout,
    )
    raw = result.stdout.strip() or result.stderr.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from Salesforce CLI: {raw[:400]}") from exc

    if result.returncode != 0:
        msg = (
            payload.get("message")
            or payload.get("result", {}).get("message", "")
            or raw[:400]
        )
        raise RuntimeError(f"Salesforce CLI error (exit {result.returncode}): {msg}")

    return payload.get("result", payload)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SalesforceClient:
    """
    Salesforce data client with two backends:

      REST (simple-salesforce) -- used when SF_PASSWORD is set in env.
          Works anywhere: Docker, cloud, CI. No sf CLI needed.

      CLI  (sf subprocess)     -- used when SF_PASSWORD is NOT set.
          Works on dev machines where `sf` is installed and authenticated.
    """

    def __init__(self, username: str | None = None):
        self.username: str = (
            username
            or os.getenv("SF_USERNAME")
            or os.getenv("SALESFORCE_USERNAME")
            or ""
        )
        self._cli: str | None = None
        self._schema_cache: dict[str, Any] = {}
        self._rest: Any = None          # simple-salesforce instance (lazy)
        self._use_rest: bool = bool(os.getenv("SF_PASSWORD", "").strip())

        if self._use_rest and not _SIMPLE_SF_AVAILABLE:
            raise RuntimeError(
                "SF_PASSWORD is set (REST mode requested) but simple-salesforce "
                "is not installed. Run: pip install simple-salesforce"
            )

    # ------------------------------------------------------------------
    # Backend selector
    # ------------------------------------------------------------------

    def _backend(self) -> str:
        """Return 'rest' or 'cli'."""
        return "rest" if self._use_rest else "cli"

    # ------------------------------------------------------------------
    # REST backend (simple-salesforce)
    # ------------------------------------------------------------------

    def _get_rest(self) -> Any:
        """Lazy-init the simple-salesforce connection."""
        if self._rest is not None:
            return self._rest
        username  = self.username
        password  = os.getenv("SF_PASSWORD", "").strip()
        token     = os.getenv("SF_SECURITY_TOKEN", "").strip()
        domain    = os.getenv("SF_DOMAIN", "login")   # 'test' for sandboxes
        if not username or not password:
            raise ValueError(
                "REST mode requires SF_USERNAME and SF_PASSWORD in .env. "
                "SF_SECURITY_TOKEN is also required unless your IP is trusted."
            )
        logger.info("Connecting to Salesforce REST API as %s (domain=%s)", username, domain)
        self._rest = _SimpleSalesforce(
            username=username,
            password=password,
            security_token=token,
            domain=domain,
        )
        return self._rest

    def _rest_query(self, soql: str) -> dict[str, Any]:
        sf = self._get_rest()
        logger.debug("REST SOQL: %s", soql)
        result = sf.query_all(soql)
        records = [dict(r) for r in result.get("records", [])]
        # Remove OrderedDict metadata key added by simple-salesforce
        for rec in records:
            rec.pop("attributes", None)
        return {
            "records":   records,
            "totalSize": result.get("totalSize", len(records)),
            "done":      result.get("done", True),
        }

    def _rest_create(self, sobject: str, fields: dict[str, Any]) -> dict[str, Any]:
        sf = self._get_rest()
        obj = getattr(sf, sobject)
        result = obj.create(fields)
        return {"id": result.get("id", ""), "success": result.get("success", False)}

    def _rest_update(self, sobject: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        sf = self._get_rest()
        obj = getattr(sf, sobject)
        obj.update(record_id, fields)
        return {"updated": True, "record_id": record_id}

    def _rest_delete(self, sobject: str, record_id: str) -> dict[str, Any]:
        sf = self._get_rest()
        obj = getattr(sf, sobject)
        obj.delete(record_id)
        return {"deleted": True, "record_id": record_id}

    def _rest_describe(self, sobject: str) -> dict[str, Any]:
        sf = self._get_rest()
        obj = getattr(sf, sobject)
        result = obj.describe()
        return dict(result)

    def _rest_list_objects(self) -> list[str]:
        sf = self._get_rest()
        result = sf.describe()
        return sorted(s["name"] for s in result.get("sobjects", []))

    # ------------------------------------------------------------------
    # CLI path resolution (unchanged)
    # ------------------------------------------------------------------

    def _sf(self) -> str:
        if self._cli:
            return self._cli
        path = (
            shutil.which("sf")
            or shutil.which("sf.cmd")
            or shutil.which("sf.exe")
        )
        if not path:
            fallback = r"C:\Program Files\sf\bin\sf.cmd"
            if os.path.exists(fallback):
                path = fallback
        if not path:
            raise RuntimeError(
                "Salesforce CLI (sf) not found on PATH. "
                "Install it with `npm install -g @salesforce/cli` "
                "then run `sf org login web --set-default`."
            )
        self._cli = path
        return path

    def _org_args(self) -> list[str]:
        if not self.username:
            raise ValueError(
                "No Salesforce username configured. "
                "Set SF_USERNAME in .env or pass username= to SalesforceClient()."
            )
        return ["--target-org", self.username]

    # ------------------------------------------------------------------
    # READ -- SOQL query (routes to REST or CLI)
    # ------------------------------------------------------------------

    def query(self, soql: str) -> dict[str, Any]:
        """Execute a SOQL SELECT and return the raw result dict."""
        if self._use_rest:
            return self._rest_query(soql)
        logger.debug("CLI SOQL: %s", soql)
        result = _run(
            [self._sf(), "data", "query", *self._org_args(), "--query", soql, "--json"]
        )
        if isinstance(result, dict) and "records" in result:
            return result
        return {"records": [], "totalSize": 0, "done": True}

    def query_records(self, soql: str) -> list[dict[str, Any]]:
        """Convenience wrapper -- returns just the records list."""
        return self.query(soql).get("records", [])

    # ------------------------------------------------------------------
    # WRITE -- Create / Update / Delete (routes to REST or CLI)
    # ------------------------------------------------------------------

    def create_record(self, sobject: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Create a new record."""
        if self._use_rest:
            return self._rest_create(sobject, fields)
        values_args: list[str] = []
        for k, v in fields.items():
            values_args += ["--values", f"{k}={v}"]
        result = _run(
            [self._sf(), "data", "create", "record",
             "--sobject", sobject, *values_args, *self._org_args(), "--json"]
        )
        return result if isinstance(result, dict) else {"id": str(result)}

    def update_record(self, sobject: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Update a record by Id."""
        if self._use_rest:
            return self._rest_update(sobject, record_id, fields)
        values_args: list[str] = []
        for k, v in fields.items():
            values_args += ["--values", f"{k}={v}"]
        result = _run(
            [self._sf(), "data", "update", "record",
             "--sobject", sobject, "--record-id", record_id,
             *values_args, *self._org_args(), "--json"]
        )
        return result if isinstance(result, dict) else {}

    def delete_record(self, sobject: str, record_id: str) -> dict[str, Any]:
        """Delete a record by Id."""
        if self._use_rest:
            return self._rest_delete(sobject, record_id)
        result = _run(
            [self._sf(), "data", "delete", "record",
             "--sobject", sobject, "--record-id", record_id,
             *self._org_args(), "--json"]
        )
        return result if isinstance(result, dict) else {}

    # ------------------------------------------------------------------
    # SCHEMA (routes to REST or CLI)
    # ------------------------------------------------------------------

    def list_objects(self) -> list[str]:
        """Return a sorted list of all queryable sObject API names."""
        if self._use_rest:
            return self._rest_list_objects()
        result = _run(
            [self._sf(), "sobject", "list", *self._org_args(), "--json"]
        )
        objects: list[str] = []
        if isinstance(result, list):
            objects = result
        elif isinstance(result, dict):
            objects = result.get("sobjects", result.get("result", []))
        return sorted(set(str(o) for o in objects if o))

    def describe_object(self, sobject: str) -> dict[str, Any]:
        """
        Return schema info for a sObject.
        Cached in-process to avoid redundant calls.
        """
        key = sobject.lower()
        if key in self._schema_cache:
            return self._schema_cache[key]
        if self._use_rest:
            schema = self._rest_describe(sobject)
        else:
            result = _run(
                [self._sf(), "sobject", "describe", "--sobject", sobject,
                 *self._org_args(), "--json"]
            )
            schema = result if isinstance(result, dict) else {}
        self._schema_cache[key] = schema
        return schema

    def get_field_names(self, sobject: str) -> list[str]:
        """Return the list of field API names for a sObject."""
        schema = self.describe_object(sobject)
        fields = schema.get("fields", [])
        return [f["name"] for f in fields if isinstance(f, dict) and "name" in f]

    def get_compact_schema(self, sobject: str) -> str:
        """
        Return a compact, LLM-friendly schema string for a sObject so the
        model can write accurate SOQL without hallucinating field names.
        Format:  FieldName (Type) [nillable]
        """
        schema = self.describe_object(sobject)
        fields = schema.get("fields", [])
        if not fields:
            return f"No schema found for {sobject}."
        lines = [f"Object: {sobject}  ({schema.get('label', '')})"]
        for f in fields[:80]:  # cap at 80 fields to avoid huge context
            nillable = " [nillable]" if f.get("nillable") else ""
            lines.append(f"  {f['name']} ({f.get('type', '?')}){nillable}")
        if len(fields) > 80:
            lines.append(f"  ... and {len(fields) - 80} more fields")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Current user ("me" / "I" / "my" resolution)
    # ------------------------------------------------------------------

    _current_user_cache: dict[str, Any] | None = None

    def get_current_user(self) -> dict[str, Any]:
        """
        Return {'Id': ..., 'Name': ..., 'Username': ...} for the logged-in user.
        Cached in-process. Used to resolve 'me' / 'my' / 'I' in queries.
        """
        if self._current_user_cache is not None:
            return self._current_user_cache
        try:
            if self.username:
                soql = (
                    f"SELECT Id, Name, Username FROM User "
                    f"WHERE Username = '{self.username}' LIMIT 1"
                )
            else:
                soql = "SELECT Id, Name, Username FROM User LIMIT 1"
            records = self.query_records(soql)
            self._current_user_cache = records[0] if records else {}
        except Exception as exc:
            logger.warning("Could not resolve current user: %s", exc)
            self._current_user_cache = {}
        return self._current_user_cache

    def current_user_name(self) -> str:
        """Return the display name of the logged-in user, or '' if unknown."""
        return self.get_current_user().get("Name", "")

    # ------------------------------------------------------------------
    # Convenience presets (used by agent tools)
    # ------------------------------------------------------------------

    def get_accounts(
        self,
        limit: int = 10,
        industry: str | None = None,
        country: str | None = None,
        name_like: str | None = None,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        if industry:
            where_parts.append(f"Industry = '{industry}'")
        if country:
            where_parts.append(f"BillingCountry = '{country}'")
        if name_like:
            where_parts.append(f"Name LIKE '%{name_like}%'")
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        soql = (
            f"SELECT Id, Name, Industry, Type, BillingCountry, BillingCity, "
            f"Phone, Website, AnnualRevenue, NumberOfEmployees, OwnerId "
            f"FROM Account {where} ORDER BY Name LIMIT {limit}"
        )
        return self.query_records(soql)

    def get_cases(
        self,
        limit: int = 10,
        status: str | None = None,
        priority: str | None = None,
        is_closed: bool | None = None,
        account_name: str | None = None,
        owner_name: str | None = None,
        created_by_name: str | None = None,
    ) -> list[dict[str, Any]]:
        # Resolve "me" / "my" / "i" / "mine" to the logged-in user's name
        owner_name      = self._resolve_me(owner_name)
        created_by_name = self._resolve_me(created_by_name)

        where_parts: list[str] = []
        if is_closed is True:
            where_parts.append("IsClosed = true")
        elif is_closed is False:
            where_parts.append("IsClosed = false")
        if status:
            where_parts.append(f"Status = '{status}'")
        if priority:
            where_parts.append(f"Priority = '{priority}'")
        if account_name:
            where_parts.append(f"Account.Name LIKE '%{account_name}%'")
        if owner_name:
            where_parts.append(f"Owner.Name LIKE '%{owner_name}%'")
        if created_by_name:
            where_parts.append(f"CreatedBy.Name LIKE '%{created_by_name}%'")
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        soql = (
            f"SELECT Id, CaseNumber, Subject, Status, Priority, Type, Origin, "
            f"CreatedDate, ClosedDate, Account.Name, Owner.Name, CreatedBy.Name, Description "
            f"FROM Case {where} ORDER BY CreatedDate DESC LIMIT {limit}"
        )
        return self.query_records(soql)

    def _resolve_me(self, value: str | None) -> str | None:
        """Translate 'me' / 'my' / 'i' / 'mine' / 'myself' into the current user's name."""
        if not value:
            return value
        if value.strip().lower() in {"me", "my", "i", "mine", "myself", "current user"}:
            resolved = self.current_user_name()
            logger.info("Resolved '%s' -> current user '%s'", value, resolved)
            return resolved or value
        return value

    def get_opportunities(
        self,
        limit: int = 10,
        stage: str | None = None,
        account_name: str | None = None,
        min_amount: float | None = None,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        if stage:
            where_parts.append(f"StageName = '{stage}'")
        if account_name:
            where_parts.append(f"Account.Name LIKE '%{account_name}%'")
        if min_amount is not None:
            where_parts.append(f"Amount >= {min_amount}")
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        soql = (
            f"SELECT Id, Name, StageName, Amount, CloseDate, Probability, "
            f"Account.Name, Owner.Name, Type, LeadSource "
            f"FROM Opportunity {where} ORDER BY CloseDate ASC LIMIT {limit}"
        )
        return self.query_records(soql)

    def get_contacts(
        self,
        limit: int = 10,
        account_name: str | None = None,
        name_like: str | None = None,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        if account_name:
            where_parts.append(f"Account.Name LIKE '%{account_name}%'")
        if name_like:
            where_parts.append(
                f"(FirstName LIKE '%{name_like}%' OR LastName LIKE '%{name_like}%')"
            )
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        soql = (
            f"SELECT Id, FirstName, LastName, Email, Phone, Title, Department, "
            f"Account.Name, LeadSource "
            f"FROM Contact {where} ORDER BY LastName LIMIT {limit}"
        )
        return self.query_records(soql)

    def get_leads(
        self,
        limit: int = 10,
        status: str | None = None,
        company: str | None = None,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        where_parts.append("IsConverted = false")
        if status:
            where_parts.append(f"Status = '{status}'")
        if company:
            where_parts.append(f"Company LIKE '%{company}%'")
        where = "WHERE " + " AND ".join(where_parts)
        soql = (
            f"SELECT Id, FirstName, LastName, Email, Phone, Company, "
            f"Status, LeadSource, Industry, AnnualRevenue "
            f"FROM Lead {where} ORDER BY CreatedDate DESC LIMIT {limit}"
        )
        return self.query_records(soql)

    def get_tasks(
        self,
        limit: int = 10,
        status: str | None = None,
        who_name: str | None = None,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        if status:
            where_parts.append(f"Status = '{status}'")
        if who_name:
            where_parts.append(f"Who.Name LIKE '%{who_name}%'")
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        soql = (
            f"SELECT Id, Subject, Status, Priority, ActivityDate, "
            f"Who.Name, What.Name, Owner.Name, Description "
            f"FROM Task {where} ORDER BY ActivityDate DESC LIMIT {limit}"
        )
        return self.query_records(soql)

    def aggregate_query(self, soql: str) -> list[dict[str, Any]]:
        """Run an aggregate / COUNT SOQL and return records."""
        return self.query_records(soql)
