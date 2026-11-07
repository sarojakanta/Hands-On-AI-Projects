"""
Logging setup with credential redaction.

A redacting filter is attached to the application loggers so API keys and
bearer tokens cannot reach the log output even if a message accidentally
includes them. Every line carries the id of the request that produced it (``-`` outside a request).
"""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization: Bearer xxx
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), f"Bearer {REDACTED}"),
    # key=value / "key": "value" for common secret field names
    (
        re.compile(r"(?i)((?:api[_-]?key|x-api-key|authorization|secret|password|token)[\"']?\s*[:=]\s*[\"']?)([^\s\"',}&]+)"),
        rf"\1{REDACTED}",
    ),
    # Well-known key shapes (OpenAI/Anthropic 'sk-...', Groq 'gsk_...')
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), REDACTED),
    (re.compile(r"\bgsk_[A-Za-z0-9]{8,}"), REDACTED),
]


def redact(text: str) -> str:
    """Return ``text`` with anything that looks like a credential replaced."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Scrub credentials from every record before it is formatted."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Configure the ``app`` and ``rag`` loggers (idempotent)."""
    from app.core.observability import install_request_id_logging

    install_request_id_logging()
    for name in ("app", "rag"):
        logger = logging.getLogger(name)
        logger.setLevel(level)
        if any(getattr(h, "_rag_handler", False) for h in logger.handlers):
            continue
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"))
        handler.addFilter(RedactingFilter())
        handler._rag_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
        logger.propagate = False
