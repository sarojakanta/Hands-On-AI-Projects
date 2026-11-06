"""Phase 6 conversation memory tests.

Covers per-conversation isolation, expiry, context retention across turns, and
the local store's TTL behaviour. Uses the local in-process store (no Redis).
"""

from __future__ import annotations

import time

from app.memory.local_store import LocalMemoryStore
from app.memory.conversation_memory import ConversationMemory, ConversationState


def _memory():
    # Fresh store per test for isolation.
    return ConversationMemory(store=LocalMemoryStore(), ttl_seconds=60)


def test_load_returns_fresh_state_when_missing():
    mem = _memory()
    state = mem.load("conv-new")
    assert isinstance(state, ConversationState)
    assert state.conversation_id == "conv-new"
    assert state.messages == []


def test_save_and_reload_round_trip():
    mem = _memory()
    state = mem.load("conv-1")
    mem.append_turn(state, role="user", content="Where is my order?")
    mem.append_turn(state, role="assistant", content="Please verify your identity.")
    state.intent = "ORDER_STATUS"
    # Verification status is intentionally NOT part of working memory (M-2);
    # we persist structured facts like active_order instead.
    state.active_order = "UC-48213"
    mem.save(state)

    reloaded = mem.load("conv-1")
    assert reloaded.intent == "ORDER_STATUS"
    assert reloaded.active_order == "UC-48213"
    assert len(reloaded.messages) == 2
    assert reloaded.messages[0]["content"] == "Where is my order?"


def test_conversations_are_isolated():
    mem = _memory()
    a = mem.load("conv-a")
    mem.append_turn(a, role="user", content="A secret")
    mem.save(a)

    b = mem.load("conv-b")
    assert b.messages == []  # B never sees A's content


def test_messages_are_trimmed():
    mem = _memory()
    state = mem.load("conv-trim")
    for i in range(50):
        mem.append_turn(state, role="user", content=f"msg {i}")
    mem.save(state)
    reloaded = mem.load("conv-trim")
    assert len(reloaded.messages) <= 12  # _MAX_TURNS


def test_local_store_ttl_expiry():
    store = LocalMemoryStore()
    store.set("k", {"v": 1}, ttl_seconds=1)
    assert store.get("k") == {"v": 1}
    time.sleep(1.1)
    assert store.get("k") is None


def test_escalation_flag_persists():
    mem = _memory()
    state = mem.load("conv-esc")
    state.escalated = True
    state.issue_summary = "Customer threatening legal action."
    mem.save(state)
    assert mem.load("conv-esc").escalated is True
    assert "legal" in mem.load("conv-esc").issue_summary
