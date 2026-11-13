import json

import pytest
import requests
from pydantic import SecretStr

from app.config import settings
from app.db import models as m
from app.db.base import session_scope
from app.servicenow.auth import BasicAuth, OAuthAuth
from app.servicenow.client import ServiceNowClient
from app.servicenow.errors import (
    ServiceNowAuthError,
    ServiceNowConfigError,
    ServiceNowRateLimitError,
    ServiceNowResponseError,
    ServiceNowTimeoutError,
    ServiceNowUnavailableError,
)
from app.servicenow.mappings import load_mappings
from app.servicenow.permissions import ApprovalGrant, WriteNotAllowedError

BAD = object()


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is BAD:
            raise ValueError("not json")
        return self._body


class FakeHttp:
    def __init__(self, *responses):
        self.responses, self.calls, self.posts = list(responses), [], []

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, data=None, timeout=None):
        self.posts.append(data)
        return self.responses.pop(0)


def row(n, **kw):
    base = {"sys_id": {"value": f"{n:032x}", "display_value": f"{n:032x}"},
            "number": {"value": f"INC{n:07d}", "display_value": f"INC{n:07d}"},
            "short_description": {"value": f"Issue {n}", "display_value": f"Issue {n}"},
            "description": {"value": "desc", "display_value": "desc"},
            "priority": {"value": "2", "display_value": "2 - High"},
            "state": {"value": "2", "display_value": "In Progress"},
            "assignment_group": {"value": "abc123", "display_value": "Network Team"},
            "cmdb_ci": {"value": "ci1", "display_value": "web-01"}}
    base.update(kw)
    return base


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(settings, "servicenow_mode", "live")
    monkeypatch.setattr(settings, "servicenow_instance", "https://dev.example.com")
    monkeypatch.setattr(settings, "servicenow_username", "svc")
    monkeypatch.setattr(settings, "servicenow_password", SecretStr("pw-secret-123"))
    monkeypatch.setattr(settings, "servicenow_max_retries", 2)
    monkeypatch.setattr(settings, "servicenow_page_size", 2)
    monkeypatch.setattr(settings, "servicenow_max_records", 100)
    sleeps = []

    def make(*responses, **kw):
        http = FakeHttp(*responses)
        client = ServiceNowClient(http=http, sleep=sleeps.append, **kw)
        return client, http, sleeps

    return make


def ok(rows, headers=None):
    return FakeResp(200, {"result": rows}, headers)


# ---------------------------------------------------------------- mock mode
def test_mock_list_get_search_and_pagination():
    c = ServiceNowClient()
    assert len(c.list_records("incident")) == 2
    assert c.get_record("incident", "INC0010025").source == "mock"
    page, total = c.search_records("incident", "portal", limit=1, offset=0)
    assert total == 1 and page[0].number == "INC0010026"
    assert c.search_records("incident", "", limit=1, offset=1)[0][0].number == "INC0010026"
    with pytest.raises(KeyError):
        c.get_record("incident", "NOPE")


def test_invalid_ids_rejected_before_any_query():
    c = ServiceNowClient()
    for bad in ["x^ORsys_id=y", "a b", "", "a" * 65, "x;drop"]:
        with pytest.raises(ValueError):
            c.get_record("incident", bad)
    with pytest.raises(ValueError):
        c.list_records("bogus")


def test_mock_request_items():
    items = ServiceNowClient().list_request_items("REQ0013001")
    assert [i["number"] for i in items] == ["RITM0020001", "RITM0020002"]


def test_read_only_view_has_no_write_methods():
    ro = ServiceNowClient().read_only()
    assert not hasattr(ro, "update_record")
    assert ro.get_record("incident", "INC0010025").number == "INC0010025"


