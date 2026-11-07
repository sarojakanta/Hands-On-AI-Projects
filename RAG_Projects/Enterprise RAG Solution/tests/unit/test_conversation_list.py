"""Listing conversations: both stores and the GET /api/v1/conversations endpoint."""

import pytest

from app.db.conversation_store import SqlConversationStore
from app.db.session import Database
from app.rag.conversations import (
    ConversationMessage,
    InMemoryConversationStore,
    make_title,
)
from tests.conftest import make_stack


def msg(role, content):
    return ConversationMessage(role=role, content=content)


@pytest.fixture(params=["sql", "memory"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryConversationStore()
    return SqlConversationStore(_started_db(tmp_path))


def _started_db(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    db.create_all()
    return db


def test_title_helper():
    assert make_title("  What was the\n profit? ") == "What was the profit?"
    assert make_title("") == "New conversation"
    assert make_title("x" * 200).endswith("\u2026") and len(make_title("x" * 200)) == 80


def test_list_is_empty_until_a_message_exists(store):
    assert store.list() == []
    conversation = store.create()
    assert store.list() == []                       # created but no messages yet
    store.append(conversation.id, [msg("user", "First question"), msg("assistant", "An answer")])
    summaries = store.list()
    assert len(summaries) == 1
    assert summaries[0].id == conversation.id
    assert summaries[0].title == "First question"
    assert summaries[0].message_count == 2


def test_list_is_most_recently_used_first(store):
    a = store.create()
    b = store.create()
    store.append(a.id, [msg("user", "Question A"), msg("assistant", "A")])
    store.append(b.id, [msg("user", "Question B"), msg("assistant", "B")])
    assert [s.title for s in store.list()] == ["Question B", "Question A"]
    store.append(a.id, [msg("user", "Follow up on A"), msg("assistant", "A2")])   # touches A
    assert [s.title for s in store.list()][0] == "Question A"                       # A now most recent
    assert store.list()[0].message_count == 4


def test_title_comes_from_the_first_user_message(store):
    c = store.create()
    store.append(c.id, [msg("user", "The original question"), msg("assistant", "a"), msg("user", "A later one")])
    assert store.list()[0].title == "The original question"


def test_deleted_conversations_leave_the_list(store):
    c = store.create()
    store.append(c.id, [msg("user", "temp"), msg("assistant", "a")])
    assert store.delete(c.id) is True
    assert store.list() == []


def test_limit_is_respected(store):
    for i in range(5):
        c = store.create()
        store.append(c.id, [msg("user", f"Q{i}"), msg("assistant", "a")])
    assert len(store.list(limit=3)) == 3


# ---------------------------------------------------------------- endpoint


SIP = "# SIP\n\nA SIP endpoint should register with valid credentials and receive a success response."


@pytest.fixture
def api(tmp_path):
    instance = make_stack(tmp_path)
    yield instance
    instance.close()


def seed_turn(store, question):
    c = store.create()
    store.append(c.id, [msg("user", question), msg("assistant", "answer")])
    return c.id


def test_list_endpoint_returns_titles_and_counts_without_bodies(api):
    store = api.services.conversations
    first = seed_turn(store, "What is SIP registration?")
    second = seed_turn(store, "How does an IVR handle timeouts?")

    body = api.client.get("/api/v1/conversations").json()
    ids = [c["id"] for c in body["conversations"]]
    assert ids == [second, first]                      # most recent first
    row = body["conversations"][0]
    assert row["title"] == "How does an IVR handle timeouts?" and row["message_count"] == 2
    assert "messages" not in row and "answer" not in row

    detail = api.client.get(f"/api/v1/conversations/{first}").json()
    assert len(detail["messages"]) == 2                # the detail endpoint still returns bodies


def test_list_endpoint_clamps_limit(api):
    assert api.client.get("/api/v1/conversations?limit=100000").status_code == 200
    assert api.client.get("/api/v1/conversations?limit=0").status_code == 200
