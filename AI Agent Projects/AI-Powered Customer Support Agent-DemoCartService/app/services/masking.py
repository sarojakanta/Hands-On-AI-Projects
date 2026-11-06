"""Shared data-minimisation / masking helpers (T-4).

Centralises how sensitive values are masked so tools and audit logging behave
consistently:
  * card numbers -> fully masked except last 4 are removed entirely,
  * phone numbers -> keep only the last 2 digits,
  * arbitrary argument dicts -> drop/replace secret keys.
"""

from __future__ import annotations

import re

_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,16}\b")

# Keys whose values must never appear in logs/responses.
_SECRET_KEYS = {"code", "otp", "password", "api_key", "secret", "security_token"}
# Keys that are PII and should be masked (not dropped) in logs.
_PII_KEYS = {"mobile", "phone", "new_address"}


def mask_phone(value: str | None) -> str:
    """Mask a phone number, keeping only the last 2 digits (T-4)."""
    if not value:
        return ""
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) <= 2:
        return "XX"
    return "X" * (len(digits) - 2) + digits[-2:]


def mask_card(text: str | None) -> str:
    """Replace any card-like number in a string with a masked token."""
    if not text:
        return text or ""
    return _CARD_RE.sub("[CARD-MASKED]", text)


def mask_arguments(args: dict | None) -> dict:
    """Return a copy of tool arguments safe for logging (T-5).

    Secret keys are replaced with [REDACTED]; PII keys are masked; everything
    else is passed through (session/customer/order ids are not secret).
    """
    if not isinstance(args, dict):
        return {}
    out: dict = {}
    for key, value in args.items():
        lkey = key.lower()
        if lkey in _SECRET_KEYS:
            out[key] = "[REDACTED]"
        elif lkey in {"mobile", "phone"}:
            out[key] = mask_phone(str(value))
        elif lkey == "new_address":
            out[key] = "[ADDRESS]"
        elif isinstance(value, str):
            out[key] = mask_card(value)
        else:
            out[key] = value
    return out