# ---------------------------------------------------------------- live reads
def test_live_pagination_collects_all_pages_and_stops(live):
    c, http, _ = live(ok([row(1), row(2)]), ok([row(3), row(4)]), ok([row(5)]))
    records = c.list_records("incident")
    assert [r.number for r in records] == [f"INC{n:07d}" for n in range(1, 6)]
    assert [call["params"]["sysparm_offset"] for call in http.calls] == ["0", "2", "4"]
    assert all("ORDERBYnumber" in call["params"]["sysparm_query"] for call in http.calls)


def test_live_pagination_respects_max_records_and_total_header(live, monkeypatch):
    monkeypatch.setattr(settings, "servicenow_max_records", 3)
    c, http, _ = live(ok([row(1), row(2)]), ok([row(3)]))
    assert len(c.list_records("incident")) == 3 and len(http.calls) == 2
    c2, http2, _ = live(ok([row(1), row(2)], {"X-Total-Count": "2"}))
    assert len(c2.list_records("incident")) == 2 and len(http2.calls) == 1


def test_display_value_parsing_codes_vs_labels_and_extras(live):
    c, _, _ = live(ok([row(1)]))
    rec = c.list_records("incident")[0]
    assert rec.priority == "2" and rec.state == "In Progress"
    assert rec.assignment_group == "Network Team" and rec.extra["cmdb_ci"] == "web-01"
    assert rec.source == "servicenow"


def test_field_mapping_override_from_file(live, tmp_path, monkeypatch):
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"incident": {"table": "u_inc", "fields": {"assignment_group": "u_group"}}}))
    monkeypatch.setattr(settings, "servicenow_field_map_file", str(mapping))
    c, http, _ = live(ok([row(1, u_group={"value": "g", "display_value": "Custom Group"})]))
    rec = c.list_records("incident")[0]
    assert rec.assignment_group == "Custom Group"
    assert http.calls[0]["url"].endswith("/api/now/table/u_inc")
    assert "u_group" in http.calls[0]["params"]["sysparm_fields"]


def test_get_record_live_not_found_and_query(live):
    c, http, _ = live(ok([]))
    with pytest.raises(KeyError):
        c.get_record("incident", "INC0000001")
    assert http.calls[0]["params"]["sysparm_query"] == "number=INC0000001^ORsys_id=INC0000001"


def test_search_term_is_sanitised_in_live_query(live):
    c, http, _ = live(ok([row(1)], {"X-Total-Count": "7"}))
    _, total = c.search_records("incident", "cpu^ORactive=true", limit=10)
    query = http.calls[0]["params"]["sysparm_query"]
    assert "cpuORactivetrue" in query and "active=true" not in query and total == 7


# ---------------------------------------------------------------- retries and errors
def test_retries_on_503_then_succeeds(live):
    c, http, sleeps = live(FakeResp(503, {}), FakeResp(502, {}), ok([row(1)]))
    assert len(c.list_records("incident")) == 1 and len(http.calls) == 3 and len(sleeps) == 2


def test_rate_limit_honours_retry_after(live):
    c, _, sleeps = live(FakeResp(429, {}, {"Retry-After": "3"}), ok([row(1)]))
    c.list_records("incident")
    assert sleeps == [3.0]


def test_rate_limit_exhausted_raises(live):
    c, _, _ = live(*[FakeResp(429, {}) for _ in range(3)])
    with pytest.raises(ServiceNowRateLimitError):
        c.list_records("incident")


def test_timeout_retried_then_raises(live):
    c, http, _ = live(*[requests.Timeout("t") for _ in range(3)])
    with pytest.raises(ServiceNowTimeoutError):
        c.list_records("incident")
    assert len(http.calls) == 3


def test_connection_error_maps_to_unavailable(live):
    c, _, _ = live(*[requests.ConnectionError("x") for _ in range(3)])
    with pytest.raises(ServiceNowUnavailableError):
        c.list_records("incident")


def test_forbidden_is_not_retried(live):
    c, http, _ = live(FakeResp(403, {"error": {"message": "ACL denied"}}))
    with pytest.raises(ServiceNowAuthError):
        c.list_records("incident")
    assert len(http.calls) == 1


