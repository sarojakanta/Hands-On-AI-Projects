"""Multi-server MCP client used by the agent.

The business tools are split across FOUR domain MCP servers, each a separate
stdio process fronting a (simulated) enterprise system:

    crm-mcp      (CRM)       send_otp, verify_customer, get_customer_profile
    oms-mcp      (OMS)       get_order, get_shipment_status, request_address_change
    helpdesk-mcp (Helpdesk)  create_ticket, escalate_to_human
    store-mcp    (Store)     find_store, get_store_hours

The agent (sync Flask) calls ``call_tool(name, args)``; this client routes the
call to the server that owns that tool and bridges the async MCP SDK.

Two modes (both go through the same domain registry, so behaviour/authorization
is identical):
  * subprocess (default): launches the owning domain server module over stdio.
  * in-process: calls the registry directly. Selected via MCP_INPROCESS=1.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import BASE_DIR

# Map each domain server to the module that runs it.
_SERVER_MODULES = {
    "crm-mcp": "mcp_server.domains.crm_server",
    "oms-mcp": "mcp_server.domains.oms_server",
    "helpdesk-mcp": "mcp_server.domains.helpdesk_server",
    "store-mcp": "mcp_server.domains.store_server",
}


def _inprocess_enabled() -> bool:
    return os.getenv("MCP_INPROCESS", "").strip().lower() in {"1", "true", "yes"}


class MCPClient:
    """Synchronous facade over the four domain MCP servers."""

    def __init__(self, inprocess: bool | None = None) -> None:
        self._inprocess = _inprocess_enabled() if inprocess is None else inprocess

    # ---- public sync API ----

    def list_tools(self) -> list[dict]:
        """List every tool across all domain servers (with server + access)."""
        if self._inprocess:
            from mcp_server.domains.registry import ALL_TOOLS

            return [
                {
                    "name": s.name,
                    "description": s.description,
                    "input_schema": s.input_schema,
                    "server": s.server,
                    "access": s.access,
                }
                for s in ALL_TOOLS.values()
            ]
        # Subprocess: query each server and merge.
        tools: list[dict] = []
        for server_name in _SERVER_MODULES:
            tools.extend(asyncio.run(self._list_tools_async(server_name)))
        return tools

    def call_tool(
        self,
        name: str,
        arguments: dict,
        *,
        conversation_id: str | None = None,
    ) -> dict:
        """Route a tool call to its owning domain server.

        Applies: a per-conversation tool-call rate limit (T-6), a timeout +
        single retry with a clear error (T-3), and per-call masked audit
        logging (T-5).
        """
        from app.services.rate_limiter import rate_limiter
        from app.services.masking import mask_arguments
        from app.observability import tracing

        session_id = arguments.get("session_id") if isinstance(arguments, dict) else None
        masked_args = mask_arguments(arguments)

        # T-6: per-conversation tool-call cap.
        if not rate_limiter.check_tool_call(conversation_id):
            result = {
                "ok": False,
                "data": None,
                "error": "rate_limited",
                "message": "This conversation has reached its tool-call limit. "
                "Let me connect you with a human if you still need help.",
            }
            tracing.log_tool_call(
                session_id=session_id, tool=name, arguments=masked_args, status="rate_limited"
            )
            return result

        result = self._dispatch(name, arguments)

        status = "ok" if result.get("ok") else (result.get("error") or "error")
        tracing.log_tool_call(
            session_id=session_id, tool=name, arguments=masked_args, status=status
        )
        return result

    def _dispatch(self, name: str, arguments: dict) -> dict:
        """Run the tool with timeout + one retry (T-3)."""
        from app.config import config

        if self._inprocess:
            from mcp_server.domains.registry import call_tool

            return call_tool(name, arguments)

        from mcp_server.domains.registry import TOOL_SERVER

        server_name = TOOL_SERVER.get(name)
        if server_name is None:
            return {
                "ok": False,
                "data": None,
                "error": "unknown_tool",
                "message": f"Tool '{name}' is not available on any MCP server.",
            }

        attempts = config.TOOL_MAX_RETRIES + 1
        last_error = "timeout"
        for attempt in range(attempts):
            try:
                return asyncio.run(
                    asyncio.wait_for(
                        self._call_tool_async(server_name, name, arguments),
                        timeout=config.TOOL_TIMEOUT_SECONDS,
                    )
                )
            except asyncio.TimeoutError:
                last_error = "timeout"
            except Exception:  # noqa: BLE001 - transport/subprocess errors
                last_error = "tool_error"
        # Clear, explainable error after the retry is exhausted (T-3).
        return {
            "ok": False,
            "data": None,
            "error": last_error,
            "message": "I couldn't reach that service just now. Please try again "
            "in a moment, or I can connect you with a human agent.",
        }

    # ---- async implementations (subprocess transport) ----

    def _server_params(self, server_name: str) -> StdioServerParameters:
        module = _SERVER_MODULES[server_name]
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", module],
            cwd=str(BASE_DIR),
            env=os.environ.copy(),
        )

    async def _list_tools_async(self, server_name: str) -> list[dict]:
        async with stdio_client(self._server_params(server_name)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resp = await session.list_tools()
                return [
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.inputSchema,
                        "server": server_name,
                    }
                    for t in resp.tools
                ]

    async def _call_tool_async(self, server_name: str, name: str, arguments: dict) -> dict:
        async with stdio_client(self._server_params(server_name)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                for item in result.content:
                    if getattr(item, "type", None) == "text":
                        try:
                            return json.loads(item.text)
                        except json.JSONDecodeError:
                            return {
                                "ok": False,
                                "data": None,
                                "error": "bad_response",
                                "message": "Tool returned non-JSON content.",
                            }
                return {
                    "ok": False,
                    "data": None,
                    "error": "empty_response",
                    "message": "Tool returned no content.",
                }


# Convenience singleton for the app.
mcp_client = MCPClient()
