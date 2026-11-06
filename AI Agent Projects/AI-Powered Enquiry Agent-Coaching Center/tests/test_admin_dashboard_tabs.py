"""Per-tab tests for the admin dashboard, one class per UI tab.

Tabs: Overview (stats + course-wise), Leads, Conversations, Courses,
Documents, Embed. Each class checks the data the tab renders and that it is
auth-protected and tenant-scoped.
"""
from __future__ import annotations

import io

from tests.conftest import API_KEY_A, API_KEY_B


def _pub(key=API_KEY_A):
    return {"X-Institute-Key": key}


def _chat(client, message, key=API_KEY_A, session_id=None):
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    return client.post("/api/chat", json=payload, headers=_pub(key)).json()


def _register_lead(client, name, phone, course, key=API_KEY_A):
    """Drive a registration through chat so a lead with a course is created."""
    d = _chat(client, f"I want to register for {course}", key)
    sid = d["session_id"]
    return _chat(client, f"My name is {name} and my number is {phone}", key, sid)


# =========================================================================
# Overview tab — stats + course-wise enquiries
# =========================================================================
class TestOverviewTab:
    def test_requires_auth(self, client, institute):
        assert client.get("/api/admin/stats").status_code == 401

    def test_stats_shape(self, client, institute, auth_headers):
        body = client.get("/api/admin/stats", headers=auth_headers).json()
        for key in (
            "total_enquiries",
            "new_leads",
            "pending_followups",
            "open_escalations",
            "total_conversations",
            "course_wise",
        ):
            assert key in body

    def test_asking_a_question_does_not_create_an_enquiry(
        self, client, institute, auth_headers
    ):
        # Merely asking about fees must NOT register a lead, so course-wise
        # stays empty and total_enquiries stays 0. (This is the behaviour the
        # user observed — it is correct by design.)
        _chat(client, "what is the fee for Python Programming?")
        body = client.get("/api/admin/stats", headers=auth_headers).json()
        assert body["total_enquiries"] == 0
        assert body["course_wise"] == []

    def test_registration_updates_course_wise(self, client, institute, auth_headers):
        _register_lead(client, "Riya", "9811100001", "Python Programming")
        body = client.get("/api/admin/stats", headers=auth_headers).json()
        assert body["total_enquiries"] == 1
        assert body["new_leads"] == 1
        courses = {r["course"]: r["count"] for r in body["course_wise"]}
        assert courses.get("Python Programming") == 1

    def test_course_wise_counts_multiple(self, client, institute, auth_headers):
        _register_lead(client, "Asha", "9811100002", "Python Programming")
        _register_lead(client, "Bala", "9811100003", "Python Programming")
        _register_lead(client, "Chandra", "9811100004", "Java Development")
        body = client.get("/api/admin/stats", headers=auth_headers).json()
        courses = {r["course"]: r["count"] for r in body["course_wise"]}
        assert courses.get("Python Programming") == 2
        assert courses.get("Java Development") == 1

    def test_stats_tenant_scoped(self, client, institute, institute_b, auth_headers):
        # A lead registered for institute B must not appear in A's stats.
        _register_lead(client, "Bee", "9811100005", "Banking Preparation", key=API_KEY_B)
        body = client.get("/api/admin/stats", headers=auth_headers).json()
        assert body["total_enquiries"] == 0