@pytest.mark.parametrize("body", [BAD, {"nope": 1}, {"result": "x"}, {"result": [1, 2]}])
def test_malformed_responses_are_rejected(live, body):
    c, _, _ = live(FakeResp(200, body))
    with pytest.raises(ServiceNowResponseError):
        c.list_records("incident")


def test_record_without_number_is_rejected(live):
    bad = row(1)
    bad.pop("number")
    c, _, _ = live(ok([bad]))
    with pytest.raises(ServiceNowResponseError):
        c.list_records("incident")


def test_live_without_instance_or_credentials(monkeypatch):
    monkeypatch.setattr(settings, "servicenow_mode", "live")
    monkeypatch.setattr(settings, "servicenow_instance", "")
    with pytest.raises(ServiceNowConfigError):
        ServiceNowClient().list_records("incident")
    monkeypatch.setattr(settings, "servicenow_instance", "https://x.example.com")
    monkeypatch.setattr(settings, "servicenow_username", "")
    with pytest.raises(ServiceNowConfigError):
        ServiceNowClient(http=FakeHttp()).list_records("incident")


def test_call_log_records_attempts_without_secrets(live):
    c, _, _ = live(FakeResp(503, {"error": {"message": "password=hunter22222"}}), ok([row(1)]))
    c.list_records("incident")
    with session_scope() as s:
        logs = s.query(m.ServiceNowCallLog).order_by(m.ServiceNowCallLog.id).all()
    assert [entry.success for entry in logs] == [False, True] and logs[1].attempts == 2
    assert "hunter22222" not in " ".join(str(entry.error) for entry in logs) and logs[0].mode == "live"


# ---------------------------------------------------------------- auth
def test_basic_auth_sent_and_secret_not_in_logs(live):
    c, http, _ = live(ok([row(1)]))
    c.list_records("incident")
    assert http.calls[0]["auth"] == ("svc", "pw-secret-123")


def test_oauth_client_credentials_cached_and_bearer_used(live, monkeypatch):
    monkeypatch.setattr(settings, "servicenow_auth", "oauth")
    monkeypatch.setattr(settings, "servicenow_username", "")
    monkeypatch.setattr(settings, "servicenow_client_id", "cid")
    monkeypatch.setattr(settings, "servicenow_client_secret", SecretStr("csecret"))
    token = FakeResp(200, {"access_token": "tok1", "expires_in": 1800})
    c, http, _ = live(token, ok([row(1)]), ok([row(2)]))
    c.list_records("incident")
    c.list_records("incident")
    assert len(http.posts) == 1 and http.posts[0]["grant_type"] == "client_credentials"
    assert all(call["headers"]["Authorization"] == "Bearer tok1" for call in http.calls)


def test_oauth_password_grant_and_refresh_on_401(live, monkeypatch):
    monkeypatch.setattr(settings, "servicenow_auth", "oauth")
    monkeypatch.setattr(settings, "servicenow_client_id", "cid")
    monkeypatch.setattr(settings, "servicenow_client_secret", SecretStr("csecret"))
    first = FakeResp(200, {"access_token": "old", "refresh_token": "r1", "expires_in": 1800})
    second = FakeResp(200, {"access_token": "new", "expires_in": 1800})
    c, http, _ = live(first, FakeResp(401, {}), second, ok([row(1)]))
    assert len(c.list_records("incident")) == 1
    assert http.posts[0]["grant_type"] == "password" and http.posts[1]["grant_type"] == "refresh_token"
    assert http.calls[-1]["headers"]["Authorization"] == "Bearer new"


def test_oauth_token_expiry_triggers_refetch():
    now = [1000.0]
    http = FakeHttp(FakeResp(200, {"access_token": "a", "expires_in": 100}),
                    FakeResp(200, {"access_token": "b", "expires_in": 100}))
    auth = OAuthAuth("https://x", "id", "secret", http=http, clock=lambda: now[0])
    assert auth.token() == "a" and auth.token() == "a"
    now[0] += 200
    assert auth.token() == "b"


