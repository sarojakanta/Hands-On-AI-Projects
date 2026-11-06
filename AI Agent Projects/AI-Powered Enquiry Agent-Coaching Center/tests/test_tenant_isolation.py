"""Tests that one institute's data is never visible to another."""
from __future__ import annotations

from app.services import course_service, lead_service
from app.schemas.lead import LeadCreate
from tests.conftest import API_KEY_A, API_KEY_B


def test_courses_scoped_by_institute(client, institute, institute_b):
    a = client.get("/api/courses", headers={"X-Institute-Key": API_KEY_A}).json()
    b = client.get("/api/courses", headers={"X-Institute-Key": API_KEY_B}).json()
    a_names = {c["name"] for c in a}
    b_names = {c["name"] for c in b}
    assert "Python Programming" in a_names
    assert "Python Programming" not in b_names
    assert "Banking Preparation" in b_names
    assert "Banking Preparation" not in a_names


def test_service_layer_filters_by_institute(db, institute, institute_b):
    a_courses = course_service.list_courses(db, institute.id)
    b_courses = course_service.list_courses(db, institute_b.id)
    assert all(c.institute_id == institute.id for c in a_courses)
    assert all(c.institute_id == institute_b.id for c in b_courses)


def test_lead_not_visible_across_tenants(db, institute, institute_b):
    lead = lead_service.create_lead(
        db,
        institute.id,
        LeadCreate(name="A Student", phone="9000000001"),
    )
    # Same phone lookup under institute B must not find institute A's lead.
    found_a = lead_service.get_lead_by_phone(db, institute.id, "9000000001")
    found_b = lead_service.get_lead_by_phone(db, institute_b.id, "9000000001")
    assert found_a is not None and found_a.id == lead.id
    assert found_b is None


def test_bad_institute_key_rejected(client, institute):
    res = client.post(
        "/api/chat",
        json={"message": "hi"},
        headers={"X-Institute-Key": "totally-wrong-key"},
    )
    assert res.status_code == 404


def test_single_institute_allows_no_key_fallback(client, institute):
    # With exactly one institute, an unkeyed request falls back to it.
    res = client.post("/api/chat", json={"message": "hi"})
    assert res.status_code == 200


def test_multi_institute_requires_key(client, institute, institute_b):
    # With two institutes and no key, the request must be rejected (400)
    # so it can never be routed to the wrong tenant.
    res = client.post("/api/chat", json={"message": "hi"})
    assert res.status_code == 400


def test_chat_routes_to_correct_institute(client, institute, institute_b):
    # Institute B offers "Banking Preparation"; institute A does not.
    rb = client.post(
        "/api/chat",
        json={"message": "what is the fee for Banking Preparation?"},
        headers={"X-Institute-Key": API_KEY_B},
    ).json()
    assert "12,000" in rb["reply"]  # institute_b fixture seeds fee 12000

    ra = client.post(
        "/api/chat",
        json={"message": "what is the fee for Banking Preparation?"},
        headers={"X-Institute-Key": API_KEY_A},
    ).json()
    # A has no such course -> agent asks which of A's courses instead.
    assert "Banking" not in ra["reply"] or "offer" in ra["reply"].lower()
