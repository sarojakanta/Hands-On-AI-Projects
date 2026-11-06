"""Tests for follow-up status transitions, the admin follow-ups endpoint,
and admin conversation listing / transcript."""
from __future__ import annotations

import pytest

from app.schemas.lead import FollowUpCreate, LeadCreate
from app.services import conversation_service, lead_service
from app.services.exceptions import NotFoundError, ValidationError
from tests.conftest import API_KEY_A


def _hdr():
    return {"X-Institute-Key": API_KEY_A}


# --- Follow-up status transitions (service layer) -------------------------
def _make_followup(db, institute_id):
    lead = lead_service.create_lead(
        db, institute_id, LeadCreate(name="Follow Up", phone="9001112223")
    )
    return lead_service.create_followup(
        db, institute_id, FollowUpCreate(lead_id=lead.id, notes="call")
    )


def test_followup_starts_pending(db, institute):
    fu = _make_followup(db, institute.id)
    assert fu.status == "pending"


def test_followup_valid_transitions(db, institute):
    fu = _make_followup(db, institute.id)
    for status in ("scheduled", "done", "cancelled"):
        updated = lead_service.set_followup_status(db, institute.id, fu.id, status)
        assert updated.status == status


def test_followup_invalid_status_rejected(db, institute):
    fu = _make_followup(db, institute.id)
    with pytest.raises(ValidationError):
        lead_service.set_followup_status(db, institute.id, fu.id, "banana")


def test_followup_status_unknown_id(db, institute):
    with pytest.raises(NotFoundError):
        lead_service.set_followup_status(db, institute.id, 999999, "done")


def test_followup_tenant_isolation(db, institute, institute_b):
    fu = _make_followup(db, institute.id)
    # Institute B cannot change institute A's follow-up.
    with pytest.raises(NotFoundError):
        lead_service.set_followup_status(db, institute_b.id, fu.id, "done")


# --- Admin follow-ups endpoint -------------------------------------------
def test_admin_list_followups(client, institute, auth_headers, db):
    _make_followup(db, institute.id)
    res = client.get("/api/admin/followups", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert data[0]["status"] == "pending"


def test_admin_followups_requires_auth(client, institute):
    assert client.get("/api/admin/followups").status_code == 401


# --- Admin conversation listing + transcript -----------------------------
def test_admin_lists_conversations_and_transcript(client, institute, auth_headers):
    # Create a conversation via the public chat.
    chat = client.post(
        "/api/chat", json={"message": "hello"}, headers=_hdr()
    ).json()
    sid = chat["session_id"]

    listing = client.get("/api/admin/conversations", headers=auth_headers)
    assert listing.status_code == 200
    convos = listing.json()
    assert any(c["session_id"] == sid for c in convos)
    mine = next(c for c in convos if c["session_id"] == sid)
    assert mine["message_count"] >= 2  # user + assistant

    transcript = client.get(
        f"/api/admin/conversations/{sid}", headers=auth_headers
    )
    assert transcript.status_code == 200
    body = transcript.json()
    assert body["session_id"] == sid
    roles = [m["role"] for m in body["messages"]]
    assert "user" in roles and "assistant" in roles


def test_admin_conversation_not_found(client, institute, auth_headers):
    res = client.get("/api/admin/conversations/no-such-session", headers=auth_headers)
    assert res.status_code == 404


def test_admin_conversations_requires_auth(client, institute):
    assert client.get("/api/admin/conversations").status_code == 401
