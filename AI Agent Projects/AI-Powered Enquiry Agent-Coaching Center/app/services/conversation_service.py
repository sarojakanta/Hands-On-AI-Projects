"""Conversation, message and escalation service (tenant-scoped)."""
from __future__ import annotations

import json
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import ChatMessage, Conversation, Escalation
from app.services.exceptions import NotFoundError


def new_session_id() -> str:
    """Generate an opaque, URL-safe conversation session id."""
    return secrets.token_urlsafe(24)


def get_or_create_conversation(
    db: Session, institute_id: int, session_id: str | None
) -> Conversation:
    """Return the conversation for a session id, creating one if needed.

    A session id from another institute is never reused — lookups are scoped
    by institute_id, so a fresh conversation is created instead.
    """
    if session_id:
        convo = db.scalar(
            select(Conversation).where(
                Conversation.session_id == session_id,
                Conversation.institute_id == institute_id,
            )
        )
        if convo:
            return convo

    convo = Conversation(
        institute_id=institute_id,
        session_id=session_id or new_session_id(),
        status="active",
        context_data=json.dumps({}),
    )
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return convo


def get_conversation(
    db: Session, institute_id: int, session_id: str
) -> Conversation:
    convo = db.scalar(
        select(Conversation)
        .where(
            Conversation.session_id == session_id,
            Conversation.institute_id == institute_id,
        )
        .options(selectinload(Conversation.messages))
    )
    if convo is None:
        raise NotFoundError("Conversation not found for this institute.")
    return convo


def add_message(
    db: Session,
    conversation: Conversation,
    role: str,
    content: str,
    meta: dict[str, Any] | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        conversation_id=conversation.id,
        role=role,
        content=content,
        meta=json.dumps(meta) if meta else None,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def get_history(
    db: Session, conversation: Conversation, limit: int = 20
) -> list[ChatMessage]:
    """Return recent messages (oldest-first) for context building."""
    msgs = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        ).all()
    )
    return list(reversed(msgs))


def get_context(db: Session, conversation: Conversation) -> dict[str, Any]:
    """Load the per-session context dict (captured name/phone/etc.)."""
    if not conversation.context_data:
        return {}
    try:
        return json.loads(conversation.context_data)
    except (json.JSONDecodeError, TypeError):
        return {}


def update_context(
    db: Session, conversation: Conversation, updates: dict[str, Any]
) -> dict[str, Any]:
    """Merge updates into the session context and persist."""
    ctx = get_context(db, conversation)
    ctx.update({k: v for k, v in updates.items() if v is not None})
    conversation.context_data = json.dumps(ctx)
    db.commit()
    db.refresh(conversation)
    return ctx


def link_lead(db: Session, conversation: Conversation, lead_id: int) -> None:
    conversation.lead_id = lead_id
    db.commit()


def list_conversations(
    db: Session, institute_id: int, limit: int = 50
) -> list[Conversation]:
    return list(
        db.scalars(
            select(Conversation)
            .where(Conversation.institute_id == institute_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        ).all()
    )


def escalate(
    db: Session, institute_id: int, conversation: Conversation, reason: str
) -> Escalation:
    conversation.status = "escalated"
    esc = Escalation(
        institute_id=institute_id,
        conversation_id=conversation.id,
        reason=reason,
        status="open",
    )
    db.add(esc)
    db.commit()
    db.refresh(esc)
    return esc


def list_escalations(
    db: Session, institute_id: int, status: str | None = None
) -> list[Escalation]:
    stmt = (
        select(Escalation)
        .where(Escalation.institute_id == institute_id)
        .order_by(Escalation.created_at.desc())
    )
    if status:
        stmt = stmt.where(Escalation.status == status)
    return list(db.scalars(stmt).all())
