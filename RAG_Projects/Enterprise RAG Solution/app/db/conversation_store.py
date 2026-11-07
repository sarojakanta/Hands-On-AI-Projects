"""SQL-backed conversation memory: survives restarts. Implements the ``ConversationStore`` protocol."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import Conversation as ConversationRow
from app.db.models import Message as MessageRow
from app.db.models import iso, utcnow
from app.db.session import Database
from app.rag.conversations import (
    MAX_MESSAGES_PER_CONVERSATION,
    Conversation,
    ConversationMessage,
    ConversationNotFoundError,
    ConversationSummary,
    make_title,
)


def _to_domain(row: MessageRow) -> ConversationMessage:
    metadata: dict[str, Any] = {}
    if row.meta_json:
        try:
            loaded = json.loads(row.meta_json)
            metadata = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            metadata = {}
    return ConversationMessage(
        role=row.role,
        content=row.content,
        id=row.id,
        created_at=iso(row.created_at) or "",
        metadata=metadata,
    )


class SqlConversationStore:
    def __init__(self, db: Database, max_conversations: int = 200) -> None:
        self._db = db
        self._max = max_conversations

    # ---- helpers -------------------------------------------------------------

    @staticmethod
    def _require(session: Session, conversation_id: str) -> ConversationRow:
        row = session.get(ConversationRow, conversation_id)
        if row is None:
            raise ConversationNotFoundError("Conversation not found. It may have expired; start a new one.")
        row.updated_at = utcnow()   # recently used conversations are evicted last
        return row

    def _evict_old(self, session: Session) -> None:
        total = session.scalar(select(func.count()).select_from(ConversationRow)) or 0
        overflow = total - self._max
        if overflow <= 0:
            return
        oldest = list(
            session.scalars(select(ConversationRow.id).order_by(ConversationRow.updated_at.asc()).limit(overflow))
        )
        session.execute(delete(MessageRow).where(MessageRow.conversation_id.in_(oldest)))
        session.execute(delete(ConversationRow).where(ConversationRow.id.in_(oldest)))

    # ---- ConversationStore protocol -------------------------------------------

    def create(self) -> Conversation:
        with self._db.session() as session:
            row = ConversationRow()
            session.add(row)
            session.flush()
            self._evict_old(session)
            return Conversation(id=row.id, created_at=iso(row.created_at) or "", messages=[])

    def get(self, conversation_id: str) -> Conversation:
        with self._db.session() as session:
            row = self._require(session, conversation_id)
            messages = session.scalars(
                select(MessageRow).where(MessageRow.conversation_id == conversation_id).order_by(MessageRow.sequence)
            )
            return Conversation(
                id=row.id, created_at=iso(row.created_at) or "", messages=[_to_domain(m) for m in messages]
            )

    def append(self, conversation_id: str, messages: list[ConversationMessage]) -> None:
        if not messages:
            return
        with self._db.session() as session:
            self._require(session, conversation_id)
            last = session.scalar(
                select(func.max(MessageRow.sequence)).where(MessageRow.conversation_id == conversation_id)
            )
            sequence = (-1 if last is None else last) + 1
            for message in messages:
                session.add(
                    MessageRow(
                        id=message.id,
                        conversation_id=conversation_id,
                        sequence=sequence,
                        role=message.role,
                        content=message.content,
                        meta_json=json.dumps(message.metadata, ensure_ascii=False) if message.metadata else None,
                    )
                )
                sequence += 1
            session.flush()
            newest = sequence - 1
            session.execute(
                delete(MessageRow).where(
                    MessageRow.conversation_id == conversation_id,
                    MessageRow.sequence <= newest - MAX_MESSAGES_PER_CONVERSATION,
                )
            )

    def recent(self, conversation_id: str, count: int) -> list[ConversationMessage]:
        with self._db.session() as session:
            self._require(session, conversation_id)
            if count <= 0:
                return []
            rows = session.scalars(
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id)
                .order_by(MessageRow.sequence.desc())
                .limit(count)
            )
            return [_to_domain(r) for r in reversed(list(rows))]

    def delete(self, conversation_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(ConversationRow, conversation_id)
            if row is None:
                return False
            session.execute(delete(MessageRow).where(MessageRow.conversation_id == conversation_id))
            session.delete(row)
            return True

    def list(self, limit: int = 100) -> list[ConversationSummary]:
        """Most-recently-used first. Conversations with no messages yet are skipped."""
        with self._db.session() as session:
            counts = (
                select(MessageRow.conversation_id, func.count().label("n"))
                .group_by(MessageRow.conversation_id)
                .subquery()
            )
            first_user = (
                select(MessageRow.conversation_id, func.min(MessageRow.sequence).label("seq"))
                .where(MessageRow.role == "user")
                .group_by(MessageRow.conversation_id)
                .subquery()
            )
            rows = session.execute(
                select(
                    ConversationRow.id,
                    ConversationRow.created_at,
                    ConversationRow.updated_at,
                    counts.c.n,
                    MessageRow.content,
                )
                .join(counts, counts.c.conversation_id == ConversationRow.id)
                .outerjoin(first_user, first_user.c.conversation_id == ConversationRow.id)
                .outerjoin(
                    MessageRow,
                    (MessageRow.conversation_id == ConversationRow.id)
                    & (MessageRow.sequence == first_user.c.seq),
                )
                .order_by(ConversationRow.updated_at.desc())
                .limit(max(1, limit))
            ).all()
        return [
            ConversationSummary(
                id=cid,
                created_at=iso(created) or "",
                updated_at=iso(updated) or "",
                message_count=int(n or 0),
                title=make_title(content or ""),
            )
            for cid, created, updated, n, content in rows
        ]
