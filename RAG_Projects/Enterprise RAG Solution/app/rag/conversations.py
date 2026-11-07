"""
Conversation memory.

Phase 3 ships an in-memory store behind the ``ConversationStore`` protocol. A SQL-backed
implementation (persistent across restarts) is added with the storage layer; nothing that uses
the protocol changes.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from app.core.errors import NotFoundError

CONVERSATION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
MAX_MESSAGES_PER_CONVERSATION = 200


class ConversationNotFoundError(NotFoundError):
    code = "conversation_not_found"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class ConversationMessage:
    role: str                                   # "user" | "assistant"
    content: str
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)   # e.g. citations of an assistant answer


@dataclass
class Conversation:
    id: str
    created_at: str
    messages: list[ConversationMessage] = field(default_factory=list)


@dataclass
class ConversationSummary:
    """A row in the conversation list: no message bodies, just enough to label and order it."""

    id: str
    created_at: str
    updated_at: str
    message_count: int
    title: str              # first user message, trimmed (empty conversations get a placeholder)


def make_title(text: str, *, limit: int = 80) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "New conversation"
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "\u2026"


class ConversationStore(Protocol):
    def create(self) -> Conversation: ...
    def get(self, conversation_id: str) -> Conversation: ...
    def append(self, conversation_id: str, messages: list[ConversationMessage]) -> None: ...
    def recent(self, conversation_id: str, count: int) -> list[ConversationMessage]: ...
    def delete(self, conversation_id: str) -> bool: ...
    def list(self, limit: int = 100) -> list[ConversationSummary]: ...


class InMemoryConversationStore:
    """Bounded, least-recently-used store. Contents are lost on restart."""

    def __init__(self, max_conversations: int = 200) -> None:
        self._max = max_conversations
        self._items: OrderedDict[str, Conversation] = OrderedDict()
        self._lock = threading.Lock()

    def create(self) -> Conversation:
        conversation = Conversation(id=new_id(), created_at=_now())
        with self._lock:
            self._items[conversation.id] = conversation
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return Conversation(conversation.id, conversation.created_at, [])

    def _require(self, conversation_id: str) -> Conversation:
        conversation = self._items.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError("Conversation not found. It may have expired; start a new one.")
        self._items.move_to_end(conversation_id)
        return conversation

    def get(self, conversation_id: str) -> Conversation:
        with self._lock:
            conversation = self._require(conversation_id)
            return Conversation(conversation.id, conversation.created_at, list(conversation.messages))

    def append(self, conversation_id: str, messages: list[ConversationMessage]) -> None:
        with self._lock:
            conversation = self._require(conversation_id)
            conversation.messages.extend(messages)
            overflow = len(conversation.messages) - MAX_MESSAGES_PER_CONVERSATION
            if overflow > 0:
                del conversation.messages[:overflow]

    def recent(self, conversation_id: str, count: int) -> list[ConversationMessage]:
        with self._lock:
            conversation = self._require(conversation_id)
            return list(conversation.messages[-count:]) if count > 0 else []

    def delete(self, conversation_id: str) -> bool:
        with self._lock:
            return self._items.pop(conversation_id, None) is not None

    def list(self, limit: int = 100) -> list[ConversationSummary]:
        with self._lock:
            summaries = []
            for conversation in self._items.values():
                if not conversation.messages:
                    continue
                first_user = next((m for m in conversation.messages if m.role == "user"), None)
                summaries.append(
                    ConversationSummary(
                        id=conversation.id,
                        created_at=conversation.created_at,
                        updated_at=conversation.created_at,
                        message_count=len(conversation.messages),
                        title=make_title(first_user.content if first_user else ""),
                    )
                )
            return list(reversed(summaries))[:max(1, limit)]
