"""Conversation, ChatMessage and Escalation models."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.institute import Institute
    from app.models.lead import StudentLead


class Conversation(Base, TimestampMixin):
    """A chat session between a prospective student and the AI agent."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    institute_id: Mapped[int] = mapped_column(
        ForeignKey("institutes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Public opaque identifier used by the widget (not the primary key).
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_leads.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # active / escalated / closed
    status: Mapped[str] = mapped_column(String(40), default="active", index=True, nullable=False)
    # Lightweight per-session memory (JSON string): captured name/phone/etc.
    context_data: Mapped[str | None] = mapped_column(Text, nullable=True)

    institute: Mapped["Institute"] = relationship(back_populates="conversations")
    lead: Mapped["StudentLead | None"] = relationship(back_populates="conversations")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )
    escalations: Mapped[list["Escalation"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Conversation id={self.id} session_id={self.session_id!r}>"


class ChatMessage(Base, TimestampMixin):
    """A single message in a conversation."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # user / assistant / system
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional diagnostic metadata (intent, tool used, sources) as JSON string.
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<ChatMessage id={self.id} role={self.role!r}>"


class Escalation(Base, TimestampMixin):
    """A request to hand a conversation to a human counsellor."""

    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    institute_id: Mapped[int] = mapped_column(
        ForeignKey("institutes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # open / handled
    status: Mapped[str] = mapped_column(String(40), default="open", index=True, nullable=False)

    conversation: Mapped["Conversation"] = relationship(back_populates="escalations")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Escalation id={self.id} status={self.status!r}>"
