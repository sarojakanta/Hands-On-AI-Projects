"""Conversation summarization (M-1, M-6).

Rolls older conversation turns into a short, one-paragraph summary so working
memory holds a summary + structured facts rather than raw chat text containing
personal data. Uses the LLM when configured, and a deterministic fallback
(intent + a compressed trace of topics) when it is not, so it always works
offline.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Summarize once a session exceeds this many turns (M-6).
SUMMARIZE_AFTER_TURNS = 10


def _deterministic_summary(prior_summary: str | None, turns: list[dict]) -> str:
    """A privacy-safe, LLM-free summary: topics only, no raw PII echoed."""
    user_msgs = [t.get("content", "") for t in turns if t.get("role") == "user"]
    topics = "; ".join(m[:60] for m in user_msgs[-5:] if m)
    base = prior_summary.strip() if prior_summary else ""
    addition = f"Customer discussed: {topics}." if topics else ""
    combined = " ".join(p for p in (base, addition) if p).strip()
    return combined[:600]


def summarize(prior_summary: str | None, turns: list[dict]) -> str:
    """Return an updated one-paragraph summary of the conversation so far."""
    from app.services.llm_service import llm_service, LLMNotConfiguredError

    if not turns:
        return prior_summary or ""

    if llm_service.configured:
        try:
            transcript = "\n".join(
                f"{t.get('role')}: {t.get('content','')}" for t in turns[-12:]
            )
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Summarize this customer support conversation in ONE short "
                        "paragraph (max 60 words). Capture the issue and any outcome. "
                        "Do NOT include card numbers, OTPs, phone numbers, emails or "
                        "other personal identifiers - topics and decisions only."
                    ),
                },
                {
                    "role": "user",
                    "content": (f"Prior summary: {prior_summary or 'none'}\n\n{transcript}"),
                },
            ]
            result = llm_service.chat(messages, temperature=0.0, max_tokens=120)
            if result.text:
                return result.text.strip()[:600]
        except (LLMNotConfiguredError, Exception) as exc:  # noqa: BLE001
            logger.info("Summary LLM unavailable, using deterministic: %s", exc)

    return _deterministic_summary(prior_summary, turns)
