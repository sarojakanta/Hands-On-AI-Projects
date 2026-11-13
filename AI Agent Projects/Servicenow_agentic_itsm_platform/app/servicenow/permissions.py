"""Read/write separation and approval enforcement for ServiceNow writes."""
from __future__ import annotations

from dataclasses import dataclass, field


class WriteNotAllowedError(PermissionError):
    """Raised when a write is attempted without valid human approval or outside the allow-list."""


@dataclass(frozen=True)
class ApprovalGrant:
    """Proof of human approval. Only the approval service should construct one.

    The client writes *exactly* ``payload`` to *exactly* this record, so approved
    content can not be altered or redirected after the decision.
    """

    approval_id: str
    approver: str
    action_type: str
    record_type: str
    record_number: str
    payload: dict = field(default_factory=dict, hash=False, compare=False)
    status: str = "approved"
