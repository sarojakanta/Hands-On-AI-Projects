"""Reusable human-in-the-loop approval service.

Lifecycle: pending -> approved -> executed | failed (retryable)
                   -> rejected | expired
Only an approved request can produce an ``ApprovalGrant``; ServiceNow writes are
impossible without one. Every transition is audited with actor, comment and time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import update

from app.approvals.policy import (
    RULES,
    ApprovalStateError,
    ApprovalValidationError,
    check_can_decide,
    validate_action,
)
from app.config import settings
from app.db import models as m
from app.db.base import session_scope
from app.db.repositories import ApprovalRepository, AuditRepository
from app.security.rbac import Principal
from app.security.sanitize import mask_secrets
from app.servicenow.permissions import ApprovalGrant

logger = logging.getLogger(__name__)

# execution (workflow) statuses driven by approvals
PENDING_LIKE = ("pending", "failed")  # failed executions stay open: they can be retried


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def to_dict(row: m.ApprovalRequest) -> dict:
    return {
        "id": row.id, "workflow_execution_id": row.workflow_execution_id, "action_type": row.action_type,
        "record_type": row.record_type, "record_number": row.record_number, "payload": row.payload,
        "status": row.status, "requested_by": row.requested_by,
        "requested_at": _aware(row.requested_at).isoformat() if row.requested_at else None,
        "decided_by": row.decided_by, "decided_at": _aware(row.decided_at).isoformat() if row.decided_at else None,
        "comment": row.comment, "execution_result": row.execution_result,
        "requires_role": RULES[row.action_type].required_role if row.action_type in RULES else "admin",
        "executable": RULES[row.action_type].executable if row.action_type in RULES else False,
    }


# --------------------------------------------------------------------- executors
def _write_executor(client, grant: ApprovalGrant) -> dict:
    record = client.get_record(grant.record_type, grant.record_number)
    return client.update_record(record, dict(grant.payload), grant)


# Only actions listed here are ever executed automatically. "remediation" is deliberately absent.
EXECUTORS: dict[str, Callable] = {
    "incident_update": _write_executor,
    "priority_change": _write_executor,
    "change_approval": _write_executor,
    "assignment_change": _write_executor,   # still blocked by the ServiceNow write allow-list unless configured
    "incident_closure": _write_executor,    # still blocked by the ServiceNow write allow-list unless configured
}


class ApprovalService:
    def __init__(self, servicenow):
        self.servicenow = servicenow

    # ------------------------------------------------------------- request
    def request(self, *, action_type: str, record_type: str, record_number: str, payload: dict,
                execution_id: str | None = None, requested_by: str = "system", rationale: str = "") -> dict:
        cleaned = validate_action(action_type, record_type, record_number, payload)
        with session_scope() as s:
            repo = ApprovalRepository(s)
            if execution_id:  # idempotent per execution/action/record
                for existing in repo.list(limit=200, workflow_execution_id=execution_id, status="pending"):
                    if (existing.action_type, existing.record_number) == (action_type, record_number):
                        return to_dict(existing)
            row = repo.add(m.ApprovalRequest(
                workflow_execution_id=execution_id, action_type=action_type, record_type=record_type,
                record_number=record_number, payload=cleaned, requested_by=requested_by, status="pending"))
            AuditRepository(s).record(requested_by, "approval.requested", "approval", row.id, action_type=action_type,
                                      record=record_number, rationale=mask_secrets(rationale)[:300])
            return to_dict(row)

    def request_for_actions(self, actions: list[dict], execution_id: str, requested_by: str) -> list[dict]:
        """Create approvals for agent-proposed actions. Invalid proposals are recorded, not executed."""
        out = []
        for action in actions:
            try:
                approval = self.request(
                    action_type=action.get("action_type", ""), record_type=action.get("record_type", ""),
                    record_number=action.get("record_number", ""), payload=action.get("payload", {}),
                    execution_id=execution_id, requested_by=requested_by, rationale=action.get("rationale", ""))
                out.append({**action, "approval_id": approval["id"], "status": "pending_approval"})
            except ApprovalValidationError as exc:
                with session_scope() as s:
                    AuditRepository(s).record("system", "approval.proposal_rejected", "workflow_execution",
                                              execution_id, reason=str(exc)[:300],
                                              action_type=str(action.get("action_type"))[:60])
                out.append({**action, "status": "invalid_proposal", "error": str(exc)})
        return out

    # ------------------------------------------------------------ decisions
    def decide(self, approval_id: str, principal: Principal, decision: str, comment: str = "") -> dict:
        if decision not in ("approve", "reject"):
            raise ApprovalValidationError("decision must be 'approve' or 'reject'")
        if decision == "reject" and not comment.strip():
            raise ApprovalValidationError("A comment is required when rejecting")
        self.expire_stale()
        new_status = "approved" if decision == "approve" else "rejected"
        with session_scope() as s:
            row = s.get(m.ApprovalRequest, approval_id)
            if row is None:
                raise KeyError("Approval request not found")
            check_can_decide(principal, row.action_type, row.requested_by)
            if row.status != "pending":
                raise ApprovalStateError(f"Approval is already {row.status}")
            execution_id = row.workflow_execution_id
            # Conditional update: of two concurrent decisions only one can win.
            won = s.execute(update(m.ApprovalRequest).where(
                m.ApprovalRequest.id == approval_id, m.ApprovalRequest.status == "pending").values(
                status=new_status, decided_by=principal.username, decided_at=_now(), comment=comment.strip()[:2000]))
            if won.rowcount != 1:
                raise ApprovalStateError("Approval was decided concurrently")
            AuditRepository(s).record(principal.username, f"approval.{new_status}", "approval", approval_id,
                                      comment=comment.strip()[:500], action_type=row.action_type)
        if decision == "approve":
            return self._execute(approval_id, principal.username)
        self._resume(execution_id)
        return self.get(approval_id)

    def retry(self, approval_id: str, principal: Principal) -> dict:
        """Re-attempt execution of an approved action whose ServiceNow write failed."""
        with session_scope() as s:
            row = s.get(m.ApprovalRequest, approval_id)
            if row is None:
                raise KeyError("Approval request not found")
            check_can_decide(principal, row.action_type, row.requested_by)
            if row.status != "failed":
                raise ApprovalStateError(f"Only failed executions can be retried (status is {row.status})")
            AuditRepository(s).record(principal.username, "approval.retry", "approval", approval_id)
        return self._execute(approval_id, principal.username)

    # ------------------------------------------------------------ execution
    def _execute(self, approval_id: str, actor: str) -> dict:
        with session_scope() as s:
            row = s.get(m.ApprovalRequest, approval_id)
            if row.status not in ("approved", "failed"):
                raise ApprovalStateError(f"Cannot execute an approval in status {row.status}")
            snap = dict(action=row.action_type, rtype=row.record_type, number=row.record_number,
                        payload=dict(row.payload), approver=row.decided_by, execution_id=row.workflow_execution_id)
        executor = EXECUTORS.get(snap["action"])
        if executor is None or not RULES[snap["action"]].executable:
            status, result = "approved", {"executed": False,
                                          "reason": "No automatic executor exists for this action; perform it manually."}
        else:
            grant = ApprovalGrant(approval_id=approval_id, approver=snap["approver"], action_type=snap["action"],
                                  record_type=snap["rtype"], record_number=snap["number"], payload=snap["payload"])
            try:
                result = {"executed": True, **executor(self.servicenow, grant)}
                status = "executed"
            except Exception as exc:  # noqa: BLE001 - recorded; approval stays retryable
                status, result = "failed", {"executed": False, "error": mask_secrets(f"{type(exc).__name__}: {exc}")[:500]}
                logger.warning("Approval %s execution failed: %s", approval_id, result["error"])
        with session_scope() as s:
            row = s.get(m.ApprovalRequest, approval_id)
            row.status, row.execution_result = status, result
            AuditRepository(s).record(actor, f"approval.{'execution_failed' if status == 'failed' else status}",
                                      "approval", approval_id, action_type=snap["action"], record=snap["number"],
                                      error=result.get("error"))
        self._resume(snap["execution_id"])
        return self.get(approval_id)

    # --------------------------------------------------------------- resume
    def _resume(self, execution_id: str | None) -> None:
        """Advance the owning workflow once its approvals are settled."""
        if not execution_id:
            return
        with session_scope() as s:
            ex = s.get(m.WorkflowExecution, execution_id)
            if ex is None or ex.status != "awaiting_approval":
                return
            rows = ApprovalRepository(s).list(limit=500, workflow_execution_id=execution_id)
            statuses = [r.status for r in rows]
            if any(st in PENDING_LIKE for st in statuses):
                return  # still waiting on a human, or on a retry of a failed write
            ex.status = "completed" if any(st in ("executed", "approved") for st in statuses) else "rejected"
            ex.finished_at = _now()
            ex.state = {**(ex.state or {}), "approvals": {r.id: r.status for r in rows}}
            AuditRepository(s).record("system", "workflow.resumed_after_approval", "workflow_execution",
                                      execution_id, outcome=ex.status)

    # --------------------------------------------------------------- expiry
    def expire_stale(self) -> int:
        cutoff = _now() - timedelta(hours=settings.approval_ttl_hours)
        expired = 0
        with session_scope() as s:
            stale = [r for r in ApprovalRepository(s).list(limit=500, status="pending")
                     if _aware(r.requested_at) < cutoff]
            for row in stale:
                row.status, row.comment = "expired", "Expired without a decision"
                AuditRepository(s).record("system", "approval.expired", "approval", row.id)
                expired += 1
            executions = {r.workflow_execution_id for r in stale}
        for execution_id in executions:
            self._resume(execution_id)
        return expired

    # ---------------------------------------------------------------- query
    def get(self, approval_id: str) -> dict:
        with session_scope() as s:
            row = s.get(m.ApprovalRequest, approval_id)
            if row is None:
                raise KeyError("Approval request not found")
            data = to_dict(row)
            data["audit_trail"] = [
                {"timestamp": _aware(e.timestamp).isoformat(), "actor": e.actor, "action": e.action,
                 "details": e.details}
                for e in reversed(AuditRepository(s).list(limit=100, entity_id=approval_id))]
            return data

    def list(self, status: str | None = None, record_number: str | None = None, execution_id: str | None = None,
             limit: int = 50, offset: int = 0) -> list[dict]:
        self.expire_stale()
        with session_scope() as s:
            rows = ApprovalRepository(s).list(limit=limit, offset=offset, status=status,
                                               record_number=record_number, workflow_execution_id=execution_id)
            return [to_dict(r) for r in rows]

    def count(self, status: str | None = None) -> int:
        with session_scope() as s:
            return ApprovalRepository(s).count(status=status)
