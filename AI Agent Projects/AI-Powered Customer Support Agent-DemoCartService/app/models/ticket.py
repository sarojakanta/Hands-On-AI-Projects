"""Support ticket ORM model.

Tickets are created when the agent escalates to a human. They carry full
conversation context so the human agent does not need the customer to repeat
the issue.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base


TICKET_STATUSES = ("OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED")
TICKET_PRIORITIES = ("LOW", "MEDIUM", "HIGH", "URGENT")


class Ticket(Base):
    __tablename__ = "tickets"

    # Business-facing ticket reference, e.g. "TKT-100001".
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customers.id"), nullable=True, index=True
    )
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    assigned_agent: Mapped[str] = mapped_column(String(80), nullable=True)

    # Escalation context.
    escalation_reason: Mapped[str] = mapped_column(Text, nullable=True)
    original_request: Mapped[str] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    tool_results: Mapped[str] = mapped_column(Text, nullable=True)  # JSON string
    authentication_status: Mapped[str] = mapped_column(String(40), nullable=True)

    # Human-agent workflow fields.
    internal_notes: Mapped[str] = mapped_column(Text, nullable=True)
    resolution: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Ticket {self.id} {self.status}>"
