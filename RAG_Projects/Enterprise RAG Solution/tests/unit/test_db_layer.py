"""Tests for the SQL layer: schema, settings repository and the SQL conversation store (real SQLite)."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select, text

from app.db.conversation_store import SqlConversationStore
from app.db.models import Chunk, Document, DocumentVersion, iso
from app.db.session import Database
from app.db.settings_repo import SettingsRepository
from app.rag.conversations import (
    MAX_MESSAGES_PER_CONVERSATION,
    ConversationMessage,
    ConversationNotFoundError,
)


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.create_all()
    yield database
    database.dispose()


def msg(role, content, **metadata):
    return ConversationMessage(role=role, content=content, metadata=metadata)


# --------------------------------------------------------------------------- schema


def test_schema_contains_all_tables(db):
    tables = set(inspect(db.engine).get_table_names())
    assert {"documents", "document_versions", "chunks", "conversations", "messages", "app_settings"} <= tables


def test_sqlite_pragmas_are_applied(db):
    with db.engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert str(connection.execute(text("PRAGMA journal_mode")).scalar()).lower() == "wal"


def test_deleting_a_document_cascades_to_versions_and_chunks(db):
    with db.session() as session:
        document = Document(name="A.txt", name_key="a.txt")
        version = DocumentVersion(version_number=1, content_hash="h" * 64)
        version.chunks.append(Chunk(ordinal=0, vector_id="v0", text="hello", char_count=5))
        document.versions.append(version)
        session.add(document)

    with db.session() as session:
        session.delete(session.scalars(select(Document)).one())

    with db.session() as session:
        assert session.scalars(select(DocumentVersion)).all() == []
        assert session.scalars(select(Chunk)).all() == []


def test_document_names_are_unique_case_insensitively_through_name_key(db):
    from sqlalchemy.exc import IntegrityError

    with db.session() as session:
        session.add(Document(name="Notes.txt", name_key="notes.txt"))
    with pytest.raises(IntegrityError):
        with db.session() as session:
            session.add(Document(name="notes.TXT", name_key="notes.txt"))


def test_version_numbers_are_unique_per_document(db):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with db.session() as session:
            document = Document(name="A.txt", name_key="a.txt")
            document.versions.append(DocumentVersion(version_number=1, content_hash="a" * 64))
            document.versions.append(DocumentVersion(version_number=1, content_hash="b" * 64))
            session.add(document)


def test_session_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        with db.session() as session:
            session.add(Document(name="Temp.txt", name_key="temp.txt"))
            session.flush()
            raise RuntimeError("boom")
    with db.session() as session:
        assert session.scalars(select(Document)).all() == []


def test_iso_helper_treats_naive_datetimes_as_utc():
    from datetime import datetime

    assert iso(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05+00:00"
    assert iso(None) is None


# --------------------------------------------------------------------------- settings


def test_settings_round_trip_overwrite_and_delete(db):
    repo = SettingsRepository(db)
    assert repo.get("llm") is None

    repo.set("llm", {"provider": "ollama", "temperature": 0.2})
    repo.set("llm", {"provider": "groq", "temperature": 0.5})

    assert repo.get("llm") == {"provider": "groq", "temperature": 0.5}
    assert repo.delete("llm") is True and repo.delete("llm") is False
    assert repo.get("llm") is None


def test_corrupt_settings_are_ignored_not_fatal(db):
    from app.db.models import AppSetting

    with db.session() as session:
        session.add(AppSetting(key="bad", value_json="{not json"))
        session.add(AppSetting(key="list", value_json="[1, 2]"))
    repo = SettingsRepository(db)
    assert repo.get("bad") is None and repo.get("list") is None


# --------------------------------------------------------------------------- conversations


def test_conversation_round_trip_with_metadata(db):
    store = SqlConversationStore(db)
    conversation = store.create()
    assert len(conversation.id) == 32 and conversation.messages == []

    store.append(conversation.id, [msg("user", "q1"), msg("assistant", "a1", citations=[{"number": 1}], found_in_documents=True)])

    messages = store.get(conversation.id).messages
    assert [(m.role, m.content) for m in messages] == [("user", "q1"), ("assistant", "a1")]
    assert messages[1].metadata == {"citations": [{"number": 1}], "found_in_documents": True}
    assert messages[0].metadata == {} and messages[0].created_at.endswith("+00:00")


def test_messages_keep_their_order_across_appends(db):
    store = SqlConversationStore(db)
    conversation = store.create()
    store.append(conversation.id, [msg("user", "1"), msg("assistant", "2")])
    store.append(conversation.id, [msg("user", "3"), msg("assistant", "4")])
    assert [m.content for m in store.get(conversation.id).messages] == ["1", "2", "3", "4"]
    assert [m.content for m in store.recent(conversation.id, 3)] == ["2", "3", "4"]
    assert store.recent(conversation.id, 0) == []


def test_history_survives_reopening_the_database(tmp_path):
    dsn = f"sqlite:///{(tmp_path / 'persist.db').as_posix()}"
    first = Database(dsn)
    first.create_all()
    conversation_id = SqlConversationStore(first).create().id
    SqlConversationStore(first).append(conversation_id, [msg("user", "remember me")])
    first.dispose()

    second = Database(dsn)
    assert [m.content for m in SqlConversationStore(second).get(conversation_id).messages] == ["remember me"]
    second.dispose()


def test_unknown_conversation_raises_the_404_error(db):
    store = SqlConversationStore(db)
    for call in (lambda: store.get("nope"), lambda: store.recent("nope", 2), lambda: store.append("nope", [msg("user", "x")])):
        with pytest.raises(ConversationNotFoundError):
            call()


def test_delete_removes_conversation_and_messages(db):
    from app.db.models import Message

    store = SqlConversationStore(db)
    conversation = store.create()
    store.append(conversation.id, [msg("user", "x")])

    assert store.delete(conversation.id) is True
    assert store.delete(conversation.id) is False
    with db.session() as session:
        assert session.scalars(select(Message)).all() == []
    with pytest.raises(ConversationNotFoundError):
        store.get(conversation.id)


def test_least_recently_used_conversations_are_evicted(db):
    store = SqlConversationStore(db, max_conversations=2)
    first, second = store.create(), store.create()
    store.get(first.id)             # touching refreshes recency: `second` becomes the oldest
    third = store.create()

    store.get(first.id)
    store.get(third.id)
    with pytest.raises(ConversationNotFoundError):
        store.get(second.id)


def test_conversation_length_is_capped_keeping_the_newest(db):
    store = SqlConversationStore(db)
    conversation = store.create()
    store.append(conversation.id, [msg("user", f"m{i}") for i in range(MAX_MESSAGES_PER_CONVERSATION + 10)])
    messages = store.get(conversation.id).messages
    assert len(messages) == MAX_MESSAGES_PER_CONVERSATION
    assert messages[-1].content == f"m{MAX_MESSAGES_PER_CONVERSATION + 9}"