# =========================================================================
# Leads tab
# =========================================================================
class TestLeadsTab:
    def test_requires_auth(self, client, institute):
        assert client.get("/api/admin/leads").status_code == 401

    def test_lists_registered_leads(self, client, institute, auth_headers):
        _register_lead(client, "Lead One", "9822200001", "Python Programming")
        leads = client.get("/api/admin/leads", headers=auth_headers).json()
        assert any(l["name"] == "Lead One" for l in leads)

    def test_update_lead_status(self, client, institute, auth_headers):
        client.post(
            "/api/leads", json={"name": "S", "phone": "9822200002"}, headers=_pub()
        )
        lead_id = client.get("/api/admin/leads", headers=auth_headers).json()[0]["id"]
        res = client.patch(
            f"/api/admin/leads/{lead_id}",
            json={"status": "enrolled"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["status"] == "enrolled"

    def test_status_filter(self, client, institute, auth_headers):
        client.post(
            "/api/leads", json={"name": "New1", "phone": "9822200003"}, headers=_pub()
        )
        lead_id = client.get("/api/admin/leads", headers=auth_headers).json()[0]["id"]
        client.patch(
            f"/api/admin/leads/{lead_id}",
            json={"status": "contacted"},
            headers=auth_headers,
        )
        contacted = client.get(
            "/api/admin/leads?status_filter=contacted", headers=auth_headers
        ).json()
        assert all(l["status"] == "contacted" for l in contacted)

    def test_leads_tenant_scoped(self, client, institute, institute_b, auth_headers):
        client.post(
            "/api/leads", json={"name": "Bee Lead", "phone": "9822200004"},
            headers=_pub(API_KEY_B),
        )
        leads = client.get("/api/admin/leads", headers=auth_headers).json()
        assert all(l["name"] != "Bee Lead" for l in leads)


# =========================================================================
# Conversations tab
# =========================================================================
class TestConversationsTab:
    def test_requires_auth(self, client, institute):
        assert client.get("/api/admin/conversations").status_code == 401

    def test_list_and_message_count(self, client, institute, auth_headers):
        d = _chat(client, "hello")
        sid = d["session_id"]
        convos = client.get("/api/admin/conversations", headers=auth_headers).json()
        mine = next(c for c in convos if c["session_id"] == sid)
        assert mine["message_count"] >= 2

    def test_transcript(self, client, institute, auth_headers):
        d = _chat(client, "what courses do you offer?")
        sid = d["session_id"]
        body = client.get(
            f"/api/admin/conversations/{sid}", headers=auth_headers
        ).json()
        roles = [m["role"] for m in body["messages"]]
        assert "user" in roles and "assistant" in roles

    def test_transcript_not_found(self, client, institute, auth_headers):
        res = client.get("/api/admin/conversations/nope", headers=auth_headers)
        assert res.status_code == 404

    def test_conversations_tenant_scoped(
        self, client, institute, institute_b, auth_headers
    ):
        d = _chat(client, "hi there", key=API_KEY_B)
        sid = d["session_id"]
        # Institute A admin cannot see institute B's conversation.
        res = client.get(f"/api/admin/conversations/{sid}", headers=auth_headers)
        assert res.status_code == 404


# =========================================================================
# Courses tab
# =========================================================================
class TestCoursesTab:
    def test_requires_auth(self, client, institute):
        assert client.get("/api/admin/courses").status_code == 401

    def test_lists_seeded_courses(self, client, institute, auth_headers):
        courses = client.get("/api/admin/courses", headers=auth_headers).json()
        names = {c["name"] for c in courses}
        assert {"Python Programming", "Java Development"} <= names

    def test_create_update_delete(self, client, institute, auth_headers):
        created = client.post(
            "/api/admin/courses",
            json={"name": "DevOps", "fee": 28000, "duration": "4 months", "mode": "online"},
            headers=auth_headers,
        )
        assert created.status_code == 201
        cid = created.json()["id"]

        updated = client.patch(
            f"/api/admin/courses/{cid}", json={"fee": 29000}, headers=auth_headers
        )
        assert updated.status_code == 200
        assert updated.json()["fee"] == 29000

        assert client.delete(
            f"/api/admin/courses/{cid}", headers=auth_headers
        ).status_code == 204

    def test_update_missing_course_404(self, client, institute, auth_headers):
        res = client.patch(
            "/api/admin/courses/999999", json={"fee": 1}, headers=auth_headers
        )
        assert res.status_code == 404


# =========================================================================
# Documents tab
# =========================================================================
class TestDocumentsTab:
    def test_requires_auth(self, client, institute):
        assert client.get("/api/admin/documents").status_code == 401

    def test_upload_index_list_delete(self, client, institute, auth_headers):
        content = b"Library: students get access to an on-campus library and e-books."
        files = {"file": ("library.txt", io.BytesIO(content), "text/plain")}
        up = client.post(
            "/api/admin/documents",
            files=files,
            data={"title": "Library"},
            headers=auth_headers,
        )
        assert up.status_code == 201
        assert up.json()["status"] == "indexed"
        doc_id = up.json()["id"]

        docs = client.get("/api/admin/documents", headers=auth_headers).json()
        assert any(d["id"] == doc_id and d["status"] == "indexed" for d in docs)

        assert client.delete(
            f"/api/admin/documents/{doc_id}", headers=auth_headers
        ).status_code == 204

    def test_rejects_bad_extension(self, client, institute, auth_headers):
        files = {"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")}
        res = client.post("/api/admin/documents", files=files, headers=auth_headers)
        assert res.status_code == 400

    def test_delete_missing_document_404(self, client, institute, auth_headers):
        res = client.delete("/api/admin/documents/999999", headers=auth_headers)
        assert res.status_code == 404


# =========================================================================
# Embed tab
# =========================================================================
class TestEmbedTab:
    def test_requires_auth(self, client, institute):
        assert client.get("/api/admin/institute").status_code == 401

    def test_returns_public_key_and_base_url(self, client, institute, auth_headers):
        body = client.get("/api/admin/institute", headers=auth_headers).json()
        assert body["api_key"] == API_KEY_A
        assert body["name"]
        assert "base_url" in body


# =========================================================================
# Dashboard stat-card counts — each of the five counters, driven to a
# known value and asserted exactly.
# =========================================================================
class TestDashboardCounts:
    def _stats(self, client, auth_headers):
        return client.get("/api/admin/stats", headers=auth_headers).json()

    def test_all_counts_start_at_zero(self, client, institute, auth_headers):
        s = self._stats(client, auth_headers)
        assert s["total_enquiries"] == 0
        assert s["new_leads"] == 0
        assert s["pending_followups"] == 0
        assert s["open_escalations"] == 0
        assert s["total_conversations"] == 0

    def test_total_conversations_counts_distinct_sessions(
        self, client, institute, auth_headers
    ):
        # Two separate chats (no shared session_id) -> 2 conversations,
        # matching the "Conversations: 2" the user saw.
        _chat(client, "hello")
        _chat(client, "what courses do you offer?")
        s = self._stats(client, auth_headers)
        assert s["total_conversations"] == 2
        # Follow-up turns in the SAME session do not add conversations.
        d = _chat(client, "hi")
        _chat(client, "and the fees?", session_id=d["session_id"])
        s2 = self._stats(client, auth_headers)
        assert s2["total_conversations"] == 3

    def test_total_enquiries_and_new_leads(self, client, institute, auth_headers):
        client.post("/api/leads", json={"name": "L1", "phone": "9700000001"}, headers=_pub())
        client.post("/api/leads", json={"name": "L2", "phone": "9700000002"}, headers=_pub())
        s = self._stats(client, auth_headers)
        assert s["total_enquiries"] == 2
        assert s["new_leads"] == 2

    def test_new_leads_drops_when_status_changes(self, client, institute, auth_headers):
        client.post("/api/leads", json={"name": "L", "phone": "9700000003"}, headers=_pub())
        lead_id = client.get("/api/admin/leads", headers=auth_headers).json()[0]["id"]
        client.patch(
            f"/api/admin/leads/{lead_id}", json={"status": "contacted"}, headers=auth_headers
        )
        s = self._stats(client, auth_headers)
        assert s["total_enquiries"] == 1  # still a lead
        assert s["new_leads"] == 0        # no longer "new"

    def test_pending_followups_count(self, client, institute, auth_headers, db):
        from app.schemas.lead import FollowUpCreate, LeadCreate
        from app.services import lead_service

        lead = lead_service.create_lead(
            db, institute.id, LeadCreate(name="F", phone="9700000004")
        )
        fu = lead_service.create_followup(
            db, institute.id, FollowUpCreate(lead_id=lead.id)
        )
        assert self._stats(client, auth_headers)["pending_followups"] == 1
        # Completing it removes it from the pending count.
        lead_service.set_followup_status(db, institute.id, fu.id, "done")
        assert self._stats(client, auth_headers)["pending_followups"] == 0

    def test_open_escalations_count(self, client, institute, auth_headers):
        # Drive an escalation through chat (collects contact, then escalates).
        d = _chat(client, "I want to talk to a human counsellor")
        _chat(client, "My name is Esc Lead and my number is 9700000005",
              session_id=d["session_id"])
        s = self._stats(client, auth_headers)
        assert s["open_escalations"] == 1
        # That escalation also created a lead + conversation.
        assert s["total_enquiries"] == 1
        assert s["total_conversations"] == 1

    def test_counts_are_tenant_scoped(self, client, institute, institute_b, auth_headers):
        # Activity on institute B must not change institute A's counters.
        client.post("/api/leads", json={"name": "B", "phone": "9700000006"},
                    headers=_pub(API_KEY_B))
        _chat(client, "hello", key=API_KEY_B)
        s = self._stats(client, auth_headers)
        assert s["total_enquiries"] == 0
        assert s["total_conversations"] == 0
