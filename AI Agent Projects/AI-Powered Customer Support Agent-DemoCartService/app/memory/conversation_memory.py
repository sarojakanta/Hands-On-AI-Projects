"""Working (per-session) conversation memory (M-1, M-2, M-6).

Holds short-lived session context so the customer does not repeat themselves.
Design per the memory spec:
  * stores a rolling SUMMARY + STRUCTURED FACTS, not long raw chat text (M-1),
  * keeps only the last few raw turns transiently for the LLM; older turns are
    folded into the summary once a session passes 10 turns (M-6),
  * verification status is NOT persisted here - it lives only in the backend
    session and is re-derived each turn (M-2),
  * TTL is re-applied on every save so a session expires after idle timeout
    (SESSION_TTL_SECONDS, default 30 minutes).

Separate from the RAG knowledge base and from per-customer episodic/preference
memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from app.config import config
from app.memory.base import MemoryStore
from app.memory.local_store import LocalMemoryStore
from app.memory.summarizer import summarize, SUMMARIZE_AFTER_TURNS


# Keep at most this many raw turns in working memory; older ones are summarized.
_MAX_RAW_TURNS = 10
_KEY_PREFIX = "conv:"


def _build_store() -> MemoryStore:
    """Select Redis when configured, otherwise the local in-process store."""
    if config.REDIS_URL:
        try:
            from app.memory.redis_store import RedisMemoryStore

            store = RedisMemoryStore(config.REDIS_URL)
            store.exists("__healthcheck__")  # fail fast on a bad URL
            return store
        except Exception:
            return LocalMemoryStore()
    return LocalMemoryStore()


@dataclass
class ConversationState:
    conversation_id: str
    customer_id: str | None = None
    # Recent raw turns (transient, capped at _MAX_RAW_TURNS).
    messages: list[dict] = field(default_factory=list)
    # Rolling one-paragraph summary of older turns (M-1/M-6).
    summary: str = ""
    # Structured facts (not raw chat text).
    intent: str | None = None
    active_order: str | None = None
    # Account request retained across OTP verification and follow-up turns.
    pending_intent: str | None = None
    pending_order_ref: str | None = None
    awaiting_order_ref: bool = False
    language: str | None = None
    tool_results_summary: str | None = None
    issue_summary: str | None = None
    escalated: bool = False
    # NOTE: verification status is deliberately NOT stored here (M-2).
    # Guardrail state-machine counters.
    abuse_count: int = 0
    unresolved_attempts: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationState":
        return cls(
            conversation_id=data.get("conversation_id", ""),
            customer_id=data.get("customer_id"),
            messages=data.get("messages", []),
            summary=data.get("summary", ""),
            intent=data.get("intent"),
            active_order=data.get("active_order"),
            pending_intent=data.get("pending_intent"),
            pending_order_ref=data.get("pending_order_ref"),
            awaiting_order_ref=bool(data.get("awaiting_order_ref", False)),
            language=data.get("language"),
            tool_results_summary=data.get("tool_results_summary"),
            issue_summary=data.get("issue_summary"),
            escalated=bool(data.get("escalated", False)),
            abuse_count=int(data.get("abuse_count", 0)),
            unresolved_attempts=int(data.get("unresolved_attempts", 0)),
        )


class ConversationMemory:
    def __init__(self, store: MemoryStore | None = None, ttl_seconds: int | None = None) -> None:
        self._store = store or _build_store()
        self._ttl = ttl_seconds or config.SESSION_TTL_SECONDS

    def _key(self, conversation_id: str) -> str:
        return f"{_KEY_PREFIX}{conversation_id}"

    def load(self, conversation_id: str) -> ConversationState:
        """Load state for a conversation, or a fresh state if none exists.

        Isolation: a conversation id only ever returns its own state.
        """
        raw = self._store.get(self._key(conversation_id))
        if raw is None:
            return ConversationState(conversation_id=conversation_id)
        return ConversationState.from_dict(raw)

    def save(self, state: ConversationState) -> None:
        """Persist state. Older raw turns are summarized then dropped (M-1/M-6),
        and the TTL is re-applied so idle sessions expire (30-min default)."""
        if len(state.messages) > SUMMARIZE_AFTER_TURNS:
            # Fold everything except the most recent turns into the summary,
            # then keep only the recent raw turns.
            to_summarize = state.messages[:-_MAX_RAW_TURNS] or state.messages
            state.summary = summarize(state.summary, to_summarize)
            state.messages = state.messages[-_MAX_RAW_TURNS:]
        elif len(state.messages) > _MAX_RAW_TURNS:
            state.messages = state.messages[-_MAX_RAW_TURNS:]

        self._store.set(self._key(state.conversation_id), state.to_dict(), ttl_seconds=self._ttl)

    def append_turn(self, state: ConversationState, *, role: str, content: str) -> None:
        if role in ("user", "assistant") and content:
            state.messages.append({"role": role, "content": content})

    def clear(self, conversation_id: str) -> None:
        self._store.delete(self._key(conversation_id))


# Convenience singleton.
conversation_memory = ConversationMemory()
