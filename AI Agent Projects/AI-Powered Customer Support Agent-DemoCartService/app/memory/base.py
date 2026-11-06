"""Key-value memory store interface.

Conversation memory is kept separate from the RAG knowledge base. The store is
a simple TTL key-value abstraction with two implementations:
  * LocalMemoryStore - in-process dict with per-key expiry (zero dependencies).
  * RedisMemoryStore - backed by Redis when REDIS_URL is configured.

The store holds opaque JSON-serializable values; the ConversationMemory model
(memory/conversation_memory.py) layers the conversation schema on top. Keys are
namespaced per conversation so sessions are isolated.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MemoryStore(ABC):
    @abstractmethod
    def get(self, key: str) -> Any | None:
        """Return the stored value for ``key`` or None if missing/expired."""

    @abstractmethod
    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Store ``value`` under ``key`` with an optional TTL."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove ``key`` if present."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if ``key`` is present and not expired."""
