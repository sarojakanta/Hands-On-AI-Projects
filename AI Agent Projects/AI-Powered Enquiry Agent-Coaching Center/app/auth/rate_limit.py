"""Rate limiting for public endpoints.

Implemented as a lightweight in-process sliding-window limiter exposed as a
FastAPI dependency. A dependency (rather than a decorator) is used because it
composes cleanly with FastAPI's request-body parsing and is trivial to test.

The window is per client IP. The limit string is parsed from
``PUBLIC_CHAT_RATE_LIMIT`` (e.g. "20/minute", "5/second", "100/hour").

For a single-process MVP an in-memory store is sufficient. A multi-process
or multi-instance deployment should back this with Redis; the dependency
interface would stay the same.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.config import get_settings

_settings = get_settings()

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def _unit_seconds(unit: str) -> int:
    unit = unit.strip().lower()
    if unit.endswith("s") and unit[:-1] in _UNITS:
        unit = unit[:-1]
    return _UNITS.get(unit, 60)


class SlidingWindowLimiter:
    """Thread-safe in-memory sliding-window limiter."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.max_requests:
                return False
            dq.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def _build_limiter() -> SlidingWindowLimiter:
    spec = _settings.public_chat_rate_limit or "20/minute"
    try:
        count_s, unit = spec.split("/")
        count = int(count_s)
        window = _unit_seconds(unit)
    except ValueError:
        count, window = 20, 60
    return SlidingWindowLimiter(count, window)


public_chat_limiter = _build_limiter()


def _client_ip(request: Request) -> str:
    # Honour X-Forwarded-For when behind a proxy; fall back to peer address.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_public_chat(request: Request) -> None:
    """FastAPI dependency: enforce the public chat rate limit (429 if over)."""
    key = _client_ip(request)
    if not public_chat_limiter.allow(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down and try again shortly.",
        )
