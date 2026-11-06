"""Tests for public course information APIs: details, fees, batches."""
from __future__ import annotations

from tests.conftest import API_KEY_A


def _hdr():
    return {"X-Institute-Key": API_KEY_A}


def test_list_courses(client, institute):
    res = client.get("/api/courses", headers=_hdr())
    assert res.status_code == 200
    names = {c["name"] for c in res.json()}
    assert {"Python Programming", "Java Development"} <= names


def test_get_course_details(client, institute):
    res = client.get("/api/courses", headers=_hdr())
    course_id = next(c["id"] for c in res.json() if c["name"] == "Python Programming")
    detail = client.get(f"/api/courses/{course_id}", headers=_hdr())
    assert detail.status_code == 200
    body = detail.json()
    assert body["name"] == "Python Programming"
    assert body["fee"] == 15000.0
    assert body["duration"] == "3 months"


def test_course_fee_value(client, institute):
    res = client.get("/api/courses", headers=_hdr())
    java = next(c for c in res.json() if c["name"] == "Java Development")
    assert java["fee"] == 20000.0


def test_get_batches(client, institute):
    res = client.get("/api/courses", headers=_hdr())
    py_id = next(c["id"] for c in res.json() if c["name"] == "Python Programming")
    batches = client.get(f"/api/courses/{py_id}/batches", headers=_hdr())
    assert batches.status_code == 200
    data = batches.json()
    assert len(data) == 1
    assert data[0]["name"] == "PY-Morning"
    assert data[0]["seats_available"] == 10


def test_course_not_found(client, institute):
    res = client.get("/api/courses/99999", headers=_hdr())
    assert res.status_code == 404