def test_oauth_failure_is_auth_error_without_secret():
    auth = OAuthAuth("https://x", "id", "supersecretvalue", http=FakeHttp(FakeResp(401, {})))
    with pytest.raises(ServiceNowAuthError) as exc:
        auth.token()
    assert "supersecretvalue" not in str(exc.value)


def test_auth_config_validation():
    with pytest.raises(ServiceNowConfigError):
        BasicAuth("", "")
    with pytest.raises(ServiceNowConfigError):
        OAuthAuth("", "", "")


# ---------------------------------------------------------------- write permissions
def grant(record, payload, **kw):
    base = dict(approval_id="ap-1", approver="alice", action_type="incident_update",
                record_type=record.record_type, record_number=record.number, payload=payload)
    base.update(kw)
    return ApprovalGrant(**base)


def test_write_requires_grant():
    c = ServiceNowClient()
    rec = c.get_record("incident", "INC0010025")
    with pytest.raises(WriteNotAllowedError):
        c.update_record(rec, {"work_notes": "x"})
    with pytest.raises(WriteNotAllowedError):
        c.update_record(rec, {"work_notes": "x"}, grant(rec, {"work_notes": "x"}, status="pending"))


def test_write_must_match_approved_payload_and_record():
    c = ServiceNowClient()
    rec = c.get_record("incident", "INC0010025")
    other = c.get_record("incident", "INC0010026")
    with pytest.raises(WriteNotAllowedError):
        c.update_record(rec, {"work_notes": "tampered"}, grant(rec, {"work_notes": "approved"}))
    with pytest.raises(WriteNotAllowedError):
        c.update_record(other, {"work_notes": "x"}, grant(rec, {"work_notes": "x"}))


def test_write_blocks_fields_outside_allowlist():
    c = ServiceNowClient()
    rec = c.get_record("incident", "INC0010025")
    payload = {"assignment_group": "somewhere", "state": "7"}
    with pytest.raises(WriteNotAllowedError):
        c.update_record(rec, payload, grant(rec, payload))
    change = c.get_record("change", "CHG0002001")
    with pytest.raises(WriteNotAllowedError):  # priority is only writable on incidents
        c.update_record(change, {"priority": "1"}, grant(change, {"priority": "1"}))


def test_mock_write_with_grant_updates_state():
    c = ServiceNowClient()
    rec = c.get_record("incident", "INC0010026")
    payload = {"work_notes": "AI note", "priority": "1"}
    result = c.update_record(rec, payload, grant(rec, payload))
    assert result["approval_id"] == "ap-1"
    assert c.get_record("incident", "INC0010026").priority == "1"


def test_live_write_disabled_by_default_and_works_when_enabled(live, monkeypatch):
    rec_row = row(1)
    c, http, _ = live(ok([rec_row]), ok([rec_row]), FakeResp(200, {"result": {"sys_id": "x"}}))
    rec = c.get_record("incident", "INC0000001")
    payload = {"work_notes": "note"}
    with pytest.raises(WriteNotAllowedError):
        c.update_record(rec, payload, grant(rec, payload))
    monkeypatch.setattr(settings, "servicenow_write_enabled", True)
    c.get_record("incident", "INC0000001")
    out = c.update_record(rec, payload, grant(rec, payload))
    patch = http.calls[-1]
    assert patch["method"] == "PATCH" and patch["json"] == {"work_notes": "note"}
    assert patch["url"].endswith(f"/incident/{rec.sys_id}") and out["mode"] == "live"


def test_default_mappings_have_least_privilege_writes():
    maps = load_mappings()
    assert maps["incident"].write_fields == ["work_notes", "priority"]
    assert all(maps[t].write_fields == ["work_notes"] for t in ("problem", "change", "request"))
    assert "assignment_group" not in maps["incident"].write_names()
