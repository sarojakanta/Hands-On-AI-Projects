"""Tool-side authorization enforced in backend code.

CRITICAL: the MCP server can run as a separate process from Flask, so it cannot
rely on the in-process SessionManager. Instead, authorization is validated
against the SHARED, authoritative source both processes see: the persisted
OTPChallenge records in the database.

An account-scoped tool (get_order, get_shipment_status) is authorized only
when there exists a challenge that is:
  * bound to the exact (session_id, customer_id) pair,
  * verified == True and consumed == True (a real successful verification),
  * not older than the session TTL.

This guarantees:
  * the LLM can never "decide" a caller is authorized - it only passes a
    session_id, and the backend checks the DB,
  * a session verified for customer A cannot read customer B's data,
  * expired verifications stop working.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import config
from app.database.db import get_session
from app.models.otp_challenge import OTPChallenge


@dataclass
class AuthorizationResult:
    authorized: bool
    reason: str


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def is_session_verified_for(session_id: str, customer_id: str) -> bool:
    """Return True if the (session, customer) pair has a live verified OTP."""
    if not session_id or not customer_id:
        return False

    session = get_session()
    try:
        challenge = (
            session.query(OTPChallenge)
            .filter(
                OTPChallenge.session_id == session_id,
                OTPChallenge.customer_id == customer_id,
                OTPChallenge.verified.is_(True),
                OTPChallenge.consumed.is_(True),
            )
            .order_by(OTPChallenge.created_at.desc())
            .first()
        )
        if challenge is None:
            return False

        # Honour the session TTL from the moment of verification.
        verified_at = _as_utc(challenge.created_at)
        if datetime.now(timezone.utc) > verified_at + timedelta(
            seconds=config.SESSION_TTL_SECONDS
        ):
            return False
        return True
    finally:
        session.close()


def authorize_account_access(
    *, session_id: str, customer_id: str
) -> AuthorizationResult:
    """Authorize access to account-scoped data for a specific customer."""
    if not session_id:
        return AuthorizationResult(False, "missing_session")
    if not customer_id:
        return AuthorizationResult(False, "missing_customer")
    if not is_session_verified_for(session_id, customer_id):
        return AuthorizationResult(False, "not_verified")
    return AuthorizationResult(True, "ok")
