"""Generic domain MCP server runner.

Builds a stdio MCP server that exposes ONLY the tools belonging to one domain
(crm-mcp / oms-mcp / helpdesk-mcp / store-mcp). Each domain server is launched
as its own process, so the agent composes four scoped servers rather than one
monolith - mirroring an enterprise where each backend system (CRM, OMS,
Helpdesk, Store) is fronted by its own MCP adapter.
"""

from __future__ import annotations

import asyncio
import json

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from app.database.db import init_db
from mcp_server.domains.registry import tools_for, call_tool


def build_server(server_name: str) -> Server:
    server = Server(server_name)
    specs = tools_for(server_name)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=spec.name,
                description=f"[{spec.access}] {spec.description}",
                inputSchema=spec.input_schema,
            )
            for spec in specs
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        # Only tools belonging to this server are honoured here.
        allowed = {s.name for s in specs}
        if name not in allowed:
            result = {
                "ok": False,
                "data": None,
                "error": "unknown_tool",
                "message": f"Tool '{name}' is not served by {server_name}.",
            }
        else:
            result = call_tool(name, arguments or {})
        return [types.TextContent(type="text", text=json.dumps(result))]

    return server


async def _run(server_name: str) -> None:
    init_db()
    server = build_server(server_name)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run(server_name: str) -> None:
    asyncio.run(_run(server_name))
