"""Tests for the admission agent via the public chat endpoint (mock LLM)."""
from __future__ import annotations

from tests.conftest import API_KEY_A


def _hdr():
    return {"X-Institute-Key": API_KEY_A}


def _chat(client, message, session_id=None):
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    res = client.post("/api/chat", json=payload, headers=_hdr())
    assert res.status_code == 200, res.text
    return res.json()


def test_greeting(client, institute):
    data = _chat(client, "hello")
    assert data["intent"] == "greeting"
    assert data["session_id"]
    assert "Test Academy A" in data["reply"]


def test_fee_tool_invocation(client, institute):
    data = _chat(client, "what is the fee for Python Programming?")
    assert data["intent"] == "fee_details"
    assert "15,000" in data["reply"]


def test_batch_tool_invocation(client, institute):
    data = _chat(client, "when do the Python batches start?")
    assert data["intent"] == "batch_schedule"
    assert "PY-Morning" in data["reply"]


def test_missing_info_handling_registration(client, institute):
    # Asking to register with no details -> agent asks for name + phone.
    data = _chat(client, "I want to register for Python")
    assert data["intent"] == "enquiry_registration"
    assert "name" in data["reply"].lower()
    assert data["collected_lead"] is False


def test_registration_collects_lead(client, institute, db):
    d1 = _chat(client, "I want to register for Java Development")
    sid = d1["session_id"]
    d2 = _chat(client, "My name is Ravi Kumar and my number is 9876500000", sid)
    assert d2["collected_lead"] is True
    # Lead persisted with the course captured.
    from app.models import StudentLead

    lead = db.query(StudentLead).filter_by(phone="9876500000").one()
    assert lead.name == "Ravi Kumar"
    assert lead.course_interest == "Java Development"


def test_escalation_collects_contact_first(client, institute):
    # Without contact details, the agent asks for name + phone first
    # (so the counsellor can actually follow up) and does NOT escalate yet.
    d1 = _chat(client, "I want to talk to a human counsellor")
    assert d1["escalated"] is False
    assert "name" in d1["reply"].lower()

    # Providing details completes the escalation and captures a lead.
    d2 = _chat(
        client,
        "My name is Meera Iyer and my number is 9811122233",
        d1["session_id"],
    )
    assert d2["escalated"] is True
    assert d2["collected_lead"] is True


def test_escalation_completes_with_informal_details(client, institute):
    # Details given in a phrasing that isn't classified as "registration"
    # must still complete a pending escalation.
    d1 = _chat(client, "can I speak to a human?")
    assert d1["escalated"] is False
    d2 = _chat(client, "I am Ravi, phone 9822011111", d1["session_id"])
    assert d2["escalated"] is True
    assert d2["collected_lead"] is True


def test_escalation_immediate_when_details_known(client, institute, db):
    # If the student already registered (name + phone in context), asking for
    # a counsellor escalates immediately.
    d1 = _chat(client, "I want to register for Python")
    sid = d1["session_id"]
    _chat(client, "My name is Anil Verma and my number is 9700000001", sid)
    d3 = _chat(client, "can I talk to a human counsellor?", sid)
    assert d3["escalated"] is True
    # The escalation is linked via the conversation's lead.
    from app.models import Conversation, Escalation

    convo = db.query(Conversation).filter_by(session_id=sid).one()
    assert convo.lead_id is not None
    assert db.query(Escalation).filter_by(conversation_id=convo.id).count() >= 1


def test_conversation_history_persists(client, institute):
    d1 = _chat(client, "hi")
    sid = d1["session_id"]
    _chat(client, "what courses do you offer?", sid)
    res = client.get(f"/api/conversations/{sid}", headers=_hdr())
    assert res.status_code == 200
    msgs = res.json()["messages"]
    # 2 user + 2 assistant.
    assert len(msgs) == 4
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_context_carryover_focus_course(client, institute):
    d1 = _chat(client, "tell me about Python Programming")
    sid = d1["session_id"]
    # No course named now -> should resolve to Python from focus.
    d2 = _chat(client, "what is the fee?", sid)
    assert "15,000" in d2["reply"]
