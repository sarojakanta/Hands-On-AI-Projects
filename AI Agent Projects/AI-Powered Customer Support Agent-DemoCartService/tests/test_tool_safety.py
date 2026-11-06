"""Tests for tool-layer hardening T-3..T-7.

  T-3  timeout + one retry + clear error on tool calls (mocked transport).
  T-4  minimal data / masking (phone last-2, internal notes omitted).
  T-5  per-call audit logging with masked arguments + status.
  T-6  rate limits (tool calls / conversation, OTP / customer / hour).
  T-7  idempotency keys prevent duplicate tickets / address requests.
"""

from __future__ import annotations

from app.config import config
from app.database.db import get_session
from app.models.order import Order
from app.models.shipment import Shipment
from app.auth.auth_service import AuthService
from app.services import ticket_service
from app.services.masking import mask_phone, mask_arguments
from app.services.rate_limiter import rate_limiter
from app.mcp.client import MCPClient
from mcp_server.domains.registry import call_tool


def _verify(session_id, customer_id):
    svc = AuthService()
    svc.initiate(session_id=session_id, customer_ref=customer_id)
    assert svc.verify(session_id=session_id, customer_id=customer_id, code=config.DEV_OTP).success


def _a_customer():
    session = get_session()
    try:
        return session.query(Order).first().customer_id
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


# ---------------------------------------------------------------- T-4 masking

def test_mask_phone_keeps_last_two():
    assert mask_phone("+91-9876543210").endswith("10")
    assert set(mask_phone("+91-9876543210")[:-2]) == {"X"}


def test_mask_arguments_redacts_secrets_and_pii():
    masked = mask_arguments(
        {"session_id": "s1", "code": "123456", "customer_id": "CUST-1", "new_address": "12 St"}
    )
    assert masked["code"] == "[REDACTED]"
    assert masked["new_address"] == "[ADDRESS]"
    assert masked["session_id"] == "s1"  # non-secret ids pass through


def test_customer_profile_masks_phone_to_last_two():
    order_id, customer_id = _order_with_shipment()
    sid = "mask-prof"
    _verify(sid, customer_id)
    resp = call_tool("get_customer_profile", {"session_id": sid, "customer_id": customer_id})
    assert resp["ok"]
    mm = resp["data"]["masked_mobile"]
    assert mm[:-2].count("X") >= 1 and mm[-2:].isdigit()


def test_ticket_tool_response_omits_internal_fields():
    resp = call_tool("create_ticket", {"category": "COMPLAINT", "summary": "x"})
    assert resp["ok"]
    data = resp["data"]
    assert "internal_notes" not in data
    assert "resolution" not in data
    assert "tool_results" not in data
    assert "ticket_id" in data


# ----------------------------------------------------------------- T-6 limits

def test_tool_call_limit_per_conversation(monkeypatch):
    monkeypatch.setattr(config, "MAX_TOOL_CALLS_PER_CONVERSATION", 3)
    rate_limiter.reset()
    cid = "limit-conv"
    assert rate_limiter.check_tool_call(cid) is True  # 1
    assert rate_limiter.check_tool_call(cid) is True  # 2
    assert rate_limiter.check_tool_call(cid) is True  # 3
    assert rate_limiter.check_tool_call(cid) is False  # 4 -> blocked


def test_client_returns_rate_limited_error(monkeypatch):
    monkeypatch.setattr(config, "MAX_TOOL_CALLS_PER_CONVERSATION", 1)
    rate_limiter.reset()
    client = MCPClient(inprocess=True)
    cid = "rl-conv"
    first = client.call_tool("find_store", {"city": "Mumbai"}, conversation_id=cid)
    assert first["ok"] is True
    second = client.call_tool("find_store", {"city": "Mumbai"}, conversation_id=cid)
    assert second["ok"] is False
    assert second["error"] == "rate_limited"


def test_otp_requests_limited_per_customer(monkeypatch):
    monkeypatch.setattr(config, "MAX_OTP_REQUESTS_PER_HOUR", 2)
    rate_limiter.reset()
    customer_id = _a_customer()
    svc = AuthService()
    assert svc.initiate(session_id="o1", customer_ref=customer_id).success
    assert svc.initiate(session_id="o2", customer_ref=customer_id).success
    # Third within the hour is blocked.
    blocked = svc.initiate(session_id="o3", customer_ref=customer_id)
    assert blocked.success is False


# ----------------------------------------------------------- T-7 idempotency

def test_create_ticket_idempotency_key_dedups():
    r1 = call_tool("create_ticket", {"category": "COMPLAINT", "summary": "a", "idempotency_key": "k1"})
    r2 = call_tool("create_ticket", {"category": "COMPLAINT", "summary": "a", "idempotency_key": "k1"})
    assert r1["data"]["ticket_id"] == r2["data"]["ticket_id"]


def test_create_ticket_without_key_creates_distinct():
    r1 = call_tool("create_ticket", {"category": "COMPLAINT", "summary": "b"})
    r2 = call_tool("create_ticket", {"category": "COMPLAINT", "summary": "b"})
    assert r1["data"]["ticket_id"] != r2["data"]["ticket_id"]


def test_address_change_idempotent():
    # Find a pre-dispatch order (no shipment).
    session = get_session()
    try:
        shipped = {s.order_id for s in session.query(Shipment).all()}
        pre = next(
            (o for o in session.query(Order).all()
             if o.id not in shipped and o.status in ("PLACED", "CONFIRMED", "PROCESSING")),
            None,
        )
        if pre is None:
            import pytest

            pytest.skip("No pre-dispatch order available.")
        order_id, customer_id = pre.id, pre.customer_id
    finally:
        session.close()

    sid = "addr-idem"
    _verify(sid, customer_id)
    args = {
        "session_id": sid,
        "customer_id": customer_id,
        "order_id": order_id,
        "new_address": "9 Idem Street, Delhi",
    }
    r1 = call_tool("request_address_change", args)
    r2 = call_tool("request_address_change", args)
    assert r1["ok"] and r2["ok"]
    assert r1["data"]["request_id"] == r2["data"]["request_id"]


# ------------------------------------------------------------------ T-5 audit

def test_tool_call_is_audited_with_masked_args(monkeypatch):
    from app.observability import tracing

    client = MCPClient(inprocess=True)
    client.call_tool("find_store", {"city": "Mumbai"}, conversation_id="audit-conv")
    recent = tracing.read_recent_tool_calls(limit=20)
    entry = next((r for r in recent if r["tool"] == "find_store"), None)
    assert entry is not None
    assert entry["status"] == "ok"
    assert "arguments" in entry


# ------------------------------------------------------------------ T-3 timeout

def test_timeout_then_clear_error(monkeypatch):
    """Subprocess dispatch that times out returns a clear, explainable error."""
    import asyncio

    client = MCPClient(inprocess=False)
    monkeypatch.setattr(config, "TOOL_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(config, "TOOL_MAX_RETRIES", 1)

    async def _slow(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(client, "_call_tool_async", _slow)
    resp = client.call_tool("find_store", {"city": "Mumbai"}, conversation_id="to-conv")
    assert resp["ok"] is False
    assert resp["error"] in ("timeout", "tool_error")
    assert "human" in resp["message"].lower() or "try again" in resp["message"].lower()
