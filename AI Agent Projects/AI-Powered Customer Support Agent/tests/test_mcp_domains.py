"""Tests for the four-domain MCP topology + the two new tools.

Covers:
  * domain partitioning (each server owns the right tools, with access tiers),
  * get_customer_profile (crm-mcp, read, verified-only, no raw PII),
  * request_address_change (oms-mcp, guarded write, verified + rule checks),
  * multi-server client routing (in-process).
"""

from __future__ import annotations

from app.config import config
from app.database.db import get_session
from app.models.order import Order
from app.models.shipment import Shipment
from app.auth.auth_service import AuthService
from app.mcp.client import MCPClient
from mcp_server.domains.registry import DOMAINS, ALL_TOOLS, TOOL_SERVER, call_tool


def _verify(session_id, customer_id):
    svc = AuthService()
    svc.initiate(session_id=session_id, customer_ref=customer_id)
    assert svc.verify(session_id=session_id, customer_id=customer_id, code=config.DEV_OTP).success


def _order_without_shipment():
    """Find a PLACED/PROCESSING order (no shipment) for address-change tests."""
    session = get_session()
    try:
        shipped_order_ids = {s.order_id for s in session.query(Shipment).all()}
        for o in session.query(Order).all():
            if o.id not in shipped_order_ids and o.status in ("PLACED", "CONFIRMED", "PROCESSING"):
                return o.id, o.customer_id
        return None, None
    finally:
        session.close()


def _order_with_shipment():
    session = get_session()
    try:
        sh = session.query(Shipment).first()
        o = session.get(Order, sh.order_id)
        return o.id, o.customer_id
    finally:
        session.close()


# ----------------------------------------------------------- domain topology

def test_four_domains_defined():
    assert set(DOMAINS.keys()) == {"crm-mcp", "oms-mcp", "helpdesk-mcp", "store-mcp"}


def test_crm_domain_tools():
    names = {s.name for s in DOMAINS["crm-mcp"]}
    assert names == {"send_otp", "verify_customer", "get_customer_profile"}


def test_oms_domain_tools():
    names = {s.name for s in DOMAINS["oms-mcp"]}
    assert names == {"get_order", "get_shipment_status", "request_address_change"}


def test_helpdesk_and_store_domains():
    assert {s.name for s in DOMAINS["helpdesk-mcp"]} == {"create_ticket", "escalate_to_human"}
    assert {s.name for s in DOMAINS["store-mcp"]} == {"find_store", "get_store_hours"}


def test_access_tiers_assigned():
    assert ALL_TOOLS["get_order"].access == "read"
    assert ALL_TOOLS["send_otp"].access == "write_low"
    assert ALL_TOOLS["request_address_change"].access == "write_guarded"


def test_tool_server_routing_map():
    assert TOOL_SERVER["get_customer_profile"] == "crm-mcp"
    assert TOOL_SERVER["request_address_change"] == "oms-mcp"
    assert TOOL_SERVER["create_ticket"] == "helpdesk-mcp"
    assert TOOL_SERVER["find_store"] == "store-mcp"


# ----------------------------------------------------- get_customer_profile

def test_customer_profile_requires_verification():
    _, customer_id = _order_with_shipment()
    resp = call_tool("get_customer_profile", {"session_id": "nope", "customer_id": customer_id})
    assert resp["ok"] is False
    assert resp["error"] == "unauthorized"


def test_customer_profile_after_verification_no_raw_pii():
    _, customer_id = _order_with_shipment()
    sid = "prof-ok"
    _verify(sid, customer_id)
    resp = call_tool("get_customer_profile", {"session_id": sid, "customer_id": customer_id})
    assert resp["ok"] is True
    data = resp["data"]
    assert data["customer_id"] == customer_id
    assert "tier" in data and "language" in data
    # Only a masked contact is returned, never the full mobile.
    assert data["masked_mobile"].startswith("XXXX")
    assert "mobile" not in data


# --------------------------------------------------- request_address_change

def test_address_change_requires_verification():
    order_id, customer_id = _order_with_shipment()
    resp = call_tool(
        "request_address_change",
        {"session_id": "no", "customer_id": customer_id, "order_id": order_id, "new_address": "12 New St"},
    )
    assert resp["ok"] is False
    assert resp["error"] == "unauthorized"


def test_address_change_rejected_after_dispatch():
    order_id, customer_id = _order_with_shipment()  # this order has shipped
    sid = "addr-ship"
    _verify(sid, customer_id)
    resp = call_tool(
        "request_address_change",
        {"session_id": sid, "customer_id": customer_id, "order_id": order_id, "new_address": "12 New St, Pune"},
    )
    assert resp["ok"] is False
    assert resp["error"] == "rejected"


def test_address_change_allowed_before_dispatch():
    order_id, customer_id = _order_without_shipment()
    if order_id is None:
        import pytest

        pytest.skip("No pre-dispatch order available in seed data.")
    sid = "addr-ok"
    _verify(sid, customer_id)
    resp = call_tool(
        "request_address_change",
        {"session_id": sid, "customer_id": customer_id, "order_id": order_id, "new_address": "9 Park Ave, Delhi"},
    )
    assert resp["ok"] is True
    assert resp["data"]["status"] == "REQUESTED"


# ------------------------------------------------- multi-server client (in-proc)

def test_client_inprocess_lists_all_ten_tools():
    client = MCPClient(inprocess=True)
    names = {t["name"] for t in client.list_tools()}
    assert len(names) == 10
    # Each listed tool carries its owning server + access tier.
    sample = next(t for t in client.list_tools() if t["name"] == "get_order")
    assert sample["server"] == "oms-mcp"
    assert sample["access"] == "read"


def test_client_inprocess_routes_unknown_tool():
    client = MCPClient(inprocess=True)
    resp = client.call_tool("nonexistent_tool", {})
    assert resp["ok"] is False
    assert resp["error"] == "unknown_tool"
