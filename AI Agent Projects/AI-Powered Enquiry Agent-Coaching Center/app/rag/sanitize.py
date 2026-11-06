"""Prompt-injection sanitisation for ingested and retrieved content.

Uploaded documents and retrieved chunks are UNTRUSTED. Attackers can embed
instructions like "ignore previous instructions" inside a PDF to try to
hijack the agent. We neutralise the most common injection patterns before
the text ever reaches the model, and we always treat document text as data,
never as instructions (reinforced in the agent system prompt).
"""
from __future__ import annotations

import re

# Patterns that look like attempts to override the system / agent behaviour.
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(the\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?prior\s+instructions",
    r"disregard\s+(all\s+)?(the\s+)?(previous|above|prior)\s+(instructions|context)",
    r"forget\s+(everything|all)\s+(you|above)",
    r"you\s+are\s+now\s+[a-z]",
    r"act\s+as\s+(an?\s+)?(?:dan|jailbreak|developer\s+mode)",
    r"system\s+prompt\s*[:=]",
    r"</?\s*system\s*>",
    r"reveal\s+(your\s+)?(system\s+)?(prompt|instructions)",
    r"print\s+(your\s+)?(system\s+)?(prompt|instructions)",
    r"\bBEGIN\s+SYSTEM\b",
]

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

# Zero-width / control characters sometimes used to smuggle hidden text.
_CONTROL_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")

_REDACTION = "[removed: potential injected instruction]"


def sanitize_text(text: str) -> str:
    """Return text with injection attempts neutralised.

    This is intentionally conservative: it only strips obvious instruction-
    override phrases and invisible control characters, leaving legitimate
    document content intact.
    """
    if not text:
        return ""
    cleaned = _CONTROL_RE.sub("", text)
    cleaned = _INJECTION_RE.sub(_REDACTION, cleaned)
    return cleaned


def contains_injection(text: str) -> bool:
    """True if the text appears to contain a prompt-injection attempt."""
    return bool(text) and bool(_INJECTION_RE.search(text))
