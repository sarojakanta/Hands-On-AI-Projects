"""
agent.py - Agentic Salesforce assistant built on OpenAI function-calling.

MCP_MODE switching (one env var, no code change needed):
  MCP_MODE=none   -> SalesforceTools direct (default)
  MCP_MODE=local  -> LocalMCPClient stdio child process
  MCP_MODE=remote -> RemoteMCPClient HTTP cloud server
"""

from __future__ import annotations

import json
import logging
import os
import textwrap
from collections import defaultdict
from typing import Any, Generator

from dotenv import load_dotenv

from salesforce_client import SalesforceClient

load_dotenv()

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]
    _OPENAI_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM provider configuration
# ---------------------------------------------------------------------------
# All providers below expose an OpenAI-compatible API, so the same openai
# client library works for all of them -- only the base URL / key / model
# change. LLM_PROVIDER picks the preset; LLM_BASE_URL overrides for 'custom'.

LLM_PROVIDER_URLS: dict[str, str | None] = {
    "openai":   None,  # library default: https://api.openai.com/v1
    "groq":     "https://api.groq.com/openai/v1",
    "claude":   "https://api.anthropic.com/v1/",   # Anthropic OpenAI-compat endpoint
    "together": "https://api.together.xyz/v1",
    "ollama":   "http://localhost:11434/v1",
    "gemini":   "https://generativelanguage.googleapis.com/v1beta/openai/",
    "custom":   None,  # uses LLM_BASE_URL verbatim
}

DEFAULT_MODELS: dict[str, str] = {
    "openai":   "gpt-4o-mini",
    "groq":     "llama-3.3-70b-versatile",
    "claude":   "claude-sonnet-4-20250514",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "ollama":   "llama3.1",
    "gemini":   "gemini-2.0-flash",
    "custom":   "gpt-4o-mini",
}


def _llm_provider() -> str:
    return (os.getenv("LLM_PROVIDER") or "openai").strip().lower()


