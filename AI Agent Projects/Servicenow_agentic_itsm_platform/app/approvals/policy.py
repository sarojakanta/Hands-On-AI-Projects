"""Approval policy: which actions exist, who may approve them, and what a valid payload is.

The AI can only *propose* actions from this closed list. Unknown action types are
rejected, so a model can not invent new capabilities (e.g. "run_shell").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import settings
from app.security.rbac import Principal, has_role
from app.security.sanitize import mask_secrets

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_VALUE_LEN = 8000


class ApprovalError(Exception):
    """Base class for approval framework errors."""


class ApprovalValidationError(ApprovalError, ValueError):
    pass


class ApprovalPermissionError(ApprovalError, PermissionError):
    pass


class ApprovalStateError(ApprovalError):
    """The request is not in a state that allows the operation (e.g. already decided)."""


@dataclass(frozen=True)
class ActionRule:
    allowed_keys: frozenset[str]
    required_role: str = "approver"
    executable: bool = True          # False => decision is recorded, nothing is executed automatically
    description: str = ""


RULES: dict[str, ActionRule] = {
    "incident_update": ActionRule(frozenset({"work_notes"}), description="Add AI analysis as a work note"),
    "priority_change": ActionRule(frozenset({"priority"}), description="Change record priority"),
    "assignment_change": ActionRule(frozenset({"assignment_group"}), description="Change assignment"),
    "change_approval": ActionRule(frozenset({"work_notes"}), description="Record a change-approval recommendation"),
    "incident_closure": ActionRule(frozenset({"close_notes", "state"}), description="Close an incident"),
    "remediation": ActionRule(frozenset({"description", "runbook"}), required_role="admin", executable=False,
                              description="Remediation (never executed automatically)"),
}
APPROVAL_REQUIRED = frozenset(RULES)  # every listed action needs a human decision


def clean_value(value) -> str:
    return _CTRL.sub("", mask_secrets(str(value)))[:MAX_VALUE_LEN]


def validate_action(action_type: str, record_type: str, record_number: str, payload: dict) -> dict:
    """Return a sanitised payload or raise ApprovalValidationError."""
    rule = RULES.get(action_type)
    if rule is None:
        raise ApprovalValidationError(f"Unknown action type: {action_type!r}")
    if record_type not in ("incident", "problem", "change", "request"):
        raise ApprovalValidationError(f"Unsupported record type: {record_type!r}")
    if not record_number:
        raise ApprovalValidationError("record_number is required")
    if not isinstance(payload, dict) or not payload:
        raise ApprovalValidationError("payload must be a non-empty object")
    extra = set(payload) - rule.allowed_keys
    if extra:
        raise ApprovalValidationError(f"Field(s) not allowed for {action_type}: {', '.join(sorted(extra))}")
    cleaned = {k: clean_value(v) for k, v in payload.items()}
    if action_type == "priority_change" and cleaned["priority"] not in {"1", "2", "3", "4", "5"}:
        raise ApprovalValidationError("priority must be one of 1-5")
    return cleaned


def self_approval_allowed() -> bool:
    flag = settings.approval_allow_self_approval
    return (not settings.is_production) if flag is None else flag


def check_can_decide(principal: Principal, action_type: str, requested_by: str) -> None:
    rule = RULES[action_type]
    if not has_role(principal, rule.required_role):
        raise ApprovalPermissionError(f"Role '{rule.required_role}' is required to decide {action_type}")
    if principal.username == requested_by and not self_approval_allowed():
        raise ApprovalPermissionError("Separation of duties: the requester can not approve their own request")
