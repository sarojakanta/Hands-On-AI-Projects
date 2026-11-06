"""In-process memory store with per-key TTL.

The zero-dependency default used when REDIS_URL is not configured. Thread-safe.
Values are stored as-is (deep-copied on read is unnecessary because callers
replace the whole value), with an absolute expiry timestamp per key.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.memory.base import MemoryStore


class LocalMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> (value, expires_at_epoch | None)
        self._data: dict[str, tuple[Any, float | None]] = {}

    def _is_expired(self, expires_at: float | None) -> bool:
        return expires_at is not None and time.time() > expires_at

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if self._is_expired(expires_at):
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds else None
        with self._lock:
            self._data[key] = (value, expires_at)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def clear(self) -> None:
        """Remove everything (used by tests)."""
        with self._lock:
            self._data.clear()
