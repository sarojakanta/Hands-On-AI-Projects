"""Support ticket business logic.

Thin facade over the configured TicketProvider (local SQLite by default, or
Salesforce when TICKET_PROVIDER=salesforce). The public functions keep the same
signatures as before so the MCP tools, agent, and dashboard are unaffected by
which backend is active.
"""

from __future__ import annotations

from app.services.ticket_providers.factory import ticket_provider


def create_ticket(
    *,
    category: str,
    customer_id: str | None = None,
    conversation_id: str | None = None,
    priority: str = "MEDIUM",
    escalation_reason: str | None = None,
    original_request: str | None = None,
    summary: str | None = None,
    tool_results: dict | list | None = None,
    authentication_status: str | None = None,
) -> dict:
    """Create a support ticket via the configured provider."""
    return ticket_provider.create_ticket(
        category=category,
        customer_id=customer_id,
        conversation_id=conversation_id,
        priority=priority,
        escalation_reason=escalation_reason,
        original_request=original_request,
        summary=summary,
        tool_results=tool_results,
        authentication_status=authentication_status,
    )


def get_ticket(ticket_id: str) -> dict | None:
    return ticket_provider.get_ticket(ticket_id)


def list_tickets(status: str | None = None) -> list[dict]:
    return ticket_provider.list_tickets(status)


def update_ticket(ticket_id: str, **fields) -> dict | None:
    """Update mutable ticket fields (used by the human dashboard, Phase 7)."""
    return ticket_provider.update_ticket(ticket_id, **fields)


# --- Idempotency (T-7) ---------------------------------------------------

# In-process store mapping an idempotency key -> previously created ticket.
# A repeated write with the same key returns the original result instead of
# creating a duplicate. (A production system would persist this.)
_IDEMPOTENCY: dict[str, dict] = {}


def create_ticket_idempotent(*, idempotency_key: str | None, **kwargs) -> dict:
    """Create a ticket, deduplicating on ``idempotency_key`` when provided."""
    if idempotency_key:
        existing = _IDEMPOTENCY.get(idempotency_key)
        if existing is not None:
            return existing
    ticket = create_ticket(**kwargs)
    if idempotency_key:
        _IDEMPOTENCY[idempotency_key] = ticket
    return ticket


def reset_idempotency() -> None:
    """Clear the idempotency store (used by tests)."""
    _IDEMPOTENCY.clear()
