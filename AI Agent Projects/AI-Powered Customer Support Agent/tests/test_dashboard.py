"""Phase 7 tests: escalation auto-ticket + human support dashboard.

The LLM is forced offline by conftest. Covers:
  * escalation auto-creates a ticket with full context,
  * the ticket reference appears in the agent output and the reply,
  * no duplicate ticket per conversation,
  * dashboard list/filter/detail/update endpoints (HTML + JSON API).
"""

from __future__ import annotations

from app.agents.graph import run_agent
from app.services import ticket_service


# ------------------------------------------------- escalation -> auto ticket

def test_escalation_creates_ticket_with_context():
    out = run_agent(
        message="This is the third time my parcel is late. I will call my lawyer.",
        conversation_id="t7-esc-1",
        session_id="s1",
    )
    assert out.escalate is True
    assert out.ticket_reference and out.ticket_reference.startswith("TKT-")
    # The reference number is surfaced to the customer.
    assert out.ticket_reference in out.reply

    ticket = ticket_service.get_ticket(out.ticket_reference)
    assert ticket is not None
    assert ticket["priority"] == "HIGH"
    assert ticket["escalation_reason"]
    assert ticket["original_request"]
    assert ticket["summary"]
    assert ticket["authentication_status"] in ("VERIFIED", "UNVERIFIED")
    assert ticket["conversation_id"] == "t7-esc-1"


def test_no_duplicate_ticket_per_conversation():
    cid = "t7-dup"
    first = run_agent(message="I want a chargeback, this is fraud", conversation_id=cid, session_id="s1")
    second = run_agent(message="still waiting and now I'll sue", conversation_id=cid, session_id="s1")
    assert first.ticket_reference == second.ticket_reference
    tickets = [t for t in ticket_service.list_tickets() if t["conversation_id"] == cid]
    assert len(tickets) == 1


def test_non_escalation_creates_no_ticket():
    out = run_agent(
        message="What are your store hours?",
        conversation_id="t7-noesc",
        session_id="s1",
    )
    assert out.escalate is False
    assert out.ticket_reference is None


# ----------------------------------------------------------- dashboard views

def _make_ticket(client, message="I will call my lawyer", cid="t7-dash"):
    out = run_agent(message=message, conversation_id=cid, session_id="s1")
    return out.ticket_reference


def test_dashboard_index_renders(client):
    _make_ticket(client, cid="t7-idx")
    resp = client.get("/dashboard/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Human Support Dashboard" in body
    assert "TKT-" in body


def test_dashboard_status_filter(client):
    _make_ticket(client, cid="t7-filter")
    resp = client.get("/dashboard/?status=OPEN")
    assert resp.status_code == 200
    # New escalation tickets are OPEN, so they appear under the OPEN filter.
    assert "TKT-" in resp.get_data(as_text=True)


def test_dashboard_detail_shows_context_and_history(client):
    ref = _make_ticket(client, cid="t7-detail")
    resp = client.get(f"/dashboard/ticket/{ref}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert ref in body
    assert "Escalation reason" in body
    assert "Conversation history" in body


def test_dashboard_update_via_form(client):
    ref = _make_ticket(client, cid="t7-update")
    resp = client.post(
        f"/dashboard/ticket/{ref}/update",
        data={
            "status": "IN_PROGRESS",
            "assigned_agent": "agent.jones",
            "internal_notes": "Investigating courier delay.",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    updated = ticket_service.get_ticket(ref)
    assert updated["status"] == "IN_PROGRESS"
    assert updated["assigned_agent"] == "agent.jones"


def test_dashboard_json_api(client):
    ref = _make_ticket(client, cid="t7-api")
    # list
    resp = client.get("/dashboard/api/tickets")
    assert resp.status_code == 200
    assert any(t["ticket_id"] == ref for t in resp.get_json()["tickets"])
    # detail
    resp = client.get(f"/dashboard/api/tickets/{ref}")
    assert resp.status_code == 200
    assert resp.get_json()["ticket_id"] == ref
    # update
    resp = client.post(f"/dashboard/api/tickets/{ref}", json={"status": "RESOLVED"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "RESOLVED"


def test_dashboard_detail_404_for_missing(client):
    resp = client.get("/dashboard/ticket/TKT-DOES-NOT-EXIST")
    assert resp.status_code == 404
