"""Customer satisfaction (thumbs up/down) capture (CSAT).

A lightweight in-process store of thumbs feedback per conversation turn. In
production this would be a DB table; here it supports the CSAT launch metric
(>= 80% thumbs up). The metric is only meaningful with REAL user feedback, so
when no feedback exists ``csat_score`` returns None (reported as
"not measurable offline") rather than a fake number.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
# conversation_id -> list of bool (True = thumbs up).
_feedback: dict[str, list[bool]] = {}


def record_feedback(conversation_id: str, thumbs_up: bool) -> None:
    if not conversation_id:
        return
    with _lock:
        _feedback.setdefault(conversation_id, []).append(bool(thumbs_up))


def csat_score() -> float | None:
    """Fraction of thumbs-up across all feedback, or None if no feedback yet."""
    with _lock:
        votes = [v for vs in _feedback.values() for v in vs]
    if not votes:
        return None
    return sum(1 for v in votes if v) / len(votes)


def reset() -> None:
    with _lock:
        _feedback.clear()
