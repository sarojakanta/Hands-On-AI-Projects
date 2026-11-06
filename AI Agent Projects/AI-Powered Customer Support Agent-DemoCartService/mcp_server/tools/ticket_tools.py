"""Ticket MCP tools: create_support_ticket, escalate_to_human.

Both create a ticket. escalate_to_human is a specialized create that always
marks the ticket HIGH priority and tags it as an escalation, carrying the full
conversation context so a human can take over without the customer repeating
themselves.

T-4: the tool RESPONSE returns a minimal ticket view (internal notes,
resolution, and raw tool_results are omitted - those are for the dashboard).
T-7: both tools accept an optional idempotency_key so a repeated call cannot
create a duplicate ticket.
"""

from __future__ import annotations

from app.services import ticket_service
from mcp_server.tools.base import ok, err, require_string


CREATE_SUPPORT_TICKET_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "description": "Issue category."},
        "customer_id": {"type": "string"},
        "conversation_id": {"type": "string"},
        "priority": {
            "type": "string",
            "enum": ["LOW", "MEDIUM", "HIGH", "URGENT"],
        },
        "original_request": {"type": "string"},
        "summary": {"type": "string"},
        "authentication_status": {"type": "string"},
        "idempotency_key": {"type": "string", "description": "Dedup key for safe retries."},
    },
    "required": ["category"],
    "additionalProperties": False,
}

ESCALATE_TO_HUMAN_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "customer_id": {"type": "string"},
        "conversation_id": {"type": "string"},
        "escalation_reason": {"type": "string", "description": "Why this needs a human."},
        "original_request": {"type": "string"},
        "summary": {"type": "string", "description": "Conversation summary for handoff."},
        "tool_results": {
            "type": ["object", "array", "null"],
            "description": "Relevant tool outputs to include for the human agent.",
        },
        "authentication_status": {"type": "string"},
        "idempotency_key": {"type": "string", "description": "Dedup key for safe retries."},
    },
    "required": ["category", "escalation_reason"],
    "additionalProperties": False,
}


# Fields returned to the agent/customer. Internal-only fields are omitted (T-4).
_MINIMAL_FIELDS = (
    "ticket_id",
    "category",
    "priority",
    "status",
    "conversation_id",
    "created_at",
)


def _minimal(ticket: dict) -> dict:
    return {k: ticket.get(k) for k in _MINIMAL_FIELDS}


def create_support_ticket(args: dict) -> dict:
    category, e = require_string(args, "category")
    if e:
        return e

    ticket = ticket_service.create_ticket_idempotent(
        idempotency_key=_opt_str(args, "idempotency_key"),
        category=category,
        customer_id=_opt_str(args, "customer_id"),
        conversation_id=_opt_str(args, "conversation_id"),
        priority=args.get("priority", "MEDIUM"),
        original_request=_opt_str(args, "original_request"),
        summary=_opt_str(args, "summary"),
        authentication_status=_opt_str(args, "authentication_status"),
    )
    return ok(data=_minimal(ticket), message=f"Support ticket {ticket['ticket_id']} created.")


def escalate_to_human(args: dict) -> dict:
    category, e = require_string(args, "category")
    if e:
        return e
    reason, e = require_string(args, "escalation_reason")
    if e:
        return e

    ticket = ticket_service.create_ticket_idempotent(
        idempotency_key=_opt_str(args, "idempotency_key"),
        category=category,
        customer_id=_opt_str(args, "customer_id"),
        conversation_id=_opt_str(args, "conversation_id"),
        priority="HIGH",
        escalation_reason=reason,
        original_request=_opt_str(args, "original_request"),
        summary=_opt_str(args, "summary"),
        tool_results=args.get("tool_results"),
        authentication_status=_opt_str(args, "authentication_status"),
    )
    return ok(
        data=_minimal(ticket),
        message=f"Escalated to a human. Ticket {ticket['ticket_id']} created.",
    )


def _opt_str(args: dict, key: str) -> str | None:
    value = args.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
