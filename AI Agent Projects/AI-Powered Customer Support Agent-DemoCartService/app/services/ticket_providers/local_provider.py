"""Local ticket provider: SQLite via SQLAlchemy.

This is the default, self-contained backend. It holds the ticket logic that
previously lived directly in ticket_service.py.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app.config import config
from app.database.db import get_session
from app.models.ticket import Ticket, TICKET_PRIORITIES, TICKET_STATUSES
from app.services.ticket_providers.base import TicketProvider


def _next_ticket_id(session) -> str:
    """Generate a sequential id without reusing ids after dev cleanup."""
    ids = session.query(Ticket.id).all()
    suffixes = [
        int(match.group(1))
        for (ticket_id,) in ids
        if (match := re.fullmatch(r"TKT-(\d+)", ticket_id or ""))
    ]
    return f"TKT-{max(suffixes, default=100000) + 1:06d}"


def _prune_dev_tickets(session) -> None:
    """Keep local dev tickets under the cap, preserving every open ticket."""
    if (
        not config.DEV_MODE
        or config.TICKET_PROVIDER != "local"
        or config.DEV_TICKET_MAX_COUNT <= 0
    ):
        return

    count = session.query(Ticket).count()
    excess = count - config.DEV_TICKET_MAX_COUNT
    if excess <= 0:
        return

    oldest_resolved = (
        session.query(Ticket)
        .filter(Ticket.status.in_(("RESOLVED", "CLOSED")))
        .order_by(Ticket.created_at.asc(), Ticket.id.asc())
        .limit(excess)
        .all()
    )
    for ticket in oldest_resolved:
        session.delete(ticket)


def serialize_ticket(ticket: Ticket) -> dict:
    return {
        "ticket_id": ticket.id,
        "customer_id": ticket.customer_id,
        "conversation_id": ticket.conversation_id,
        "category": ticket.category,
        "priority": ticket.priority,
        "status": ticket.status,
        "assigned_agent": ticket.assigned_agent,
        "escalation_reason": ticket.escalation_reason,
        "original_request": ticket.original_request,
        "summary": ticket.summary,
        "authentication_status": ticket.authentication_status,
        "internal_notes": ticket.internal_notes,
        "resolution": ticket.resolution,
        "tool_results": ticket.tool_results,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
    }


class LocalTicketProvider(TicketProvider):
    name = "local"

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
        priority = priority if priority in TICKET_PRIORITIES else "MEDIUM"
        session = get_session()
        try:
            ticket = Ticket(
                id=_next_ticket_id(session),
                customer_id=customer_id,
                conversation_id=conversation_id,
                category=(category or "GENERAL").strip().upper(),
                priority=priority,
                status="OPEN",
                escalation_reason=escalation_reason,
                original_request=original_request,
                summary=summary,
                tool_results=json.dumps(tool_results) if tool_results is not None else None,
                authentication_status=authentication_status,
                created_at=datetime.now(timezone.utc),
            )
            session.add(ticket)
            session.flush()
            created = serialize_ticket(ticket)
            _prune_dev_tickets(session)
            session.commit()
            return created
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_ticket(self, ticket_id: str) -> dict | None:
        session = get_session()
        try:
            ticket = session.get(Ticket, (ticket_id or "").strip())
            return serialize_ticket(ticket) if ticket else None
        finally:
            session.close()

    def list_tickets(self, status: str | None = None) -> list[dict]:
        session = get_session()
        try:
            q = session.query(Ticket)
            if status and status.upper() in TICKET_STATUSES:
                q = q.filter(Ticket.status == status.upper())
            tickets = q.order_by(Ticket.created_at.desc()).all()
            return [serialize_ticket(t) for t in tickets]
        finally:
            session.close()

    def update_ticket(self, ticket_id: str, **fields) -> dict | None:
        """Update mutable fields: status, assigned_agent, internal_notes,
        resolution, priority. Unknown/unsafe fields are ignored."""
        allowed = {"status", "assigned_agent", "internal_notes", "resolution", "priority"}
        session = get_session()
        try:
            ticket = session.get(Ticket, (ticket_id or "").strip())
            if ticket is None:
                return None
            for key, value in fields.items():
                if key not in allowed or value is None:
                    continue
                if key == "status" and value.upper() not in TICKET_STATUSES:
                    continue
                if key == "priority" and value.upper() not in TICKET_PRIORITIES:
                    continue
                setattr(ticket, key, value.upper() if key in ("status", "priority") else value)
            session.commit()
            return serialize_ticket(ticket)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
