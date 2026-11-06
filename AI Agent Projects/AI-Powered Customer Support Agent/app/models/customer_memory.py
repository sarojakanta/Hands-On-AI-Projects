"""Per-customer episodic + preference memory models.

Episodic (CustomerIssue): one-paragraph summary of each past issue and its
outcome, retained 12 months then purged (M-1/M-4). Preference
(CustomerPreference): preferred language + contact channel, kept until the
customer deletes it. Both are keyed by customer_id (M-3) and stored in the
relational DB (SQLite here; Postgres in production).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import String, Text, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base


EPISODIC_RETENTION_DAYS = 365  # 12 months (M-4)


def _retain_until() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=EPISODIC_RETENTION_DAYS)


class CustomerIssue(Base):
    __tablename__ = "customer_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customers.id"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(40), nullable=True)
    # One-paragraph summary + outcome. NO raw chat text / PII (M-1).
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=True)  # RESOLVED/ESCALATED/...
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    retain_until: Mapped[datetime] = mapped_column(DateTime, default=_retain_until)


class CustomerPreference(Base):
    __tablename__ = "customer_preferences"

    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customers.id"), primary_key=True
    )
    preferred_language: Mapped[str] = mapped_column(String(8), nullable=True)
    contact_channel: Mapped[str] = mapped_column(String(20), nullable=True)  # email/sms/whatsapp
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
