"""
In-memory sliding-window rate limiter (per client address and bucket).

Scope: protects the expensive endpoints (LLM calls and writes) from accidental loops and a single noisy
client. It is per process and keyed by network address, so it is NOT a defence against a distributed
attack and is not shared between several server processes. Reads (document list, status, static files,
health, metrics) are never limited, because the web UI polls them.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from collections.abc import Callable

from app.core.observability import Metrics, client_address

MAX_TRACKED_KEYS = 10_000
WINDOW_SECONDS = 60.0

LLM_PATHS = ("/api/v1/chat", "/api/v1/llm/complete", "/api/query")


def classify(method: str, path: str) -> str | None:
    """Return the bucket a request counts against, or ``None`` when it is not limited."""
    if method == "OPTIONS":
        return None
    if path in LLM_PATHS or (path.startswith("/api/v1/llm/providers/") and path.endswith("/test")):
        return "llm"
    if method in {"POST", "PUT", "PATCH", "DELETE"} and (path.startswith("/api/v1/") or path.startswith("/api/")):
        return "write"
    return None


class SlidingWindowLimiter:
    def __init__(self, limits: dict[str, int], *, clock: Callable[[], float] = time.monotonic) -> None:
        self.limits = limits
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str, bucket: str) -> tuple[bool, int, int]:
        """Record a request. Returns ``(allowed, remaining, retry_after_seconds)``."""
        limit = self.limits.get(bucket, 0)
        if limit <= 0:
            return True, 0, 0
        now = self._clock()
        with self._lock:
            key = (client, bucket)
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= MAX_TRACKED_KEYS:
                    self._evict(now)
                hits = self._hits[key] = deque()
            while hits and now - hits[0] >= WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                return False, 0, max(1, math.ceil(WINDOW_SECONDS - (now - hits[0])))
            hits.append(now)
            return True, limit - len(hits), 0

    def _evict(self, now: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] >= WINDOW_SECONDS]:
            del self._hits[key]
        while len(self._hits) >= MAX_TRACKED_KEYS:      # still full: drop the oldest entries
            self._hits.pop(next(iter(self._hits)))


class RateLimitMiddleware:
    def __init__(self, app, limiter: SlidingWindowLimiter, metrics: Metrics | None = None, *, trust_proxy: bool = False) -> None:
        self.app = app
        self.limiter = limiter
        self.metrics = metrics
        self.trust_proxy = trust_proxy

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        bucket = classify(scope.get("method", "GET"), scope.get("path", ""))
        if bucket is None:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        client = client_address(scope, headers, self.trust_proxy)
        allowed, remaining, retry_after = self.limiter.check(client, bucket)
        limit = self.limiter.limits.get(bucket, 0)

        if not allowed:
            if self.metrics is not None:
                self.metrics.inc("http_rate_limited_total", bucket)
            message = f"Too many requests. Try again in {retry_after} seconds."
            # "detail" keeps the original web UI's error display working; "error" is the API envelope.
            body = json.dumps(
                {"detail": message, "error": {"code": "rate_limited", "message": message, "details": {"retry_after": retry_after}}}
            ).encode()
            await send({
                "type": "http.response.start", "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", str(retry_after).encode()),
                    (b"x-ratelimit-limit", str(limit).encode()),
                    (b"x-ratelimit-remaining", b"0"),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-ratelimit-limit", str(limit).encode()),
                    (b"x-ratelimit-remaining", str(remaining).encode()),
                ]
            await send(message)

        await self.app(scope, receive, send_wrapper)
