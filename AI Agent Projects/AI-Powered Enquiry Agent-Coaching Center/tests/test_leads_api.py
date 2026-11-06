"""Tests for lead creation, status lookup and follow-up creation."""
from __future__ import annotations

from tests.conftest import API_KEY_A


def _hdr():
    return {"X-Institute-Key": API_KEY_A}


def test_create_lead(client, institute):
    payload = {
        "name": "Asha Rao",
        "phone": "+91-99887-66554",
        "email": "asha@example.com",
        "course_interest": "Python Programming",
    }
    res = client.post("/api/leads", json=payload, headers=_hdr())
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "Asha Rao"
    assert body["status"] == "new"


def test_create_lead_invalid_phone(client, institute):
    res = client.post(
        "/api/leads", json={"name": "X", "phone": "abc"}, headers=_hdr()
    )
    assert res.status_code == 422


def test_lead_status_lookup(client, institute):
    phone = "9190000111222"
    client.post(
        "/api/leads", json={"name": "Lookup", "phone": phone}, headers=_hdr()
    )
    # Pass phone as a params dict so it is URL-encoded correctly.
    res = client.get("/api/leads/status", params={"phone": phone}, headers=_hdr())
    assert res.status_code == 200
    assert res.json()["name"] == "Lookup"


def test_lead_status_not_found(client, institute):
    res = client.get("/api/leads/status?phone=+91-00000-00000", headers=_hdr())
    assert res.status_code == 404


def test_create_followup(client, institute):
    lead = client.post(
        "/api/leads", json={"name": "Follow", "phone": "9000011122"}, headers=_hdr()
    ).json()
    res = client.post(
        "/api/leads/followups",
        json={"lead_id": lead["id"], "notes": "Call after 6"},
        headers=_hdr(),
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "pending"


def test_followup_for_missing_lead(client, institute):
    res = client.post(
        "/api/leads/followups", json={"lead_id": 999999}, headers=_hdr()
    )
    assert res.status_code == 404
