"""Input guardrails.

Deterministic, code-first checks run on the raw customer message BEFORE the LLM
sees it. These do not rely on the model to police itself. Checks:
  * excessively long messages,
  * malformed / empty input,
  * prompt-injection attempts,
  * abuse / threatening language,
  * sensitive personal information (card numbers, etc.).

The result tells the agent whether to block, sanitize, or proceed, and carries
flags the downstream nodes use (e.g. injection_detected).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Keep messages to a sane length to avoid abuse / token blowups.
_MAX_LENGTH = 2000

# Prompt-injection signatures (case-insensitive substrings / patterns).
_INJECTION_PATTERNS = (
    r"ignore (all |your |previous |above )?(instructions|rules|prompt)",
    r"disregard (all |your |previous |the )?(instructions|rules)",
    r"forget (all |your |previous |everything)",
    r"you are now",
    r"act as (if you are |an? )?",
    r"system prompt",
    r"reveal your (instructions|prompt|system)",
    r"developer mode",
    r"jailbreak",
    r"print your (instructions|prompt|rules)",
    r"what (are|were) your instructions",
    r"show me (another|other) customer",
)

# Abuse / threat signatures.
_ABUSE_PATTERNS = (
    r"\bidiot\b", r"\bstupid\b", r"\bshut up\b", r"\bmoron\b",
    r"\bi('| wi)ll (kill|hurt|destroy)\b", r"\bgo to hell\b",
)

# Sensitive PII that customers should not paste into chat.
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,16}\b")
_CVV_RE = re.compile(r"\bcvv\b", re.IGNORECASE)
# Aadhaar: 12 digits, often grouped 4-4-4.
_AADHAAR_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
# Password/credential mentions.
_PASSWORD_RE = re.compile(r"\b(password|passwd|my pin|otp is|cvv)\b", re.IGNORECASE)

_DONT_SHARE_MESSAGE = (
    "For your security, please don't share card numbers, Aadhaar, OTPs or "
    "passwords in chat. I've not stored that, and I don't need it to help you. "
    "I only ever verify identity with a one-time code."
)


@dataclass
class InputCheck:
    ok: bool  # False => block
    reason: str | None = None
    message: str | None = None  # safe message to show the customer if blocked
    flags: list[str] = field(default_factory=list)
    injection_detected: bool = False
    contains_sensitive_pii: bool = False
    pii_warning: str | None = None  # "don't share" message when PII detected


def check_input(message: str) -> InputCheck:
    text = (message or "").strip()

    # Malformed / empty.
    if not text:
        return InputCheck(ok=False, reason="empty", message="Please enter a message.")

    # Excessive length.
    if len(text) > _MAX_LENGTH:
        return InputCheck(
            ok=False,
            reason="too_long",
            message="Your message is a bit long. Could you shorten it and try again?",
            flags=["too_long"],
        )

    low = text.lower()
    flags: list[str] = []

    injection = any(re.search(p, low) for p in _INJECTION_PATTERNS)
    if injection:
        flags.append("prompt_injection")

    abuse = any(re.search(p, low) for p in _ABUSE_PATTERNS)
    if abuse:
        flags.append("abuse")

    sensitive = (
        bool(_CARD_RE.search(text))
        or bool(_CVV_RE.search(text))
        or bool(_AADHAAR_RE.search(text))
        or bool(_PASSWORD_RE.search(text))
    )
    if sensitive:
        flags.append("sensitive_pii")

    # Prompt injection and sensitive PII are not hard blocks at the input stage;
    # we proceed but flag them so downstream nodes (and the backend auth/tool
    # layer) enforce the real restrictions and the response avoids echoing PII.
    # When PII is pasted, surface a clear "don't share" warning and never store
    # the raw value. Abuse is handled by the escalation state machine.
    return InputCheck(
        ok=True,
        reason=None,
        flags=flags,
        injection_detected=injection,
        contains_sensitive_pii=sensitive,
        pii_warning=_DONT_SHARE_MESSAGE if sensitive else None,
    )
