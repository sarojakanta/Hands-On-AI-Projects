import pytest

from app.approvals import (
    ApprovalPermissionError,
    ApprovalService,
    ApprovalStateError,
    ApprovalValidationError,
)
from app.approvals.policy import RULES, validate_action
from app.config import settings
from app.db import models as m
from app.db.base import session_scope
from app.security.rbac import Principal, has_role
from app.servicenow.client import ServiceNowClient
from app.servicenow.errors import ServiceNowUnavailableError
from app.services.investigation import InvestigationService

ALICE = Principal.of("alice", "analyst")          # requested the investigation
BOB = Principal.of("bob", "approver")
ADMIN = Principal.of("root", "admin")
VIEWER = Principal.of("vic", "viewer")


@pytest.fixture
def env():
    client = ServiceNowClient()
    # make a priority change necessary so more than one action type is exercised
    client._mock_rows["incident"][1]["priority"] = "4"
    svc = InvestigationService(servicenow=client)
    return client, svc, svc.approvals


def run(svc, record="INC0010026", requested_by="alice", **kw):
    return svc.investigate(record, "incident", "mock", requested_by=requested_by, **kw)


def by_type(approvals, action_type):
    return next(a for a in approvals if a["action_type"] == action_type)


def pending(approvals_svc, run_id):
    return approvals_svc.list(execution_id=run_id)


# ---------------------------------------------------------------- request phase
def test_agents_propose_but_nothing_is_written_before_approval(env):
    client, svc, approvals = env
    run_id, result = run(svc)
    assert result.status == "awaiting_approval"
    types = {a["action_type"] for a in result.proposed_actions}
    assert types == {"priority_change", "incident_update"}
    assert all(a["status"] == "pending_approval" and a["approval_id"] for a in result.proposed_actions)
    row = client._mock_rows["incident"][1]
    assert row["priority"] == "4" and "work_notes_log" not in row
    listed = pending(approvals, run_id)
    assert {a["status"] for a in listed} == {"pending"} and all(a["requested_by"] == "alice" for a in listed)


def test_requests_are_idempotent_per_execution(env):
    _, svc, approvals = env
    run_id, result = run(svc)
    action = result.proposed_actions[0]
    again = approvals.request(action_type=action["action_type"], record_type="incident",
                              record_number=action["record_number"], payload=action["payload"], execution_id=run_id)
    assert again["id"] == action["approval_id"]
    assert len(pending(approvals, run_id)) == 2


# ---------------------------------------------------------------- approve and execute
def test_approval_executes_write_resumes_workflow_and_is_audited(env):
    client, svc, approvals = env
    run_id, result = run(svc)
    note = by_type(pending(approvals, run_id), "incident_update")
    done = approvals.decide(note["id"], BOB, "approve", "Looks right")
    assert done["status"] == "executed" and done["decided_by"] == "bob" and done["decided_at"]
    assert done["execution_result"]["executed"] is True and done["execution_result"]["approval_id"] == note["id"]
    assert len(client._mock_rows["incident"][1]["work_notes_log"]) == 1
    assert client._mock_rows["incident"][1]["priority"] == "4"       # the other action is still pending
    with session_scope() as s:
        assert s.get(m.WorkflowExecution, run_id).status == "awaiting_approval"

    prio = by_type(pending(approvals, run_id), "priority_change")
    approvals.decide(prio["id"], BOB, "approve")
    assert client.get_record("incident", "INC0010026").priority == "2"
    with session_scope() as s:
        assert s.get(m.WorkflowExecution, run_id).status == "completed"     # workflow resumed and finished
        assert s.query(m.AuditEvent).filter_by(action="workflow.resumed_after_approval").count() == 1

    trail = [e["action"] for e in approvals.get(note["id"])["audit_trail"]]
    assert trail == ["approval.requested", "approval.approved", "approval.executed"]
    assert approvals.get(note["id"])["audit_trail"][1]["details"]["comment"] == "Looks right"
    stored = svc.get_result(run_id)
    assert stored.status == "completed" and {a["status"] for a in stored.proposed_actions} == {"executed"}


def test_double_approval_is_rejected_and_write_happens_once(env):
    client, svc, approvals = env
    run_id, _ = run(svc)
    note = by_type(pending(approvals, run_id), "incident_update")
    approvals.decide(note["id"], BOB, "approve")
    with pytest.raises(ApprovalStateError):
        approvals.decide(note["id"], ADMIN, "approve")
    with pytest.raises(ApprovalStateError):
        approvals.decide(note["id"], ADMIN, "reject", "changed my mind")
    assert len(client._mock_rows["incident"][1]["work_notes_log"]) == 1


