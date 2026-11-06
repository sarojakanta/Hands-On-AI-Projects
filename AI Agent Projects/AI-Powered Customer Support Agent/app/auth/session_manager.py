"""Verified customer session management.

Tracks which (session_id -> customer_id) pairs have passed OTP verification.
Verification status is isolated per browser session: one customer's verified
state can never be read by another session.

This is the authoritative source of "is this session allowed to see account
data for customer X". MCP order/shipment tools consult this independently on
every call (Phase 3) so authorization is enforced in backend code, not by the
LLM.

Storage: an in-process TTL store for the local dev default. Phase 6 can back
this with the shared memory layer (Redis/local). Session state deliberately
holds only the minimum: customer id, verified-at timestamp, and expiry.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import config


@dataclass
class VerifiedSession:
    session_id: str
    customer_id: str
    verified_at: datetime
    expires_at: datetime

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now <= self.expires_at


class SessionManager:
    """Thread-safe in-process store of verified sessions."""

    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._ttl = ttl_seconds or config.SESSION_TTL_SECONDS
        self._lock = threading.Lock()
        # Key: session_id -> VerifiedSession. A session is verified for exactly
        # one customer at a time.
        self._store: dict[str, VerifiedSession] = {}

    def mark_verified(self, *, session_id: str, customer_id: str) -> VerifiedSession:
        now = datetime.now(timezone.utc)
        record = VerifiedSession(
            session_id=session_id,
            customer_id=customer_id,
            verified_at=now,
            expires_at=now + timedelta(seconds=self._ttl),
        )
        with self._lock:
            self._store[session_id] = record
        return record

    def get(self, session_id: str) -> VerifiedSession | None:
        with self._lock:
            record = self._store.get(session_id)
            if record is None:
                return None
            if not record.is_active():
                # Expired: drop it so stale state is never honoured.
                del self._store[session_id]
                return None
            return record

    def is_verified(self, *, session_id: str, customer_id: str) -> bool:
        """True only if THIS session is verified for THIS exact customer.

        This is the cross-customer isolation guarantee: a session verified for
        customer A returns False when asked about customer B.
        """
        record = self.get(session_id)
        return record is not None and record.customer_id == customer_id

    def verified_customer_id(self, session_id: str) -> str | None:
        record = self.get(session_id)
        return record.customer_id if record else None

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)

    def reset(self) -> None:
        """Clear all sessions. Used by tests."""
        with self._lock:
            self._store.clear()


# Module-level singleton shared across requests.
session_manager = SessionManager()
