"""Redis-backed memory store.

Used when REDIS_URL is configured. Values are JSON-serialized. TTL is handled
natively by Redis' EX option. The redis client is created lazily so importing
this module never requires a live Redis.
"""

from __future__ import annotations

import json
from typing import Any

from app.memory.base import MemoryStore


class RedisMemoryStore(MemoryStore):
    def __init__(self, url: str) -> None:
        self._url = url
        self._client = None

    def _get_client(self):
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(self._url, decode_responses=True)
        return self._client

    def get(self, key: str) -> Any | None:
        raw = self._get_client().get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        payload = json.dumps(value)
        if ttl_seconds:
            self._get_client().set(key, payload, ex=ttl_seconds)
        else:
            self._get_client().set(key, payload)

    def delete(self, key: str) -> None:
        self._get_client().delete(key)

    def exists(self, key: str) -> bool:
        return bool(self._get_client().exists(key))
