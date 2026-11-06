"""Phase 3 MCP tool tests.

Validates the scoped business tools: input schema validation, structured
responses, and - most importantly - backend-enforced authorization:

  * order/shipment tools require a verified session for the exact customer,
  * a verified customer cannot read another customer's records,
  * store tools are public,
  * ticket/escalation tools create tickets with context,
  * unknown tools are refused.

Tools are exercised through the registry's ``call_tool`` dispatch - the same
path the MCP server uses. A separate test does a real subprocess round-trip
through the MCP client to prove the separate-process transport works.
"""

from __future__ import annotations

from app.config import config
from app.database.db import get_session
from app.models.customer import Customer
from app.models.order import Order
from app.auth.auth_service import AuthService
from mcp_server.domains.registry import ALL_TOOLS as TOOLS, call_tool


# -------------------------------------------------------------------- helpers

def _known_order_customer() -> tuple[str, str]:
    session = get_session()
    try:
        order = session.get(Order, "UC-48213")
        return order.id, order.customer_id
    finally:
        session.close()


def _two_customers_with_orders() -> tuple[str, str, str, str]:
    """Return (custA, orderA, custB, orderB) for distinct customers."""
    session = get_session()
    try:
        orders = session.query(Order).all()
        by_customer: dict[str, str] = {}
        for o in orders:
            by_customer.setdefault(o.customer_id, o.id)
            if len(by_customer) >= 2:
                break
        items = list(by_customer.items())
        (custA, orderA), (custB, orderB) = items[0], items[1]
        return custA, orderA, custB, orderB
    finally:
        session.close()


def _verify_session(session_id: str, customer_id: str) -> None:
    """Drive a full OTP verification so the DB-backed authorization passes."""
    svc = AuthService()
    svc.initiate(session_id=session_id, customer_ref=customer_id)
    result = svc.verify(session_id=session_id, customer_id=customer_id, code=config.DEV_OTP)
    assert result.success


# ------------------------------------------------------------ registry surface

def test_registry_exposes_the_ten_domain_tools():
    expected = {
        # crm-mcp
        "send_otp",
        "verify_customer",
        "get_customer_profile",
        # oms-mcp
        "get_order",
        "get_shipment_status",
        "request_address_change",
        # helpdesk-mcp
        "create_ticket",
        "escalate_to_human",
        # store-mcp
        "find_store",
        "get_store_hours",
    }
    assert set(TOOLS.keys()) == expected


def test_unknown_tool_is_refused():
    resp = call_tool("drop_table", {})
    assert resp["ok"] is False
    assert resp["error"] == "unknown_tool"


def test_schema_validation_rejects_missing_args():
    # get_order requires session_id, customer_id, order_id.
    resp = call_tool("get_order", {"order_id": "UC-48213"})
    assert resp["ok"] is False
    assert resp["error"] == "invalid_arguments"


# ------------------------------------------------------- authorization: orders

def test_get_order_requires_verification():
    order_id, customer_id = _known_order_customer()
    resp = call_tool(
        "get_order",
        {"session_id": "sess-unverified", "customer_id": customer_id, "order_id": order_id},
    )
    assert resp["ok"] is False
    assert resp["error"] == "unauthorized"


def test_get_order_succeeds_after_verification():
    order_id, customer_id = _known_order_customer()
    sid = "sess-order-ok"
    _verify_session(sid, customer_id)

    resp = call_tool(
        "get_order",
        {"session_id": sid, "customer_id": customer_id, "order_id": order_id},
    )
    assert resp["ok"] is True
    assert resp["data"]["order_id"] == order_id
    assert resp["data"]["customer_id"] == customer_id


def test_verified_customer_cannot_read_another_customers_order():
    custA, orderA, custB, orderB = _two_customers_with_orders()
    sid = "sess-cross"
    _verify_session(sid, custA)

    # Customer A (verified) tries to read customer B's order.
    resp = call_tool(
        "get_order",
        {"session_id": sid, "customer_id": custA, "order_id": orderB},
    )
    assert resp["ok"] is False
    assert resp["error"] == "not_found"