# ---------------------------------------------------------------- rejection
def test_rejection_requires_comment_and_writes_nothing(env):
    client, svc, approvals = env
    run_id, _ = run(svc)
    a, b = pending(approvals, run_id)
    with pytest.raises(ApprovalValidationError):
        approvals.decide(a["id"], BOB, "reject", "   ")
    rejected = approvals.decide(a["id"], BOB, "reject", "Not justified")
    assert rejected["status"] == "rejected" and rejected["comment"] == "Not justified"
    approvals.decide(b["id"], BOB, "reject", "Also no")
    row = client._mock_rows["incident"][1]
    assert row["priority"] == "4" and "work_notes_log" not in row
    with session_scope() as s:
        assert s.get(m.WorkflowExecution, run_id).status == "rejected"
    assert svc.get_result(run_id).status == "rejected"


def test_mixed_decisions_complete_the_workflow(env):
    _, svc, approvals = env
    run_id, _ = run(svc)
    a, b = pending(approvals, run_id)
    approvals.decide(a["id"], BOB, "approve")
    approvals.decide(b["id"], BOB, "reject", "no")
    with session_scope() as s:
        assert s.get(m.WorkflowExecution, run_id).status == "completed"


# ---------------------------------------------------------------- permissions
def test_role_hierarchy():
    assert has_role(ADMIN, "approver") and has_role(BOB, "analyst") and not has_role(ALICE, "approver")
    assert not has_role(Principal("nobody", frozenset()), "viewer")
    with pytest.raises(ValueError):
        has_role(BOB, "superuser")


@pytest.mark.parametrize("who", [VIEWER, ALICE])
def test_insufficient_role_can_not_decide(env, who):
    client, svc, approvals = env
    run_id, _ = run(svc, requested_by="someone-else")
    target = pending(approvals, run_id)[0]
    with pytest.raises(ApprovalPermissionError):
        approvals.decide(target["id"], who, "approve")
    assert approvals.get(target["id"])["status"] == "pending"
    assert "work_notes_log" not in client._mock_rows["incident"][1]


def test_separation_of_duties_enforced_in_production(env, monkeypatch):
    _, svc, approvals = env
    monkeypatch.setattr(settings, "app_env", "production")
    run_id, _ = run(svc, requested_by="bob")
    target = pending(approvals, run_id)[0]
    with pytest.raises(ApprovalPermissionError, match="Separation of duties"):
        approvals.decide(target["id"], BOB, "approve")
    assert approvals.decide(target["id"], Principal.of("carol", "approver"), "approve")["status"] in (
        "executed", "approved")


def test_self_approval_allowed_in_development_and_overridable(env, monkeypatch):
    _, svc, approvals = env
    run_id, _ = run(svc, requested_by="bob")
    assert approvals.decide(pending(approvals, run_id)[0]["id"], BOB, "approve")["status"] == "executed"
    monkeypatch.setattr(settings, "approval_allow_self_approval", False)
    run_id2, _ = run(svc, requested_by="bob", force=True)
    with pytest.raises(ApprovalPermissionError):
        approvals.decide(pending(approvals, run_id2)[0]["id"], BOB, "approve")


def test_unknown_approval_id_and_bad_decision(env):
    _, _, approvals = env
    with pytest.raises(KeyError):
        approvals.decide("nope", BOB, "approve")
    with pytest.raises(ApprovalValidationError):
        approvals.decide("nope", BOB, "maybe")
    with pytest.raises(KeyError):
        approvals.get("nope")


# ---------------------------------------------------------------- policy and validation
def test_every_sensitive_action_type_requires_approval():
    assert set(RULES) == {"incident_update", "priority_change", "assignment_change", "change_approval",
                          "incident_closure", "remediation"}


@pytest.mark.parametrize("action,payload,msg", [
    ("run_shell", {"cmd": "rm -rf /"}, "Unknown action type"),
    ("incident_update", {"work_notes": "x", "state": "7"}, "not allowed"),
    ("incident_update", {}, "non-empty"),
    ("priority_change", {"priority": "0"}, "1-5"),
    ("priority_change", {"priority": "1; DROP"}, "1-5"),
])
def test_invalid_proposals_are_rejected(action, payload, msg):
    with pytest.raises(ApprovalValidationError, match=msg):
        validate_action(action, "incident", "INC1", payload)


def test_payload_is_sanitised_and_bounded():
    out = validate_action("incident_update", "incident", "INC1",
                          {"work_notes": "ok\x00 password=hunter22222 " + "x" * 9000})
    assert "\x00" not in out["work_notes"] and "hunter22222" not in out["work_notes"]
    assert len(out["work_notes"]) <= 8000
    with pytest.raises(ApprovalValidationError):
        validate_action("incident_update", "bogus", "INC1", {"work_notes": "x"})
    with pytest.raises(ApprovalValidationError):
        validate_action("incident_update", "incident", "", {"work_notes": "x"})


