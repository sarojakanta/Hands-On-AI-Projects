"""Output guardrails.

Run on the agent's generated reply BEFORE it reaches the customer. These catch
things the LLM might produce despite the system prompt. Checks:
  * PII leakage (emails, full card/phone numbers),
  * unsupported refund/discount promises,
  * unverified delivery-date commitments not backed by tool data,
  * disclosure of internal instructions or MCP tool names,
  * inappropriate tone (profanity).

The guardrail sanitizes where it safely can (redact PII, strip tool names) and
replaces the reply entirely when it makes an unsupported promise, so the final
message is always safe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Internal tool names that must never appear in a customer reply (rule 11).
# Covers the current domain tool names and the legacy names, for safety.
_TOOL_NAMES = (
    "send_otp", "verify_customer", "get_customer_profile",
    "get_order", "get_shipment_status", "request_address_change",
    "create_ticket", "escalate_to_human",
    "find_store", "get_store_hours",
    # legacy aliases
    "initiate_otp", "search_store", "create_support_ticket",
)

# Phrases that imply an unsupported promise (unless backed by data).
_REFUND_PROMISE = re.compile(
    r"\b(i('| wi)ll|we('| wi)ll|i can|we can|i have|we have)\b[^.]{0,40}\b(refund|refunded)\b",
    re.IGNORECASE,
)
_DISCOUNT_PROMISE = re.compile(
    r"\b(i('| wi)ll|we('| wi)ll|i can|we can|here('|')?s)\b[^.]{0,40}\b(discount|coupon|voucher|% off)\b",
    re.IGNORECASE,
)
# Unsupported compensation promise.
_COMPENSATION_PROMISE = re.compile(
    r"\b(i('| wi)ll|we('| wi)ll|i can|we can)\b[^.]{0,40}\b(compensat\w*|credit your account|reimburse\w*|make it up to you)\b",
    re.IGNORECASE,
)
# Unsupported firm delivery-date commitment (only allowed when backed by tool
# data, which the caller signals via tool_data_present).
_DELIVERY_DATE_PROMISE = re.compile(
    r"\b(will|guarantee[d]?|definitely|for sure)\b[^.]{0,30}\b(deliver|arrive|reach you|be there)\b",
    re.IGNORECASE,
)
# Medical / legal / financial advice (off-scope) signatures.
_ADVICE = re.compile(
    r"\b(you should (take|consult a doctor|see a lawyer|invest|sue)|"
    r"i recommend (you )?(invest|take|buy stock|see a doctor)|"
    r"(medical|legal|financial) advice|diagnos|prescrib)\b",
    re.IGNORECASE,
)

# Internal-instruction disclosure.
_INSTRUCTION_LEAK = re.compile(
    r"\b(my (system )?(prompt|instructions)|system prompt|my rules are)\b", re.IGNORECASE
)

# PII patterns.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,16}\b")
_PHONE_RE = re.compile(r"\+?\d[\d -]{8,}\d")

_PROFANITY = (r"\bdamn\b", r"\bhell\b", r"\bstupid\b")

_SAFE_REPLACEMENT = (
    "I'm not able to promise that directly, but I can connect you with a human "
    "support specialist who can review your case. Would you like me to do that?"
)


@dataclass
class OutputCheck:
    reply: str
    modified: bool = False
    violations: list[str] = field(default_factory=list)


def check_output(reply: str, *, tool_data_present: bool = False) -> OutputCheck:
    """Validate and, where needed, sanitize the outgoing reply."""
    text = reply or ""
    violations: list[str] = []
    modified = False

    # 1) Strip any leaked internal tool names.
    for name in _TOOL_NAMES:
        if name in text:
            text = text.replace(name, "our system")
            violations.append("tool_name_leak")
            modified = True

    # 2) Block instruction disclosure.
    if _INSTRUCTION_LEAK.search(text):
        violations.append("instruction_leak")
        return OutputCheck(
            reply="I can help with your order, delivery, store info, or our policies. "
            "What would you like to do?",
            modified=True,
            violations=violations,
        )

    # 3) Unsupported refund / discount / compensation promises -> replace.
    if _REFUND_PROMISE.search(text):
        violations.append("unsupported_refund")
        return OutputCheck(reply=_SAFE_REPLACEMENT, modified=True, violations=violations)
    if _DISCOUNT_PROMISE.search(text):
        violations.append("unsupported_discount")
        return OutputCheck(reply=_SAFE_REPLACEMENT, modified=True, violations=violations)
    if _COMPENSATION_PROMISE.search(text):
        violations.append("unsupported_compensation")
        return OutputCheck(reply=_SAFE_REPLACEMENT, modified=True, violations=violations)

    # 3b) A firm delivery-date commitment is only allowed when backed by tool
    # data (an actual expected-delivery-date from the shipment tool).
    if _DELIVERY_DATE_PROMISE.search(text) and not tool_data_present:
        violations.append("unverified_delivery_date")
        return OutputCheck(reply=_SAFE_REPLACEMENT, modified=True, violations=violations)

    # 3c) Medical / legal / financial advice is out of scope -> decline + human.
    if _ADVICE.search(text):
        violations.append("off_scope_advice")
        return OutputCheck(
            reply="I'm not able to give medical, legal or financial advice. For "
            "your order, delivery, store info or our policies I'm happy to help, "
            "or I can connect you with a human specialist.",
            modified=True,
            violations=violations,
        )

    # 4) Redact PII that should not be echoed.
    if _EMAIL_RE.search(text):
        text = _EMAIL_RE.sub("[redacted email]", text)
        violations.append("pii_email")
        modified = True
    if _CARD_RE.search(text):
        text = _CARD_RE.sub("[redacted]", text)
        violations.append("pii_card")
        modified = True

    # 5) Tone: soften obvious profanity.
    for pat in _PROFANITY:
        if re.search(pat, text, re.IGNORECASE):
            violations.append("tone")
            text = re.sub(pat, "", text, flags=re.IGNORECASE)
            modified = True

    return OutputCheck(reply=text.strip(), modified=modified, violations=violations)