def test_cannot_claim_other_customer_id_without_verification():
    custA, orderA, custB, orderB = _two_customers_with_orders()
    sid = "sess-claim"
    _verify_session(sid, custA)

    # Verified as A, but claims to be B to read B's order -> not verified for B.
    resp = call_tool(
        "get_order",
        {"session_id": sid, "customer_id": custB, "order_id": orderB},
    )
    assert resp["ok"] is False
    assert resp["error"] == "unauthorized"


# ---------------------------------------------------- authorization: shipments

def test_get_shipment_requires_verification():
    order_id, customer_id = _known_order_customer()
    resp = call_tool(
        "get_shipment_status",
        {"session_id": "nope", "customer_id": customer_id, "order_id": order_id},
    )
    assert resp["ok"] is False
    assert resp["error"] == "unauthorized"


def test_get_shipment_after_verification():
    # Find an order that actually has a shipment, owned by a single customer.
    session = get_session()
    try:
        from app.models.shipment import Shipment

        shipment = session.query(Shipment).first()
        order = session.get(Order, shipment.order_id)
        order_id, customer_id = order.id, order.customer_id
    finally:
        session.close()

    sid = "sess-ship-ok"
    _verify_session(sid, customer_id)
    resp = call_tool(
        "get_shipment_status",
        {"session_id": sid, "customer_id": customer_id, "order_id": order_id},
    )
    assert resp["ok"] is True
    assert resp["data"]["order_id"] == order_id


# ----------------------------------------------------------- public store tools

def test_search_store_is_public():
    resp = call_tool("find_store", {"city": "Mumbai"})
    assert resp["ok"] is True
    assert resp["data"]["count"] >= 1


def test_get_store_hours():
    resp = call_tool("get_store_hours", {"store_id": "STORE-01"})
    assert resp["ok"] is True
    assert "opening_hours" in resp["data"]


def test_get_store_hours_unknown():
    resp = call_tool("get_store_hours", {"store_id": "STORE-999"})
    assert resp["ok"] is False
    assert resp["error"] == "not_found"


# ----------------------------------------------------------------- ticket tools

def test_create_support_ticket():
    resp = call_tool(
        "create_ticket",
        {
            "category": "ORDER_STATUS",
            "customer_id": "CUST-1001",
            "conversation_id": "conv-1",
            "original_request": "Where is my order?",
            "summary": "Customer asking about order status.",
        },
    )
    assert resp["ok"] is True
    assert resp["data"]["ticket_id"].startswith("TKT-")
    assert resp["data"]["status"] == "OPEN"


def test_escalate_to_human_is_high_priority_with_context():
    resp = call_tool(
        "escalate_to_human",
        {
            "category": "COMPLAINT",
            "customer_id": "CUST-1002",
            "conversation_id": "conv-2",
            "escalation_reason": "Customer mentioned legal action.",
            "original_request": "This is the third time my parcel is late.",
            "summary": "Repeated delays; customer threatening lawyer.",
            "tool_results": {"order": {"status": "DELAYED"}},
        },
    )
    assert resp["ok"] is True
    # The tool response is minimal (T-4): HIGH priority is surfaced, but
    # internal context (escalation_reason, tool_results) is NOT echoed back -
    # it is stored on the ticket for the dashboard. Verify via the stored
    # ticket instead.
    assert resp["data"]["priority"] == "HIGH"
    assert "escalation_reason" not in resp["data"]
    from app.services import ticket_service

    stored = ticket_service.get_ticket(resp["data"]["ticket_id"])
    assert stored["escalation_reason"]


def test_escalate_requires_reason():
    resp = call_tool("escalate_to_human", {"category": "COMPLAINT"})
    assert resp["ok"] is False
    assert resp["error"] == "invalid_arguments"
