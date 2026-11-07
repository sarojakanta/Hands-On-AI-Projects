"""Unit tests for the conversation store and the legacy-engine retriever adapter."""

from __future__ import annotations

import pytest

from app.rag.conversations import (
    MAX_MESSAGES_PER_CONVERSATION,
    ConversationMessage,
    ConversationNotFoundError,
    InMemoryConversationStore,
)
from app.rag.retrieval import KnowledgeBaseNotReadyError, LegacyEngineRetriever

pytestmark = pytest.mark.anyio


def msg(role, text):
    return ConversationMessage(role=role, content=text)


def test_create_append_get_and_recent():
    store = InMemoryConversationStore()
    conversation = store.create()
    assert len(conversation.id) == 32 and conversation.messages == []

    store.append(conversation.id, [msg("user", "q1"), msg("assistant", "a1"), msg("user", "q2"), msg("assistant", "a2")])

    assert [m.content for m in store.get(conversation.id).messages] == ["q1", "a1", "q2", "a2"]
    assert [m.content for m in store.recent(conversation.id, 2)] == ["q2", "a2"]
    assert store.recent(conversation.id, 0) == []


def test_unknown_conversation_is_a_404_style_error():
    store = InMemoryConversationStore()
    for call in (lambda: store.get("nope"), lambda: store.recent("nope", 2), lambda: store.append("nope", [msg("user", "x")])):
        with pytest.raises(ConversationNotFoundError) as info:
            call()
        assert info.value.status_code == 404 and info.value.code == "conversation_not_found"


def test_delete_reports_whether_anything_was_removed():
    store = InMemoryConversationStore()
    conversation = store.create()
    assert store.delete(conversation.id) is True
    assert store.delete(conversation.id) is False
    with pytest.raises(ConversationNotFoundError):
        store.get(conversation.id)


def test_least_recently_used_conversation_is_evicted():
    store = InMemoryConversationStore(max_conversations=2)
    first, second = store.create(), store.create()
    store.get(first.id)              # touch: now `second` is the oldest
    third = store.create()

    store.get(first.id)
    store.get(third.id)
    with pytest.raises(ConversationNotFoundError):
        store.get(second.id)


def test_conversation_length_is_capped_keeping_the_newest_messages():
    store = InMemoryConversationStore()
    conversation = store.create()
    store.append(conversation.id, [msg("user", f"m{i}") for i in range(MAX_MESSAGES_PER_CONVERSATION + 10)])
    messages = store.get(conversation.id).messages
    assert len(messages) == MAX_MESSAGES_PER_CONVERSATION
    assert messages[-1].content == f"m{MAX_MESSAGES_PER_CONVERSATION + 9}"


def test_callers_cannot_mutate_stored_history_through_returned_copies():
    store = InMemoryConversationStore()
    conversation = store.create()
    store.append(conversation.id, [msg("user", "q")])
    store.get(conversation.id).messages.clear()
    assert len(store.get(conversation.id).messages) == 1


# --------------------------------------------------------------------------- retriever adapter


class FakeEngine:
    def __init__(self, ready=True, rows=None):
        self.is_ready = ready
        self.rows = rows or []
        self.calls = []

    def retrieve(self, query, top_k):
        self.calls.append((query, top_k))
        return self.rows


async def test_adapter_maps_engine_rows_to_chunks():
    engine = FakeEngine(rows=[{"chunk": "text", "source": "a.txt", "chunk_index": 2, "score": 0.5}])
    (chunk,) = await LegacyEngineRetriever(engine).retrieve("question", 3)

    assert (chunk.text, chunk.source, chunk.chunk_index, chunk.score) == ("text", "a.txt", 2, 0.5)
    assert chunk.chunk_id == "a.txt#2"
    assert engine.calls == [("question", 3)]


async def test_adapter_tolerates_rows_without_chunk_index():
    engine = FakeEngine(rows=[{"chunk": "text", "source": "a.txt", "score": 0.5}])
    (chunk,) = await LegacyEngineRetriever(engine).retrieve("q", 1)
    assert chunk.chunk_index is None and chunk.chunk_id is None


async def test_adapter_reports_an_index_that_is_not_ready():
    retriever = LegacyEngineRetriever(FakeEngine(ready=False))
    assert not retriever.is_ready
    with pytest.raises(KnowledgeBaseNotReadyError) as info:
        await retriever.retrieve("q", 3)
    assert info.value.status_code == 503
