"""OTP verification service.

ALL OTP security decisions live here, in backend code. The LLM never calls
this and never decides whether an OTP is valid. Rules enforced:

  * Expiry:        OTP is invalid after OTP_TTL_SECONDS (default 5 minutes).
  * Attempts:      at most OTP_MAX_ATTEMPTS wrong tries (default 3).
  * Single-use:    once consumed (verified), an OTP cannot be reused.
  * Binding:       a code is only valid for the exact (session_id, customer_id)
                   it was issued to - preventing cross-customer/session leakage.

The service operates on OTPChallenge rows and returns typed results. It never
logs OTP values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.config import config
from app.database.db import get_session
from app.models.otp_challenge import OTPChallenge


# Machine-readable verification outcomes.
RESULT_SUCCESS = "SUCCESS"
RESULT_INVALID_CODE = "INVALID_CODE"
RESULT_EXPIRED = "EXPIRED"
RESULT_TOO_MANY_ATTEMPTS = "TOO_MANY_ATTEMPTS"
RESULT_ALREADY_USED = "ALREADY_USED"
RESULT_NO_CHALLENGE = "NO_CHALLENGE"
RESULT_PROVIDER_ERROR = "PROVIDER_ERROR"


@dataclass
class VerificationResult:
    success: bool
    status: str
    attempts_remaining: int
    message: str


def _as_utc(dt: datetime) -> datetime:
    """Treat naive datetimes (SQLite) as UTC for safe comparison."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class VerificationService:
    def __init__(self, max_attempts: int | None = None) -> None:
        self._max_attempts = max_attempts or config.OTP_MAX_ATTEMPTS

    def _active_challenge(self, session, *, session_id: str, customer_id: str):
        """Return the most recent challenge bound to this session+customer."""
        return (
            session.query(OTPChallenge)
            .filter(
                OTPChallenge.session_id == session_id,
                OTPChallenge.customer_id == customer_id,
            )
            .order_by(OTPChallenge.created_at.desc())
            .first()
        )

    def verify(
        self,
        *,
        session_id: str,
        customer_id: str,
        code: str,
        code_validator: Callable[[str], bool] | None = None,
    ) -> VerificationResult:
        """Verify a code against the active session/customer challenge.

        ``code_validator`` delegates the code check to a managed provider such
        as Twilio Verify. If omitted, the locally stored development code is
        checked. Expiry, attempt limits, binding, and single-use remain local.
        """
        session = get_session()
        try:
            challenge = self._active_challenge(
                session, session_id=session_id, customer_id=customer_id
            )

            # Binding: no challenge for this exact session+customer pair.
            if challenge is None:
                return VerificationResult(
                    success=False,
                    status=RESULT_NO_CHALLENGE,
                    attempts_remaining=0,
                    message="No active verification request. Please request a new code.",
                )

            # Single-use: a consumed/verified challenge cannot be reused.
            if challenge.consumed or challenge.verified:
                return VerificationResult(
                    success=False,
                    status=RESULT_ALREADY_USED,
                    attempts_remaining=0,
                    message="This code has already been used. Please request a new code.",
                )

            # Expiry.
            now = datetime.now(timezone.utc)
            if now > _as_utc(challenge.expires_at):
                return VerificationResult(
                    success=False,
                    status=RESULT_EXPIRED,
                    attempts_remaining=0,
                    message="This code has expired. Please request a new code.",
                )

            # Attempts exhausted before this try.
            if challenge.attempts >= challenge.max_attempts:
                return VerificationResult(
                    success=False,
                    status=RESULT_TOO_MANY_ATTEMPTS,
                    attempts_remaining=0,
                    message="Too many incorrect attempts. Please request a new code.",
                )

            # Compare locally in development, or ask the managed provider.
            # Record the attempt regardless of a valid/invalid outcome.
            submitted = (code or "").strip()
            valid = (
                code_validator(submitted)
                if code_validator is not None
                else submitted == challenge.code
            )
            if not valid:
                challenge.attempts += 1
                remaining = max(challenge.max_attempts - challenge.attempts, 0)
                # If this wrong attempt exhausts the limit, mark consumed so the
                # code cannot be retried even with the right value later.
                if remaining == 0:
                    challenge.consumed = True
                session.commit()
                status = RESULT_TOO_MANY_ATTEMPTS if remaining == 0 else RESULT_INVALID_CODE
                msg = (
                    "Too many incorrect attempts. Please request a new code."
                    if remaining == 0
                    else f"Incorrect code. {remaining} attempt(s) remaining."
                )
                return VerificationResult(
                    success=False,
                    status=status,
                    attempts_remaining=remaining,
                    message=msg,
                )

            # Success: mark verified and single-use consumed.
            challenge.attempts += 1
            challenge.verified = True
            challenge.consumed = True
            session.commit()
            return VerificationResult(
                success=True,
                status=RESULT_SUCCESS,
                attempts_remaining=max(challenge.max_attempts - challenge.attempts, 0),
                message="Verification successful.",
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
