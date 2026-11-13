"""Executes the real Streamlit app (AppTest) against the real FastAPI app, in-process."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from app.main import create_app
from app.security import auth
from dashboard import api as dash_api
from dashboard.sections import SECTIONS
from dashboard.sections.common import progress_of
from dashboard.ui import badge, has_role

PW = "S3cure-passw0rd"
V1 = "/api/v1"
BASE = "http://testserver"


@pytest.fixture
def backend():
    client = TestClient(create_app())
    tokens = {}
    for role in ("viewer", "analyst", "approver", "admin"):
        auth.create_user(role, PW, [role])
        tokens[role] = client.post(f"{V1}/auth/login", json={"username": role, "password": PW}).json()["access_token"]
    return client, tokens


@pytest.fixture
def seeded(backend):
    """A couple of runs so every page has real data to render."""
    client, tokens = backend
    h = {"Authorization": f"Bearer {tokens['analyst']}"}
    for record, wf in [("INC0010026", "incident"), ("PRB0001001", "problem"), ("CHG0002001", "change"),
                       ("REQ0013001", "request")]:
        assert client.post(f"{V1}/investigations", headers=h,
                           json={"record_id": record, "workflow": wf, "llm_provider": "mock"}).status_code == 201
    return backend


APP_PATH = str(Path(__file__).resolve().parent.parent / "dashboard" / "app.py")


def app_for(client: TestClient, token: str | None, role: str | None = None) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.session_state["api"] = dash_api.ApiClient(BASE, http=client, token=token, timeout=None)
    return at


def errors_in(at: AppTest) -> list[str]:
    return [e.value for e in at.error] + [str(x.value) for x in at.exception]


def test_unauthenticated_user_sees_only_the_login_form(backend):
    client, _ = backend
    at = app_for(client, None).run()
    assert not at.exception
    assert any("Sign in" in t.value for t in at.markdown) or any(b.label == "Sign in" for b in at.button)
    assert len(at.sidebar.radio) == 0          # no navigation before login


def test_login_form_authenticates_and_shows_navigation(backend):
    client, _ = backend
    at = app_for(client, None).run()
    at.text_input[0].input("analyst")
    at.text_input[1].input(PW)
    at.button[0].click().run()
    assert not at.exception and not errors_in(at)
    assert at.sidebar.radio[0].options == [label for label, _ in SECTIONS]
    assert len(SECTIONS) == 13


def test_bad_credentials_show_a_generic_error(backend):
    client, _ = backend
    at = app_for(client, None).run()
    at.text_input[0].input("analyst")
    at.text_input[1].input("wrong-password")
    at.button[0].click().run()
    assert any("Invalid username or password" in e for e in errors_in(at))
    assert len(at.sidebar.radio) == 0


@pytest.mark.parametrize("role", ["admin", "approver", "analyst", "viewer"])
def test_every_section_renders_without_errors_for_every_role(seeded, role):
    client, tokens = seeded
    at = app_for(client, tokens[role]).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    for label, _ in SECTIONS:
        at.sidebar.radio[0].set_value(label).run()
        assert not at.exception, (label, [str(e.value) for e in at.exception])
        assert not errors_in(at), (label, errors_in(at))


def test_executive_dashboard_shows_real_numbers(seeded):
    client, tokens = seeded
    at = app_for(client, tokens["viewer"]).run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Incidents"] == "2" and metrics["Workflow runs"] == "4"
    assert int(metrics["Pending approvals"]) >= 4


def test_record_page_runs_workflow_and_renders_result(backend):
    client, tokens = backend
    at = app_for(client, tokens["analyst"]).run()
    at.sidebar.radio[0].set_value("Incident Management").run()
    at.button(key="incident-run").click().run()
    assert not at.exception and not errors_in(at)
    assert any("Agent workflow results" in s.value for s in at.subheader)
    assert any("Assessment" in m.value for m in at.markdown)
    assert any(t.label == "Confidence (hypothesis)" for t in at.metric)
    runs = client.get(f"{V1}/investigations", headers={"Authorization": f"Bearer {tokens['viewer']}"}).json()
    assert runs["total"] == 1 and runs["items"][0]["requested_by"] == "analyst"


def test_viewer_can_not_start_workflows(backend):
    client, tokens = backend
    at = app_for(client, tokens["viewer"]).run()
    at.sidebar.radio[0].set_value("Incident Management").run()
    assert not [b for b in at.button if b.key == "incident-run"]
    assert any("read-only" in i.value for i in at.info)


def test_approver_approves_from_the_dashboard_and_servicenow_is_updated(seeded):
    client, tokens = seeded
    at = app_for(client, tokens["approver"]).run()
    at.sidebar.radio[0].set_value("Human Approval Center").run()
    approve = [b for b in at.button if b.key and b.key.startswith("center-pending-") and b.key.endswith("-approve")]
    assert approve and not approve[0].disabled
    approve[0].click().run()
    assert not at.exception and not errors_in(at)
    h = {"Authorization": f"Bearer {tokens['approver']}"}
    done = client.get(f"{V1}/approvals?status=executed", headers=h).json()
    assert done["total"] == 1 and done["items"][0]["decided_by"] == "approver"


def test_reject_without_comment_shows_the_api_error(seeded):
    client, tokens = seeded
    at = app_for(client, tokens["approver"]).run()
    at.sidebar.radio[0].set_value("Human Approval Center").run()
    reject = next(b for b in at.button if b.key and b.key.startswith("center-pending-") and b.key.endswith("-reject"))
    reject.click().run()
    assert any("comment is required" in e for e in errors_in(at))


def test_analyst_sees_approvals_but_can_not_decide(seeded):
    client, tokens = seeded
    at = app_for(client, tokens["analyst"]).run()
    at.sidebar.radio[0].set_value("Human Approval Center").run()
    buttons = [b for b in at.button if b.key and b.key.endswith("-approve") and b.key.startswith("center-pending-")]
    assert buttons and all(b.disabled for b in buttons)


def test_admin_only_areas_are_hidden_from_other_roles(seeded):
    client, tokens = seeded
    admin = app_for(client, tokens["admin"]).run()
    admin.sidebar.radio[0].set_value("System Configuration").run()
    assert any(s.value == "Users" for s in admin.subheader)
    viewer = app_for(client, tokens["viewer"]).run()
    viewer.sidebar.radio[0].set_value("System Configuration").run()
    assert not [s for s in viewer.subheader if s.value == "Users"]
    viewer.sidebar.radio[0].set_value("Audit Logs").run()
    assert any("approvers and administrators" in i.value for i in viewer.info)


def test_secrets_never_reach_the_rendered_page(seeded, monkeypatch):
    from pydantic import SecretStr

    from app.config import settings

    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_supersecretkey_1234567890"))
    monkeypatch.setattr(settings, "database_url", "postgresql://itsm:dbpassword99@db:5432/itsm")
    client, tokens = seeded
    at = app_for(client, tokens["admin"]).run()
    for label in ("System Configuration", "LLM Provider Configuration"):
        at.sidebar.radio[0].set_value(label).run()
        page = " ".join(str(e) for e in [*at.markdown, *at.dataframe, *at.json, *at.text_input, *at.caption])
        assert "supersecretkey" not in page and "dbpassword99" not in page


def test_monitor_shows_timeline_for_a_failed_run_with_resume(seeded):
    client, tokens = seeded
    from app.db import models as m
    from app.db.base import session_scope

    with session_scope() as s:
        run = s.query(m.WorkflowExecution).first()
        run.status, run.error = "partial", "knowledge: search backend down"
    at = app_for(client, tokens["analyst"]).run()
    at.sidebar.radio[0].set_value("Workflow Execution Monitor").run()
    assert not at.exception
    options = at.selectbox(key="monitor-run").options
    pick = next(o for o in options if "partial" in o)
    at.selectbox(key="monitor-run").select(pick).run()
    assert any("search backend down" in e for e in errors_in(at))
    assert at.button(key="monitor-resume")


# ---------------------------------------------------------------- pure helpers
def test_badges_escape_untrusted_text():
    out = badge('<script>alert(1)</script>')
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_role_helper_and_progress():
    assert has_role({"roles": ["approver"]}, "analyst") and not has_role({"roles": ["viewer"]}, "analyst")
    assert not has_role({"roles": []}, "viewer")
    steps = [{"agent": "classification", "status": "success"}, {"agent": "priority", "status": "skipped"}]
    fraction, remaining = progress_of("incident", steps)
    assert 0 < fraction < 1 and "rca" in remaining
    assert progress_of("incident", [{"agent": a, "status": "success"} for a in
                                    ["classification", "priority", "investigation", "knowledge", "rca", "resolution",
                                     "verification"]])[0] == 1.0


def test_api_client_turns_failures_into_readable_errors(backend):
    client, tokens = backend
    api = dash_api.ApiClient(BASE, http=client, token=tokens["viewer"], timeout=None)
    with pytest.raises(dash_api.ApiError) as exc:
        api.get("/records/incident/NOPE")
    assert exc.value.status == 404 and exc.value.request_id and "request" in str(exc.value)
    down = dash_api.ApiClient("http://127.0.0.1:1", token="x")
    with pytest.raises(dash_api.ApiError) as unreachable:
        down.get("/auth/me")
    assert unreachable.value.code == "unreachable"
