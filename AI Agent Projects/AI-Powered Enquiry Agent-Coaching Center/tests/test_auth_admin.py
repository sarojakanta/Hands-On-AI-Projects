"""Tests for admin authentication, authorization and admin APIs."""
from __future__ import annotations

import io

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD


def test_login_success(client, institute):
    res = client.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert res.status_code == 200
    assert res.json()["access_token"]


def test_login_wrong_password(client, institute):
    res = client.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL, "password": "nope"}
    )
    assert res.status_code == 401


def test_admin_stats_requires_auth(client, institute):
    assert client.get("/api/admin/stats").status_code == 401


def test_admin_stats_with_auth(client, institute, auth_headers):
    res = client.get("/api/admin/stats", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert "total_enquiries" in body
    assert "course_wise" in body


def test_admin_institute_requires_auth(client, institute):
    # No credentials at all -> 401.
    assert client.get("/api/admin/institute").status_code == 401


def test_admin_institute_embed_info(client, institute, auth_headers):
    # The embed info (incl. public api_key) returns the admin's own institute.
    res = client.get("/api/admin/institute", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["api_key"] == "test-key-institute-a"
    assert "base_url" in body and body["name"]


def test_admin_can_list_and_update_lead(client, institute, auth_headers):
    # Create a lead via the public API first.
    client.post(
        "/api/leads",
        json={"name": "Dash Lead", "phone": "9000012345"},
        headers={"X-Institute-Key": "test-key-institute-a"},
    )
    leads = client.get("/api/admin/leads", headers=auth_headers).json()
    assert leads
    lead_id = leads[0]["id"]
    res = client.patch(
        f"/api/admin/leads/{lead_id}",
        json={"status": "contacted"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "contacted"


def test_admin_invalid_lead_status(client, institute, auth_headers):
    client.post(
        "/api/leads",
        json={"name": "X", "phone": "9000099999"},
        headers={"X-Institute-Key": "test-key-institute-a"},
    )
    lead_id = client.get("/api/admin/leads", headers=auth_headers).json()[0]["id"]
    res = client.patch(
        f"/api/admin/leads/{lead_id}",
        json={"status": "banana"},
        headers=auth_headers,
    )
    assert res.status_code == 400


def test_admin_course_crud(client, institute, auth_headers):
    created = client.post(
        "/api/admin/courses",
        json={"name": "Cloud Computing", "fee": 30000, "duration": "5 months"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    cid = created.json()["id"]
    updated = client.patch(
        f"/api/admin/courses/{cid}", json={"fee": 32000}, headers=auth_headers
    )
    assert updated.status_code == 200
    assert updated.json()["fee"] == 32000
    deleted = client.delete(f"/api/admin/courses/{cid}", headers=auth_headers)
    assert deleted.status_code == 204


def test_admin_document_upload_and_index(client, institute, auth_headers):
    content = b"Hostel information: on-campus hostel available for outstation students."
    files = {"file": ("hostel.txt", io.BytesIO(content), "text/plain")}
    res = client.post(
        "/api/admin/documents",
        files=files,
        data={"title": "Hostel Info"},
        headers=auth_headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "indexed"
    assert body["chunks"] >= 1


def test_admin_document_rejects_bad_extension(client, institute, auth_headers):
    files = {"file": ("evil.exe", io.BytesIO(b"MZ..."), "application/octet-stream")}
    res = client.post("/api/admin/documents", files=files, headers=auth_headers)
    assert res.status_code == 400
