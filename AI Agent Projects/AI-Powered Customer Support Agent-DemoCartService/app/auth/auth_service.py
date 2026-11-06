"""Authentication service - orchestrates the OTP challenge lifecycle.

Responsibilities:
  * Identify a customer from an order reference or a customer reference.
  * Create an OTP challenge bound to (session_id, customer_id).
  * Ask the configured OTPProvider to deliver the code to the customer's
    REGISTERED mobile only (never an arbitrary number supplied by the caller).
  * On successful verification, create a verified session.

Privacy rules enforced here:
  * Before verification, no customer PII is returned - only a masked contact
    hint so the user knows which number received the code.
  * The OTP code is exposed to the client ONLY in dev mode, via the provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import config
from app.database.db import get_session
from app.models.customer import Customer
from app.models.order import Order
from app.models.otp_challenge import OTPChallenge
from app.auth.otp_provider import (
    OTPProvider,
    OTPProviderError,
    build_otp_provider,
)
from app.auth.verification_service import (
    RESULT_NO_CHALLENGE,
    RESULT_PROVIDER_ERROR,
    VerificationService,
    VerificationResult,
)
from app.auth.session_manager import session_manager


@dataclass
class InitiationResult:
    success: bool
    message: str
    customer_id: str | None = None
    masked_destination: str | None = None
    channel: str | None = None
    # Dev-only: present only when DEV_MODE is enabled.
    dev_code: str | None = None


class AuthService:
    def __init__(
        self,
        provider: OTPProvider | None = None,
        verification_service: VerificationService | None = None,
    ) -> None:
        # DEV_MODE strictly selects the local mock; otherwise Twilio Verify is
        # required. Explicit injection remains available for isolated tests.
        self._provider = provider or build_otp_provider()
        self._verifier = verification_service or VerificationService()

    # ----- customer identification -----

    def _find_customer(self, session, *, order_ref: str | None, customer_ref: str | None):
        """Resolve a Customer from an order reference or a customer reference.

        Returns the Customer or None. Does not reveal whether a given reference
        exists beyond what the caller is allowed to know.
        """
        if customer_ref:
            customer = session.get(Customer, customer_ref.strip())
            if customer:
                return customer
        if order_ref:
            order = session.get(Order, order_ref.strip())
            if order:
                return session.get(Customer, order.customer_id)
        return None

    # ----- lifecycle -----

    def initiate(
        self,
        *,
        session_id: str,
        order_ref: str | None = None,
        customer_ref: str | None = None,
    ) -> InitiationResult:
        """Create and 'deliver' an OTP challenge for the identified customer."""
        if not session_id:
            return InitiationResult(success=False, message="Missing session.")
        if not (order_ref or customer_ref):
            return InitiationResult(
                success=False,
                message="Please provide an order reference or customer ID.",
            )

        session = get_session()
        try:
            customer = self._find_customer(
                session, order_ref=order_ref, customer_ref=customer_ref
            )

            # T-6: cap OTP requests per customer per hour (abuse prevention).
            if customer is not None:
                from app.services.rate_limiter import rate_limiter

                if not rate_limiter.check_otp_request(customer.id):
                    return InitiationResult(
                        success=False,
                        message=(
                            "You've requested too many codes recently. Please "
                            "wait a little while before trying again."
                        ),
                    )
            # Do NOT disclose whether the reference exists; return a neutral
            # message either way to avoid customer enumeration. We still cannot
            # send a code if there is no registered contact.
            if customer is None:
                return InitiationResult(
                    success=False,
                    message=(
                        "We couldn't match that reference. Please check your "
                        "order ID or customer ID and try again."
                    ),
                )

            code = self._provider.generate_code()
            now = datetime.now(timezone.utc)
            challenge = OTPChallenge(
                session_id=session_id,
                customer_id=customer.id,
                code=code,
                attempts=0,
                max_attempts=config.OTP_MAX_ATTEMPTS,
                verified=False,
                consumed=False,
                created_at=now,
                expires_at=now + timedelta(seconds=config.OTP_TTL_SECONDS),
            )
            session.add(challenge)

            # Deliver only to the REGISTERED mobile. Commit the challenge after
            # the provider accepts the request so failed sends leave no active
            # local challenge behind.
            delivery = self._provider.send(
                code=code,
                destination=customer.mobile,
                expose_dev_code=config.DEV_MODE,
            )
            if not delivery.delivered:
                session.rollback()
                return InitiationResult(
                    success=False,
                    message="Unable to send a verification code right now.",
                )
            session.commit()

            return InitiationResult(
                success=True,
                message="Verification code sent.",
                customer_id=customer.id,
                masked_destination=delivery.masked_destination,
                channel=delivery.channel,
                dev_code=delivery.dev_code,
            )
        except OTPProviderError:
            session.rollback()
            return InitiationResult(
                success=False,
                message="Unable to send a verification code right now.",
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def verify(
        self, *, session_id: str, customer_id: str, code: str
    ) -> VerificationResult:
        """Verify a submitted code and, on success, create a verified session."""
        code_validator = None
        if self._provider.verifies_remotely:
            session = get_session()
            try:
                customer = session.get(Customer, customer_id)
                destination = customer.mobile if customer else None
            finally:
                session.close()
            if destination is None:
                return VerificationResult(
                    success=False,
                    status=RESULT_NO_CHALLENGE,
                    attempts_remaining=0,
                    message="No active verification request. Please request a new code.",
                )
            code_validator = lambda submitted: self._provider.verify_code(
                destination=destination, code=submitted
            )

        try:
            result = self._verifier.verify(
                session_id=session_id,
                customer_id=customer_id,
                code=code,
                code_validator=code_validator,
            )
        except OTPProviderError:
            return VerificationResult(
                success=False,
                status=RESULT_PROVIDER_ERROR,
                attempts_remaining=0,
                message="Unable to verify the code right now. Please try again.",
            )

        if result.success:
            session_manager.mark_verified(
                session_id=session_id, customer_id=customer_id
            )
        return result
