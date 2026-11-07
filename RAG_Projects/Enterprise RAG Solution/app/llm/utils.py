"""Small helpers shared by the provider implementations."""

from __future__ import annotations

from typing import Any

from app.llm.base import Usage

_FINISH_REASONS = {
    "stop": "stop",
    "end_turn": "stop",
    "stop_sequence": "stop",
    "length": "length",
    "max_tokens": "length",
}


def normalize_finish_reason(reason: Any) -> str | None:
    """Map provider-specific stop reasons to ``'stop'`` / ``'length'`` (others pass through)."""
    if reason is None or reason == "":
        return None
    text = str(reason).lower()
    return _FINISH_REASONS.get(text, text)


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def usage_from_counts(prompt: Any, completion: Any) -> Usage | None:
    """Build a ``Usage`` from raw counts; ``None`` when the provider reported neither."""
    prompt_tokens, completion_tokens = _as_int(prompt), _as_int(completion)
    if prompt_tokens is None and completion_tokens is None:
        return None
    return Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