def _llm_api_key() -> str:
    # New var first, then fall back to legacy OPENAI_API_KEY
    return (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()


def _llm_base_url() -> str | None:
    provider = _llm_provider()
    # custom provider: use explicit LLM_BASE_URL
    if provider == "custom":
        return (os.getenv("LLM_BASE_URL") or "").strip() or None
    # explicit LLM_BASE_URL always wins if set
    explicit = (os.getenv("LLM_BASE_URL") or "").strip()
    if explicit:
        return explicit
    return LLM_PROVIDER_URLS.get(provider)


def _llm_model() -> str:
    provider = _llm_provider()
    return (
        os.getenv("LLM_MODEL")
        or os.getenv("OPENAI_MODEL")          # legacy fallback
        or DEFAULT_MODELS.get(provider, "gpt-4o-mini")
    )


def llm_config_summary() -> dict[str, Any]:
    """Return the active LLM config for /api/health (no secrets)."""
    key = _llm_api_key()
    return {
        "provider": _llm_provider(),
        "model":    _llm_model(),
        "base_url": _llm_base_url() or "default",
        "has_key":  bool(key and not key.lower().startswith("your_") and len(key) >= 20),
    }


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_soql",
            "description": (
                "Execute an arbitrary SOQL SELECT statement against Salesforce. "
                "Use this when the other specialised tools do not cover the request. "
                "Always prefer exact field names -- use get_object_schema first if unsure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "soql": {"type": "string", "description": "A valid SOQL SELECT statement."}
                },
                "required": ["soql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_accounts",
            "description": "Retrieve Account records with optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit":     {"type": "integer", "default": 10},
                    "industry":  {"type": "string",  "description": "Filter by Industry value."},
                    "country":   {"type": "string",  "description": "Filter by BillingCountry."},
                    "name_like": {"type": "string",  "description": "Partial match on Account Name."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cases",
            "description": (
                "Retrieve Case records with optional filters. "
                "For 'raised by / created by / opened by <person>' use created_by_name. "
                "For 'owned by / assigned to <person>' use owner_name. "
                "You may pass the literal word 'me' for the current user -- it is resolved automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit":           {"type": "integer", "default": 10},
                    "status":          {"type": "string",  "description": "Case Status e.g. 'New', 'Working', 'Escalated'."},
                    "priority":        {"type": "string",  "description": "Case Priority e.g. 'High', 'Medium', 'Low'."},
                    "is_closed":       {"type": "boolean", "description": "true for closed, false for open."},
                    "account_name":    {"type": "string",  "description": "Partial match on Account Name."},
                    "owner_name":      {"type": "string",  "description": "Owner/assignee name, or 'me' for current user."},
                    "created_by_name": {"type": "string",  "description": "Creator (who raised it) name, or 'me' for current user."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_opportunities",
            "description": "Retrieve Opportunity records with optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit":        {"type": "integer", "default": 10},
                    "stage":        {"type": "string",  "description": "StageName e.g. 'Prospecting', 'Closed Won'."},
                    "account_name": {"type": "string",  "description": "Partial match on Account Name."},
                    "min_amount":   {"type": "number",  "description": "Minimum opportunity amount."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_contacts",
            "description": "Retrieve Contact records with optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit":        {"type": "integer", "default": 10},
                    "account_name": {"type": "string",  "description": "Partial match on Account Name."},
                    "name_like":    {"type": "string",  "description": "Partial match on first or last name."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_leads",
            "description": "Retrieve unconverted Lead records with optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit":   {"type": "integer", "default": 10},
                    "status":  {"type": "string",  "description": "Lead Status value."},
                    "company": {"type": "string",  "description": "Partial match on Company name."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tasks",
            "description": "Retrieve Task/activity records with optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit":    {"type": "integer", "default": 10},
                    "status":   {"type": "string",  "description": "Task Status e.g. 'Not Started', 'Completed'."},
                    "who_name": {"type": "string",  "description": "Partial match on related Contact or Lead name."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate_soql",
            "description": "Run a SOQL aggregate query (COUNT, SUM, AVG, GROUP BY, HAVING).",
            "parameters": {
                "type": "object",
                "properties": {
                    "soql": {"type": "string", "description": "A valid SOQL aggregate SELECT statement."}
                },
                "required": ["soql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_object_schema",
            "description": (
                "Get the field names and types for a Salesforce sObject. "
                "Call this before writing SOQL for an unfamiliar object."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sobject": {"type": "string", "description": "API name e.g. 'Account', 'Case', 'CustomObject__c'."}
                },
                "required": ["sobject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_record",
            "description": "Create a new Salesforce record.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sobject": {"type": "string", "description": "API name of the sObject."},
                    "fields":  {"type": "object", "description": "Key-value pairs of field names and values.",
                                "additionalProperties": {"type": "string"}},
                },
                "required": ["sobject", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_record",
            "description": "Update an existing Salesforce record by Id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sobject":   {"type": "string"},
                    "record_id": {"type": "string", "description": "18-character Salesforce record Id."},
                    "fields":    {"type": "object", "description": "Fields to update.",
                                  "additionalProperties": {"type": "string"}},
                },
                "required": ["sobject", "record_id", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_record",
            "description": "Permanently delete a Salesforce record by Id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sobject":   {"type": "string"},
                    "record_id": {"type": "string", "description": "18-character Salesforce record Id."},
                },
                "required": ["sobject", "record_id"],
            },
        },
    },
]

SYSTEM_PROMPT = textwrap.dedent(
    "You are an intelligent Salesforce AI assistant with full access to the connected "
    "Salesforce org through a set of tools.\n\n"
    "Capabilities: query any sObject with filters and aggregations, create/update/delete "
    "records, introspect schemas, run analytics.\n\n"
    "Guidelines:\n"
    "- Always choose the most specific tool first (get_cases, get_accounts, etc.).\n"
    "- Call get_object_schema before writing SOQL for an unfamiliar object.\n"
    "- Chain tool calls for multi-step questions.\n"
    "- When the user says 'me', 'my', 'I', or 'mine', it refers to the current user "
    "named below. Pass that exact name (or the literal 'me') to owner_name / created_by_name.\n"
    "- 'raised by' / 'created by' / 'opened by' maps to created_by_name; "
    "'owned by' / 'assigned to' maps to owner_name.\n"
    "- 'escalated' means Status = 'Escalated' (there is NO 'Escalated' field). "
    "'open' means IsClosed = false (there is NO Status = 'Open'). "
    "Never invent field names -- if unsure of a field, call get_object_schema first.\n"
    "- Return clear structured summaries with markdown tables where helpful.\n"
    "- Confirm destructive operations (update/delete) in your reply.\n"
    "- Do NOT show or mention SOQL, queries, field API names, or technical "
    "internals in your answer. Reply in plain business language only."
)


def _build_system_prompt(sf_client: Any) -> str:
    """Append the current user's identity so the LLM can resolve 'me'/'my'/'I'."""
    try:
        name = sf_client.current_user_name()
    except Exception:
        name = ""
    if name:
        return SYSTEM_PROMPT + f"\n\nThe current user is: {name}"
    return SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Direct tool dispatcher (MCP_MODE=none)
# ---------------------------------------------------------------------------

class SalesforceTools:
    def __init__(self, client: SalesforceClient):
        self.client = client

    def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        try:
            return getattr(self, f"_tool_{name}")(**args)
        except AttributeError:
            return {"error": f"Unknown tool: {name}"}
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return {"error": str(exc)}

    def _tool_query_soql(self, soql: str) -> dict[str, Any]:
        result = self.client.query(soql)
        return {"soql": soql, "totalSize": result.get("totalSize", 0), "records": result.get("records", [])}

    def _tool_get_accounts(self, limit: int = 10, industry: str | None = None,
                           country: str | None = None, name_like: str | None = None) -> dict[str, Any]:
        records = self.client.get_accounts(limit=limit, industry=industry, country=country, name_like=name_like)
        return {"object": "Account", "totalSize": len(records), "records": records}

    def _tool_get_cases(self, limit: int = 10, status: str | None = None,
                        priority: str | None = None, is_closed: bool | None = None,
                        account_name: str | None = None, owner_name: str | None = None,
                        created_by_name: str | None = None) -> dict[str, Any]:
        records = self.client.get_cases(limit=limit, status=status, priority=priority,
                                        is_closed=is_closed, account_name=account_name,
                                        owner_name=owner_name, created_by_name=created_by_name)
        return {"object": "Case", "totalSize": len(records), "records": records}

    def _tool_get_opportunities(self, limit: int = 10, stage: str | None = None,
                                account_name: str | None = None, min_amount: float | None = None) -> dict[str, Any]:
        records = self.client.get_opportunities(limit=limit, stage=stage,
                                                account_name=account_name, min_amount=min_amount)
        return {"object": "Opportunity", "totalSize": len(records), "records": records}

    def _tool_get_contacts(self, limit: int = 10, account_name: str | None = None,
                           name_like: str | None = None) -> dict[str, Any]:
        records = self.client.get_contacts(limit=limit, account_name=account_name, name_like=name_like)
        return {"object": "Contact", "totalSize": len(records), "records": records}

    def _tool_get_leads(self, limit: int = 10, status: str | None = None,
                        company: str | None = None) -> dict[str, Any]:
        records = self.client.get_leads(limit=limit, status=status, company=company)
        return {"object": "Lead", "totalSize": len(records), "records": records}

    def _tool_get_tasks(self, limit: int = 10, status: str | None = None,
                        who_name: str | None = None) -> dict[str, Any]:
        records = self.client.get_tasks(limit=limit, status=status, who_name=who_name)
        return {"object": "Task", "totalSize": len(records), "records": records}

    def _tool_aggregate_soql(self, soql: str) -> dict[str, Any]:
        records = self.client.aggregate_query(soql)
        return {"soql": soql, "records": records}

    def _tool_get_object_schema(self, sobject: str) -> dict[str, Any]:
        return {"sobject": sobject, "schema": self.client.get_compact_schema(sobject)}

    def _tool_create_record(self, sobject: str, fields: dict[str, Any]) -> dict[str, Any]:
        return {"action": "created", "sobject": sobject, "result": self.client.create_record(sobject, fields)}

    def _tool_update_record(self, sobject: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        return {"action": "updated", "sobject": sobject, "record_id": record_id,
                "result": self.client.update_record(sobject, record_id, fields)}

    def _tool_delete_record(self, sobject: str, record_id: str) -> dict[str, Any]:
        return {"action": "deleted", "sobject": sobject, "record_id": record_id,
                "result": self.client.delete_record(sobject, record_id)}


# ---------------------------------------------------------------------------
# Local MCP client (MCP_MODE=local) -- stdio child process
# ---------------------------------------------------------------------------

class LocalMCPClient:
    """Dispatches tool calls to a local MCP server via stdio (child process)."""

    def __init__(self, script: str | None = None):
        self._script = script or os.getenv("MCP_SERVER_SCRIPT", "mcp_server.py")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from fastmcp import Client
        except ImportError as exc:
            raise RuntimeError("fastmcp required for MCP_MODE=local: pip install fastmcp") from exc
        import sys
        script_path = os.path.abspath(self._script)
        if not os.path.exists(script_path):
            raise RuntimeError(f"MCP server script not found: {script_path}")
        self._client = Client(f"{sys.executable} {script_path}")
        logger.info("LocalMCPClient: spawning %s", script_path)
        return self._client

    def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        import asyncio
        async def _call():
            async with self._get_client() as client:
                result = await client.call_tool(name, args)
                if result and hasattr(result[0], "text"):
                    try:
                        return json.loads(result[0].text)
                    except (json.JSONDecodeError, AttributeError):
                        return {"result": result[0].text}
                return {"result": str(result)}
        try:
            loop = asyncio.new_event_loop()
            return loop.run_until_complete(_call())
        except Exception as exc:
            logger.exception("LocalMCPClient.dispatch(%s) failed", name)
            return {"error": str(exc)}
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Remote MCP client (MCP_MODE=remote) -- HTTP cloud server
# ---------------------------------------------------------------------------

class RemoteMCPClient:
    """Dispatches tool calls to a remote HTTP MCP server."""

    def __init__(self, url: str | None = None, api_key: str | None = None):
        self._url     = (url or os.getenv("MCP_SERVER_URL", "")).rstrip("/")
        self._api_key = api_key or os.getenv("MCP_API_KEY", "")
        if not self._url:
            raise ValueError(
                "MCP_MODE=remote requires MCP_SERVER_URL in .env. "
                "Example: MCP_SERVER_URL=https://your-mcp-server.onrender.com"
            )

    def _get_client(self) -> Any:
        try:
            from fastmcp import Client
        except ImportError as exc:
            raise RuntimeError("fastmcp required for MCP_MODE=remote: pip install fastmcp") from exc
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        return Client(self._url, headers=headers)

    def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        import asyncio
        async def _call():
            async with self._get_client() as client:
                result = await client.call_tool(name, args)
                if result and hasattr(result[0], "text"):
                    try:
                        return json.loads(result[0].text)
                    except (json.JSONDecodeError, AttributeError):
                        return {"result": result[0].text}
                return {"result": str(result)}
        try:
            loop = asyncio.new_event_loop()
            return loop.run_until_complete(_call())
        except Exception as exc:
            logger.exception("RemoteMCPClient.dispatch(%s) failed", name)
            return {"error": str(exc)}
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# MCP factory
# ---------------------------------------------------------------------------

def make_tool_dispatcher(sf_client: Any) -> Any:
    """
    Read MCP_MODE from env and return the right tool dispatcher.
    none   -> SalesforceTools (direct, default)
    local  -> LocalMCPClient  (stdio child process)
    remote -> RemoteMCPClient (HTTP cloud server)
    """
    mode = os.getenv("MCP_MODE", "none").strip().lower()
    if mode == "local":
        script = os.getenv("MCP_SERVER_SCRIPT", "mcp_server.py")
        logger.info("MCP_MODE=local -> LocalMCPClient (script: %s)", script)
        return LocalMCPClient(script=script)
    if mode == "remote":
        url     = os.getenv("MCP_SERVER_URL", "")
        api_key = os.getenv("MCP_API_KEY", "")
        logger.info("MCP_MODE=remote -> RemoteMCPClient (url: %s)", url)
        return RemoteMCPClient(url=url, api_key=api_key)
    logger.info("MCP_MODE=none -> SalesforceTools (direct)")
    return SalesforceTools(sf_client)


# ---------------------------------------------------------------------------
# Conversation memory
# ---------------------------------------------------------------------------

class ConversationMemory:
    """Per-session message history with a sliding context window."""

    MAX_TURNS = 20

    def __init__(self):
        self._sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def get(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._sessions[session_id])

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        self._sessions[session_id].append(message)
        msgs       = self._sessions[session_id]
        system     = [m for m in msgs if m["role"] == "system"]
        non_system = [m for m in msgs if m["role"] != "system"]
        if len(non_system) > self.MAX_TURNS * 2:
            non_system = non_system[-(self.MAX_TURNS * 2):]
        self._sessions[session_id] = system + non_system

    def clear(self, session_id: str) -> None:
        self._sessions[session_id] = []

    def reset(self, session_id: str) -> None:
        self.clear(session_id)


_memory = ConversationMemory()


# ---------------------------------------------------------------------------
# Fallback agent (no OpenAI key)
# ---------------------------------------------------------------------------

class FallbackAgent:
    """Simple rule-based agent used when no OpenAI key is available."""

    KEYWORD_MAP = {
        ("opportunity", "deal", "pipeline"): "opportunity",
        ("contact", "person", "people"):     "contact",
        ("lead",):                           "lead",
        ("task", "activity", "todo"):        "task",
        ("case", "ticket", "issue", "support"): "case",
        ("account", "company", "companies"): "account",
    }

    def __init__(self, client: SalesforceClient):
        self.client = client

    def _detect_object(self, prompt: str) -> str:
        p = prompt.lower()
        for keywords, obj in self.KEYWORD_MAP.items():
            if any(k in p for k in keywords):
                return obj
        return "account"

    def _detect_filters(self, prompt: str) -> dict[str, Any]:
        p = prompt.lower()
        filters: dict[str, Any] = {}
        if "open" in p or "not closed" in p:
            filters["is_closed"] = False
        if "closed" in p and "not closed" not in p:
            filters["is_closed"] = True
        for pri in ("high", "medium", "low"):
            if pri in p:
                filters["priority"] = pri.title()
        return filters

    def handle(self, prompt: str) -> dict[str, Any]:
        obj     = self._detect_object(prompt)
        filters = self._detect_filters(prompt)
        records: list[dict[str, Any]] = []
        soql_hint = ""
        try:
            if obj == "case":
                records   = self.client.get_cases(limit=10, **filters)
                soql_hint = "Cases with filters: " + json.dumps(filters)
            elif obj == "opportunity":
                records   = self.client.get_opportunities(limit=10)
                soql_hint = "SELECT Id, Name, StageName, Amount FROM Opportunity LIMIT 10"
            elif obj == "contact":
                records   = self.client.get_contacts(limit=10)
                soql_hint = "SELECT Id, FirstName, LastName, Email FROM Contact LIMIT 10"
            elif obj == "lead":
                records   = self.client.get_leads(limit=10)
                soql_hint = "SELECT Id, FirstName, LastName, Company FROM Lead LIMIT 10"
            elif obj == "task":
                records   = self.client.get_tasks(limit=10)
                soql_hint = "SELECT Id, Subject, Status FROM Task LIMIT 10"
            else:
                records   = self.client.get_accounts(limit=10)
                soql_hint = "SELECT Id, Name, Industry FROM Account LIMIT 10"
        except Exception as exc:
            return {"answer": f"Salesforce query failed: {exc}", "records": [],
                    "soql": soql_hint, "tool_calls": [], "error": str(exc)}

        return {
            "answer":     self._format(obj, records),
            "records":    records,
            "soql":       soql_hint,
            "tool_calls": [{"tool": f"get_{obj}s", "args": filters}],
            "object":     obj.title(),
        }

    def _format(self, obj: str, records: list[dict]) -> str:
        if not records:
            return f"No {obj} records found."
        lines = [f"Found **{len(records)}** {obj.title()} record(s):\n"]
        for r in records[:10]:
            if obj == "case":
                lines.append(
                    f"- **Case {r.get('CaseNumber','?')}**: {r.get('Subject','--')} "
                    f"| Status: {r.get('Status','?')} | Priority: {r.get('Priority','?')} "
                    f"| Account: {(r.get('Account') or {}).get('Name','--')}"
                )
            elif obj == "opportunity":
                amt = r.get("Amount")
                lines.append(
                    f"- **{r.get('Name','?')}**: {r.get('StageName','?')} "
                    f"| Amount: {'${:,.0f}'.format(amt) if amt else '--'} "
                    f"| Close: {r.get('CloseDate','?')}"
                )
            elif obj == "contact":
                lines.append(
                    f"- **{r.get('FirstName','')} {r.get('LastName','')}**: "
                    f"{r.get('Title','--')} @ {(r.get('Account') or {}).get('Name','--')} "
                    f"| {r.get('Email','--')}"
                )
            elif obj == "lead":
                lines.append(
                    f"- **{r.get('FirstName','')} {r.get('LastName','')}** "
                    f"@ {r.get('Company','--')}: {r.get('Status','?')}"
                )
            elif obj == "task":
                lines.append(
                    f"- **{r.get('Subject','?')}**: {r.get('Status','?')} "
                    f"| Due: {r.get('ActivityDate','--')}"
                )
            else:
                lines.append(
                    f"- **{r.get('Name','?')}**: {r.get('Industry','--')} "
                    f"| {r.get('BillingCountry','--')}"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main agentic class
# ---------------------------------------------------------------------------

class SalesforceAgent:
    """
    Agentic Salesforce assistant.
    handle_request()  -> synchronous result dict
    stream_request()  -> NDJSON generator for SSE streaming
    """

    MAX_TOOL_ROUNDS = 6

    def __init__(self, username: str | None = None):
        self.sf       = SalesforceClient(username=username)
        self.tools    = make_tool_dispatcher(self.sf)
        self._openai: Any = None
        self._fallback    = FallbackAgent(self.sf)

    # ------------------------------------------------------------------
    # OpenAI lazy init
    # ------------------------------------------------------------------

    def _get_openai(self) -> Any:
        if self._openai is not None:
            return self._openai
        if not _OPENAI_AVAILABLE:
            return None
        api_key = _llm_api_key()
        if not api_key or api_key.lower().startswith("your_") or len(api_key) < 20:
            return None
        base_url = _llm_base_url()
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._openai = OpenAI(**kwargs)
        logger.info("LLM provider=%s model=%s base_url=%s",
                    _llm_provider(), _llm_model(), base_url or "default")
        return self._openai

    # ------------------------------------------------------------------
    # Agentic loop
    # ------------------------------------------------------------------

    def _run_agentic_loop(self, session_id: str, user_message: str) -> dict[str, Any]:
        oai = self._get_openai()
        if oai is None:
            return self._fallback.handle(user_message)

        history = _memory.get(session_id)
        if not history:
            history = [{"role": "system", "content": _build_system_prompt(self.sf)}]
            _memory.append(session_id, history[0])

        _memory.append(session_id, {"role": "user", "content": user_message})
        messages = _memory.get(session_id)

        all_tool_calls: list[dict] = []
        last_records:   list[dict] = []
        last_soql = ""
        last_object = ""

        for _round in range(self.MAX_TOOL_ROUNDS):
            response = oai.chat.completions.create(
                model=_llm_model(),
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.1,
                max_tokens=1500,
            )

            msg = response.choices[0].message
            assistant_dict: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                assistant_dict["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]
            _memory.append(session_id, assistant_dict)
            messages = _memory.get(session_id)

            if not msg.tool_calls:
                final_text = msg.content or "I could not generate a response."
                _memory.append(session_id, {"role": "assistant", "content": final_text})
                return {"answer": final_text, "tool_calls": all_tool_calls,
                        "records": last_records, "soql": last_soql, "object": last_object}

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                logger.info("Tool call: %s(%s)", fn_name, fn_args)
                tool_result = self.tools.dispatch(fn_name, fn_args)
                all_tool_calls.append({"tool": fn_name, "args": fn_args,
                                       "result_summary": _summarise_result(tool_result)})
                if isinstance(tool_result, dict):
                    if "records"  in tool_result: last_records  = tool_result["records"]
                    if "soql"     in tool_result: last_soql     = tool_result["soql"]
                    if "object"   in tool_result: last_object   = tool_result["object"]

                _memory.append(session_id, {
                    "role": "tool", "tool_call_id": tc.id,
                    "content": json.dumps(tool_result, default=str),
                })
                messages = _memory.get(session_id)

        return {"answer": "Reached maximum reasoning steps. Try a more focused question.",
                "tool_calls": all_tool_calls, "records": last_records,
                "soql": last_soql, "object": last_object}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_request(self, prompt: str, session_id: str = "default") -> dict[str, Any]:
        try:
            return self._run_agentic_loop(session_id, prompt)
        except Exception as exc:
            logger.exception("Agent error")
            return {"answer": f"An error occurred: {exc}", "tool_calls": [],
                    "records": [], "soql": "", "error": str(exc)}

    def stream_request(self, prompt: str, session_id: str = "default") -> Generator[str, None, None]:
        """
        Streaming entry point. Yields NDJSON lines:
          tool_start, tool_end, text_chunk, done, error
        """
        oai = self._get_openai()
        if oai is None:
            result = self._fallback.handle(prompt)
            yield _ndjson("text_chunk", text=result["answer"])
            yield _ndjson("done", soql=result.get("soql", ""),
                          records=result.get("records", []),
                          tool_calls=result.get("tool_calls", []))
            return

        history = _memory.get(session_id)
        if not history:
            history = [{"role": "system", "content": _build_system_prompt(self.sf)}]
            _memory.append(session_id, history[0])

        _memory.append(session_id, {"role": "user", "content": prompt})
        messages = _memory.get(session_id)

        all_tool_calls: list[dict] = []
        last_records:   list[dict] = []
        last_soql = ""

        try:
            for _round in range(self.MAX_TOOL_ROUNDS):
                response = oai.chat.completions.create(
                    model=_llm_model(),
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    temperature=0.1,
                    max_tokens=1500,
                    stream=True,
                )

                full_content = ""
                tool_calls_raw: dict[int, dict] = {}

                for chunk in response:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta is None:
                        continue
                    if delta.content:
                        full_content += delta.content
                        yield _ndjson("text_chunk", text=delta.content)
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_raw:
                                tool_calls_raw[idx] = {"id": tc_delta.id or "", "name": "", "arguments": ""}
                            if tc_delta.id:
                                tool_calls_raw[idx]["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tool_calls_raw[idx]["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tool_calls_raw[idx]["arguments"] += tc_delta.function.arguments

                assistant_dict: dict[str, Any] = {"role": "assistant", "content": full_content}
                if tool_calls_raw:
                    assistant_dict["tool_calls"] = [
                        {"id": v["id"], "type": "function",
                         "function": {"name": v["name"], "arguments": v["arguments"]}}
                        for v in tool_calls_raw.values()
                    ]
                _memory.append(session_id, assistant_dict)
                messages = _memory.get(session_id)

                if not tool_calls_raw:
                    _memory.append(session_id, {"role": "assistant", "content": full_content})
                    yield _ndjson("done", soql=last_soql, records=last_records, tool_calls=all_tool_calls)
                    return

                for tc_info in tool_calls_raw.values():
                    fn_name = tc_info["name"]
                    try:
                        fn_args = json.loads(tc_info["arguments"] or "{}")
                    except json.JSONDecodeError:
                        fn_args = {}

                    yield _ndjson("tool_start", tool=fn_name, args=fn_args)
                    tool_result = self.tools.dispatch(fn_name, fn_args)
                    summary = _summarise_result(tool_result)
                    yield _ndjson("tool_end", tool=fn_name, summary=summary)

                    all_tool_calls.append({"tool": fn_name, "args": fn_args, "result_summary": summary})
                    if isinstance(tool_result, dict):
                        if "records" in tool_result: last_records = tool_result["records"]
                        if "soql"    in tool_result: last_soql    = tool_result["soql"]

                    _memory.append(session_id, {
                        "role": "tool", "tool_call_id": tc_info["id"],
                        "content": json.dumps(tool_result, default=str),
                    })
                    messages = _memory.get(session_id)

            yield _ndjson("done", soql=last_soql, records=last_records, tool_calls=all_tool_calls)

        except Exception as exc:
            logger.exception("Streaming agent error")
            yield _ndjson("error", message=str(exc))

    def clear_history(self, session_id: str = "default") -> None:
        _memory.clear(session_id)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _summarise_result(result: Any) -> str:
    if isinstance(result, dict):
        if "error"   in result: return f"Error: {result['error']}"
        if "records" in result:
            n   = result.get("totalSize", len(result["records"]))
            obj = result.get("object", "record")
            return f"Retrieved {n} {obj} record(s)"
        if "action"  in result:
            return f"{result['action'].title()}: {result.get('sobject','')} {result.get('record_id','')}"
        if "schema"  in result:
            return f"Schema fetched for {result.get('sobject','?')}"
    return str(result)[:120]


def _ndjson(event_type: str, **kwargs: Any) -> str:
    return json.dumps({"type": event_type, **kwargs}, default=str) + "\n"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Salesforce AI Agent")
    parser.add_argument("prompt",        nargs="?", help="Natural-language Salesforce request")
    parser.add_argument("--interactive", action="store_true", help="Interactive REPL mode")
    parser.add_argument("--session",     default="cli", help="Session ID for conversation memory")
    args = parser.parse_args()

    agent = SalesforceAgent()

    def _run(prompt: str) -> None:
        result = agent.handle_request(prompt, session_id=args.session)
        print("\n" + result["answer"])
        if result.get("soql"):
            print(f"\n[SOQL] {result['soql']}")
        if result.get("tool_calls"):
            print(f"[Tools] {', '.join(t['tool'] for t in result['tool_calls'])}")
        print()

    if args.interactive:
        print("Salesforce AI Agent  (type 'exit' to quit, 'clear' to reset history)")
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user_input or user_input.lower() in {"exit", "quit"}:
                break
            if user_input.lower() == "clear":
                agent.clear_history(args.session)
                print("[History cleared]")
                continue
            _run(user_input)
        return

    if not args.prompt:
        parser.print_help()
        return

    _run(args.prompt)


if __name__ == "__main__":
    main()