def test_invalid_agent_proposal_is_recorded_not_queued(env):
    _, _, approvals = env
    out = approvals.request_for_actions(
        [{"action_type": "run_shell", "record_type": "incident", "record_number": "INC1", "payload": {"cmd": "x"}}],
        execution_id="exec-1", requested_by="alice")
    assert out[0]["status"] == "invalid_proposal" and "approval_id" not in out[0]
    assert approvals.count() == 0
    with session_scope() as s:
        assert s.query(m.AuditEvent).filter_by(action="approval.proposal_rejected").count() == 1


# ---------------------------------------------------------------- non-executable and blocked actions
def test_remediation_needs_admin_and_is_never_executed(env):
    client, _, approvals = env
    req = approvals.request(action_type="remediation", record_type="incident", record_number="INC0010026",
                            payload={"description": "Restart the portal pool"}, requested_by="alice")
    with pytest.raises(ApprovalPermissionError):
        approvals.decide(req["id"], BOB, "approve")
    done = approvals.decide(req["id"], ADMIN, "approve", "will do manually")
    assert done["status"] == "approved" and done["execution_result"]["executed"] is False
    assert "work_notes_log" not in client._mock_rows["incident"][1]


def test_closure_is_approvable_but_blocked_by_servicenow_write_allowlist(env):
    client, _, approvals = env
    req = approvals.request(action_type="incident_closure", record_type="incident", record_number="INC0010026",
                            payload={"close_notes": "fixed", "state": "7"}, requested_by="alice")
    done = approvals.decide(req["id"], BOB, "approve")
    assert done["status"] == "failed" and "not permitted for write" in done["execution_result"]["error"]
    assert client.get_record("incident", "INC0010026").state == "New"


# ---------------------------------------------------------------- failure and retry
class FlakyClient:
    def __init__(self, real, failures):
        self.real, self.failures = real, failures

    def get_record(self, *a):
        return self.real.get_record(*a)

    def update_record(self, record, fields, grant):
        if self.failures:
            self.failures -= 1
            raise ServiceNowUnavailableError("ServiceNow down password=topsecret99")
        return self.real.update_record(record, fields, grant)


def test_failed_execution_is_retryable_and_workflow_stays_open(env):
    client, svc, _ = env
    approvals = ApprovalService(FlakyClient(client, failures=1))
    svc.approvals = approvals
    run_id, _ = run(svc)
    prio, note = by_type(pending(approvals, run_id), "priority_change"), by_type(pending(approvals, run_id), "incident_update")
    failed = approvals.decide(prio["id"], BOB, "approve")          # first write hits the injected outage
    assert failed["status"] == "failed" and "topsecret99" not in str(failed["execution_result"])
    approvals.decide(note["id"], BOB, "approve")                   # the other action is unaffected
    with session_scope() as s:
        assert s.get(m.WorkflowExecution, run_id).status == "awaiting_approval"   # still open
    with pytest.raises(ApprovalStateError):
        approvals.retry(note["id"], BOB)           # only failed ones can be retried
    with pytest.raises(ApprovalPermissionError):
        approvals.retry(prio["id"], VIEWER)
    retried = approvals.retry(prio["id"], BOB)
    assert retried["status"] == "executed" and client.get_record("incident", "INC0010026").priority == "2"
    with session_scope() as s:
        assert s.get(m.WorkflowExecution, run_id).status == "completed"
    assert "approval.retry" in [e["action"] for e in approvals.get(prio["id"])["audit_trail"]]


# ---------------------------------------------------------------- expiry
def test_expired_requests_can_not_be_approved_and_close_the_workflow(env, monkeypatch):
    client, svc, approvals = env
    run_id, _ = run(svc)
    monkeypatch.setattr(settings, "approval_ttl_hours", -1)     # everything pending is already stale
    target = pending(approvals, run_id)[0]
    with pytest.raises(ApprovalStateError, match="expired"):
        approvals.decide(target["id"], BOB, "approve")
    assert {a["status"] for a in approvals.list(execution_id=run_id)} == {"expired"}
    with session_scope() as s:
        assert s.get(m.WorkflowExecution, run_id).status == "rejected"
    assert "work_notes_log" not in client._mock_rows["incident"][1]


# ---------------------------------------------------------------- workflow interplay
def test_duplicate_run_blocked_until_approvals_are_settled(env):
    _, svc, approvals = env
    run_id, _ = run(svc)
    same_id, again = run(svc)
    assert same_id == run_id and again.duplicate is True
    for a in pending(approvals, run_id):
        approvals.decide(a["id"], BOB, "reject", "no")
    new_id, fresh = run(svc)
    assert new_id != run_id and fresh.duplicate is False


def test_ai_can_not_write_without_a_grant_even_via_the_agent_view(env):
    client, _, _ = env
    view = client.read_only()
    assert not hasattr(view, "update_record")
    from app.servicenow.permissions import WriteNotAllowedError
    with pytest.raises(WriteNotAllowedError):
        client.update_record(client.get_record("incident", "INC0010026"), {"work_notes": "x"})
