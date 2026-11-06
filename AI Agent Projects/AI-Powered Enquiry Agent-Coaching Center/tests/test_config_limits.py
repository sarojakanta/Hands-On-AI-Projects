"""Tests proving chat/RAG size limits are driven by configuration.

These monkeypatch the cached settings object (what the app actually reads)
and assert the behaviour follows, confirming the env-wiring end to end.
"""
from __future__ import annotations

from app.config import get_settings
from app.rag.chunking import split_text
from app.rag.retriever import RetrievedChunk, build_context
from tests.conftest import API_KEY_A


def _hdr():
    return {"X-Institute-Key": API_KEY_A}


# --- Chunking honours configured size/overlap -----------------------------
def test_split_text_uses_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rag_chunk_size", 200)
    monkeypatch.setattr(s, "rag_chunk_overlap", 20)
    text = "word " * 500  # 2500 chars
    chunks = split_text(text)  # no explicit args -> must read settings
    assert len(chunks) > 1
    assert all(len(c) <= 260 for c in chunks)  # ~chunk_size + some slack


def test_split_text_larger_chunk_size_yields_fewer_chunks(monkeypatch):
    s = get_settings()
    text = "word " * 500
    monkeypatch.setattr(s, "rag_chunk_size", 200)
    monkeypatch.setattr(s, "rag_chunk_overlap", 0)
    small = split_text(text)
    monkeypatch.setattr(s, "rag_chunk_size", 1000)
    large = split_text(text)
    assert len(large) < len(small)


# --- RAG context cap honours settings ------------------------------------
def test_build_context_respects_max_chars(monkeypatch):
    s = get_settings()
    chunks = [
        RetrievedChunk(text="A" * 500, title="Doc1", score=0.9),
        RetrievedChunk(text="B" * 500, title="Doc2", score=0.8),
        RetrievedChunk(text="C" * 500, title="Doc3", score=0.7),
    ]
    monkeypatch.setattr(s, "rag_context_max_chars", 600)
    ctx = build_context(chunks)  # no explicit max -> reads settings
    # Only the first block fits under 600 chars.
    assert "Doc1" in ctx
    assert "Doc3" not in ctx


# --- Chat message length cap is configurable -----------------------------
def test_chat_message_length_limit_configurable(client, institute, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "max_chat_message_chars", 10)
    # 11 chars -> rejected (422).
    res = client.post("/api/chat", json={"message": "x" * 11}, headers=_hdr())
    assert res.status_code == 422
    # Within limit -> accepted.
    ok = client.post("/api/chat", json={"message": "hi there"}, headers=_hdr())
    assert ok.status_code == 200


# --- Chat history turns cap is configurable ------------------------------
def test_history_turns_limit(client, institute, monkeypatch, db):
    from app.agents.admission_agent import AdmissionAgent
    from app.models import Conversation
    from app.services import conversation_service

    s = get_settings()
    monkeypatch.setattr(s, "chat_history_turns", 2)

    convo = conversation_service.get_or_create_conversation(db, institute.id, None)
    for i in range(6):
        conversation_service.add_message(db, convo, "user", f"msg {i}")
        conversation_service.add_message(db, convo, "assistant", f"reply {i}")

    agent = AdmissionAgent()
    hist = agent._recent_history(db, convo)
    assert len(hist) <= 2
