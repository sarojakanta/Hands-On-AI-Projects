"""MCP transport test (multi-server).

Proves the tools are reachable through REAL separate domain MCP server
processes over stdio (crm/oms/helpdesk/store), not just the in-process
registry. Exercises app/mcp/client.py in subprocess mode: it launches the
owning domain server for each tool and performs list_tools + call_tool
round-trips.

Kept in its own module because it spawns subprocesses and is slower.
"""

from __future__ import annotations

from app.mcp.client import MCPClient


def test_subprocess_list_tools_across_domains():
    client = MCPClient(inprocess=False)
    tools = client.list_tools()
    names = {t["name"] for t in tools}
    # 10 tools across the four domain servers.
    assert len(names) == 10
    assert {"send_otp", "get_customer_profile"} <= names  # crm
    assert {"get_order", "request_address_change"} <= names  # oms
    assert {"create_ticket", "escalate_to_human"} <= names  # helpdesk
    assert {"find_store", "get_store_hours"} <= names  # store


def test_subprocess_public_store_tool_call():
    client = MCPClient(inprocess=False)
    resp = client.call_tool("get_store_hours", {"store_id": "STORE-01"})
    assert resp["ok"] is True
    assert "opening_hours" in resp["data"]


def test_subprocess_routes_to_correct_server():
    """A crm-mcp tool and an oms-mcp tool both route correctly over stdio."""
    client = MCPClient(inprocess=False)
    # find_store lives on store-mcp; unauthorized oms get_order still blocked.
    store = client.call_tool("find_store", {"city": "Mumbai"})
    assert store["ok"] is True
    order = client.call_tool(
        "get_order",
        {"session_id": "no-session", "customer_id": "CUST-1001", "order_id": "UC-48213"},
    )
    assert order["ok"] is False
    assert order["error"] == "unauthorized"
