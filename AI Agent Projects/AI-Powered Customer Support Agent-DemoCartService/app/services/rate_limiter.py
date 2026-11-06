"""In-process rate limiting (T-6).

Two limits:
  * tool calls per conversation (default 8) - prevents runaway tool loops,
  * OTP requests per customer per hour (default 5) - prevents OTP abuse.

This is a simple thread-safe in-memory limiter suitable for the demo. A
production deployment would back it with Redis so limits hold across processes.
"""

from __future__ import annotations

import threading
import time

from app.config import config


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # conversation_id -> running tool-call count (resets per process life).
        self._tool_calls: dict[str, int] = {}
        # customer_id -> list of OTP request timestamps (epoch seconds).
        self._otp_requests: dict[str, list[float]] = {}

    # ---- tool calls per conversation ----

    def check_tool_call(self, conversation_id: str | None) -> bool:
        """Return True if another tool call is allowed for this conversation."""
        if not conversation_id:
            return True
        limit = config.MAX_TOOL_CALLS_PER_CONVERSATION
        with self._lock:
            used = self._tool_calls.get(conversation_id, 0)
            if used >= limit:
                return False
            self._tool_calls[conversation_id] = used + 1
            return True

    def tool_calls_used(self, conversation_id: str) -> int:
        with self._lock:
            return self._tool_calls.get(conversation_id, 0)

    # ---- OTP requests per customer per hour ----

    def check_otp_request(self, customer_id: str | None) -> bool:
        """Return True if another OTP request is allowed for this customer."""
        if not customer_id:
            return True
        limit = config.MAX_OTP_REQUESTS_PER_HOUR
        now = time.time()
        window_start = now - 3600
        with self._lock:
            times = [t for t in self._otp_requests.get(customer_id, []) if t >= window_start]
            if len(times) >= limit:
                self._otp_requests[customer_id] = times
                return False
            times.append(now)
            self._otp_requests[customer_id] = times
            return True

    def reset(self) -> None:
        """Clear all counters (used by tests)."""
        with self._lock:
            self._tool_calls.clear()
            self._otp_requests.clear()


# Convenience singleton.
rate_limiter = RateLimiter()
