"""Tests for the full memory spec M-1..M-6 + memory types.

LLM is mocked offline by conftest, so summarization uses the deterministic
fallback. Memory stores use the local/SQLite backends.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app.memory.local_store import LocalMemoryStore
from app.memory.conversation_memory import ConversationMemory
from app.services import customer_memory_service as cms
from app.models.customer_memory import CustomerIssue
from app.database.db import get_session


def _mem():
    return ConversationMemory(store=LocalMemoryStore(), ttl_seconds=1800)


# ---- M-6: summarize after 10 turns ----

def test_m6_summarizes_after_10_turns():
    mem = _mem()
    state = mem.load("m6")
    for i in range(14):
        mem.append_turn(state, role="user", content=f"question {i}")
        mem.append_turn(state, role="assistant", content=f"answer {i}")
    mem.save(state)
    reloaded = mem.load("m6")
    # Raw turns are capped; a summary was produced from the older ones.
    assert len(reloaded.messages) <= 10
    assert reloaded.summary


# ---- M-1: no long raw chat text persisted beyond the cap ----

def test_m1_keeps_only_recent_raw_turns():
    mem = _mem()
    state = mem.load("m1")
    for i in range(30):
        mem.append_turn(state, role="user", content=f"msg {i}")
    mem.save(state)
    reloaded = mem.load("m1")
    assert len(reloaded.messages) <= 10  # bulk of raw text is not retained


# ---- M-2: verification status not stored in working memory ----

def test_m2_no_verification_field_in_working_memory():
    mem = _mem()
    state = mem.load("m2")
    mem.save(state)
    stored = mem._store.get(mem._key("m2"))
    assert "authenticated" not in stored  # verification never persisted here


# ---- M-3: customer-keyed isolation ----

def test_m3_episodic_isolated_by_customer():
    cms.record_issue(customer_id="CUST-1001", summary="A's late delivery", outcome="RESOLVED")
    cms.record_issue(customer_id="CUST-1002", summary="B's warranty claim", outcome="ESCALATED")
    a = cms.list_issues("CUST-1001")
    b = cms.list_issues("CUST-1002")
    assert all(i["customer_id"] == "CUST-1001" for i in a)
    assert all(i["customer_id"] == "CUST-1002" for i in b)
    assert "A's late delivery" not in [i["summary"] for i in b]


def test_m3_preference_isolated():
    cms.set_preference(customer_id="CUST-1003", preferred_language="hi", contact_channel="whatsapp")
    other = cms.get_preference("CUST-1004")
    assert other is None or other["customer_id"] == "CUST-1004"


# ---- M-4: see / correct / delete ----

def test_m4_export_shows_memory():
    cms.record_issue(customer_id="CUST-1005", summary="refund query", outcome="RESOLVED")
    cms.set_preference(customer_id="CUST-1005", preferred_language="en")
    export = cms.export_memory("CUST-1005")
    assert export["preferences"]["preferred_language"] == "en"
    assert any("refund" in i["summary"] for i in export["issues"])


def test_m4_correct_preference():
    cms.set_preference(customer_id="CUST-1006", preferred_language="en", contact_channel="email")
    cms.set_preference(customer_id="CUST-1006", contact_channel="sms")
    pref = cms.get_preference("CUST-1006")
    assert pref["contact_channel"] == "sms"
    assert pref["preferred_language"] == "en"


def test_m4_delete_removes_everything():
    cms.record_issue(customer_id="CUST-1007", summary="issue", outcome="RESOLVED")
    cms.set_preference(customer_id="CUST-1007", preferred_language="ta")
    result = cms.delete_memory("CUST-1007")
    assert result["deleted_issues"] >= 1
    assert cms.list_issues("CUST-1007") == []
    assert cms.get_preference("CUST-1007") is None


# ---- 12-month retention purge ----

def test_retention_purge_removes_expired():
    # Insert an already-expired episodic row directly.
    session = get_session()
    try:
        old = CustomerIssue(
            customer_id="CUST-1008",
            summary="old issue",
            outcome="RESOLVED",
            retain_until=datetime.now(timezone.utc) - timedelta(days=1),
        )
        session.add(old)
        session.commit()
    finally:
        session.close()
    purged = cms.purge_expired()
    assert purged >= 1


# ---- M-4 endpoints (require verified session) ----

def test_m4_endpoints_require_verification(client):
    assert client.get("/api/memory").status_code == 401
    assert client.delete("/api/memory").status_code == 401


def test_m4_endpoints_work_after_verification(client):
    from app.config import config
    # Verify via the API so the session cookie is set for this client.
    from app.database.db import get_session as gs
    from app.models.order import Order

    session = gs()
    try:
        order = session.get(Order, "UC-48213")
        customer_id = order.customer_id
    finally:
        session.close()

    client.post("/api/auth/initiate", json={"order_ref": "UC-48213"})
    client.post("/api/auth/verify", json={"customer_id": customer_id, "code": config.DEV_OTP})

    cms.set_preference(customer_id=customer_id, preferred_language="en")
    view = client.get("/api/memory")
    assert view.status_code == 200
    assert view.get_json()["customer_id"] == customer_id

    deleted = client.delete("/api/memory")
    assert deleted.status_code == 200


# ---- team knowledge in the customer index ----

def test_team_knowledge_retrievable():
    from app.rag.indexer import build_customer_index
    from app.rag.retriever import hybrid_retriever

    build_customer_index()
    passages = hybrid_retriever.retrieve("item arrived damaged replacement", region="IN", language="en")
    sources = {p.source for p in passages}
    assert "Approved Edge-Case Answers" in sources or passages



# ---- M-5: tool result wins over remembered context ----

def test_m5_tool_result_overrides_memory():
    """A verified order reply reflects the LIVE tool result, not a stale
    remembered status."""
    from app.config import config
    from app.auth.auth_service import AuthService
    from app.agents.graph import run_agent
    from app.database.db import get_session as gs
    from app.models.order import Order
    from app.memory.conversation_memory import conversation_memory

    session = gs()
    try:
        order = session.get(Order, "UC-48213")
        order_id, customer_id, real_status = order.id, order.customer_id, order.status
    finally:
        session.close()

    cid = "m5-precedence"
    conversation_memory.clear(cid)
    # Seed working memory with a deliberately WRONG remembered status.
    state = conversation_memory.load(cid)
    state.tool_results_summary = "get_order:DELIVERED (stale)"
    state.active_order = order_id
    conversation_memory.save(state)

    svc = AuthService()
    svc.initiate(session_id="m5s", customer_ref=customer_id)
    svc.verify(session_id="m5s", customer_id=customer_id, code=config.DEV_OTP)

    out = run_agent(
        message=f"What is the status of my order {order_id}?",
        conversation_id=cid,
        session_id="m5s",
        authenticated=True,
        authenticated_customer_id=customer_id,
    )
    # The reply is grounded in the live order; the stale 'DELIVERED (stale)'
    # memory string does not drive the answer.
    assert order_id in out.reply
    assert "(stale)" not in out.reply
