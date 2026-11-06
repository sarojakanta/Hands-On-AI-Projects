"""Ticket provider abstraction.

All ticket creation/reads go through a TicketProvider so the storage backend
is swappable without touching callers. Implementations:
  * LocalTicketProvider      - SQLite via SQLAlchemy (default; self-contained).
  * SalesforceTicketProvider - creates a Case in a real Salesforce org (opt-in).

Every provider returns the same serialized ticket dict shape so the MCP tools,
agent, and dashboard are provider-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


# Canonical serialized-ticket keys returned by every provider:
#   ticket_id, customer_id, conversation_id, category, priority, status,
#   assigned_agent, escalation_reason, original_request, summary,
#   authentication_status, created_at, updated_at
# Salesforce-backed tickets also include "external_id" (the Case id) when
# available.


class TicketProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def create_ticket(
        self,
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
        """Create a ticket and return its serialized form."""

    @abstractmethod
    def get_ticket(self, ticket_id: str) -> dict | None:
        """Return a serialized ticket by id, or None."""

    @abstractmethod
    def list_tickets(self, status: str | None = None) -> list[dict]:
        """Return serialized tickets, optionally filtered by status."""

    def update_ticket(self, ticket_id: str, **fields) -> dict | None:
        """Update mutable ticket fields. Optional for some providers."""
        raise NotImplementedError
