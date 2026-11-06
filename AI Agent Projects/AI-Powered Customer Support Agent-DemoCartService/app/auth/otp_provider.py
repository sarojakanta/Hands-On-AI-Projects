"""OTP delivery providers.

Development mode uses a fixed local code and never makes a network request.
Outside development mode, Twilio Verify generates, sends, and validates the
code; the application stores only challenge/session metadata.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.config import config


@dataclass
class OTPDeliveryResult:
    """Outcome of an OTP delivery attempt.

    Attributes:
        delivered: whether the provider accepted the send request.
        channel: delivery channel label, e.g. "mock-sms" or "sms".
        masked_destination: masked contact the OTP was sent to.
        dev_code: the OTP code, exposed ONLY in development. ``None`` in
            production so codes never leak to the client.
    """

    delivered: bool
    channel: str
    masked_destination: str
    dev_code: str | None = None


class OTPProviderError(RuntimeError):
    """A safe, non-secret-bearing OTP provider failure."""


class OTPProvider(ABC):
    """Abstract OTP delivery provider."""

    channel: str = "abstract"
    verifies_remotely: bool = False

    @abstractmethod
    def generate_code(self) -> str:
        """Return a local code, or an empty string for provider-managed OTPs."""

    @abstractmethod
    def send(self, *, code: str, destination: str, expose_dev_code: bool) -> OTPDeliveryResult:
        """Start verification for the customer's registered destination."""

    def verify_code(self, *, destination: str, code: str) -> bool:
        """Validate a provider-managed code. Local providers do not use this."""
        raise NotImplementedError


def _mask_destination(destination: str) -> str:
    """Mask a mobile number for safe display: keep last 4 digits only."""
    digits = "".join(ch for ch in destination if ch.isdigit())
    if len(digits) <= 4:
        return "XXXX"
    return "XXXXXX" + digits[-4:]


class TwilioVerifyProvider(OTPProvider):
    """Production OTP provider backed by Twilio Verify."""

    channel = "sms"
    verifies_remotely = True

    def __init__(
        self,
        *,
        account_sid: str,
        api_key_sid: str,
        api_key_secret: str,
        verify_service_sid: str,
    ) -> None:
        missing = [
            name
            for name, value in (
                ("TWILIO_ACCOUNT_SID", account_sid),
                ("TWILIO_API_KEY_SID", api_key_sid),
                ("TWILIO_API_KEY_SECRET", api_key_secret),
                ("TWILIO_VERIFY_SERVICE_SID", verify_service_sid),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Twilio Verify is required when DEV_MODE=false; missing "
                + ", ".join(missing)
            )

        try:
            from twilio.rest import Client
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise RuntimeError(
                "The 'twilio' package is required when DEV_MODE=false."
            ) from exc

        self._client = Client(api_key_sid, api_key_secret, account_sid)
        self._service_sid = verify_service_sid

    def generate_code(self) -> str:
        # Twilio generates and stores the OTP; never persist it locally.
        return ""

    def send(self, *, code: str, destination: str, expose_dev_code: bool) -> OTPDeliveryResult:
        try:
            verification = (
                self._client.verify.v2.services(self._service_sid)
                .verifications.create(to=destination, channel="sms")
            )
        except Exception as exc:
            raise OTPProviderError("Unable to send a verification code.") from exc

        return OTPDeliveryResult(
            delivered=verification.status == "pending",
            channel=self.channel,
            masked_destination=_mask_destination(destination),
            dev_code=None,
        )

    def verify_code(self, *, destination: str, code: str) -> bool:
        try:
            check = (
                self._client.verify.v2.services(self._service_sid)
                .verification_checks.create(to=destination, code=code)
            )
        except Exception as exc:
            raise OTPProviderError("Unable to verify the code right now.") from exc
        return check.status == "approved"


class MockOTPProvider(OTPProvider):
    """Development OTP provider.

    Uses a fixed, hardcoded development OTP. Does not integrate with any real
    SMS gateway. Never sends anything over the network. This is intentional for
    the demo; swap in a TwilioOTPProvider later implementing the same interface.
    """

    channel = "mock-sms"

    def __init__(self, dev_otp: str) -> None:
        self._dev_otp = dev_otp

    def generate_code(self) -> str:
        # Hardcoded development OTP. A real provider would use secrets.
        return self._dev_otp

    def send(self, *, code: str, destination: str, expose_dev_code: bool) -> OTPDeliveryResult:
        # No network call. We "deliver" by optionally surfacing the code to the
        # dev UI. The code value is never logged.
        return OTPDeliveryResult(
            delivered=True,
            channel=self.channel,
            masked_destination=_mask_destination(destination),
            dev_code=code if expose_dev_code else None,
        )


def build_otp_provider() -> OTPProvider:
    """Select the provider strictly from DEV_MODE.

    Development always bypasses Twilio. Non-development always requires
    Twilio Verify and fails fast when its configuration is incomplete.
    """
    if config.DEV_MODE:
        return MockOTPProvider(dev_otp=config.DEV_OTP)
    return TwilioVerifyProvider(
        account_sid=config.TWILIO_ACCOUNT_SID,
        api_key_sid=config.TWILIO_API_KEY_SID,
        api_key_secret=config.TWILIO_API_KEY_SECRET,
        verify_service_sid=config.TWILIO_VERIFY_SERVICE_SID,
    )
