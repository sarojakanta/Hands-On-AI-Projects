"""Shared pytest fixtures.

Points the database at an isolated SQLite file under data/ for tests, seeds it
once, and provides a Flask test client.
"""

from __future__ import annotations

import os

# Use a dedicated test database so running tests never clobbers dev data.
os.environ.setdefault("DATABASE_URL", "sqlite:///data/test_support_agent.db")
os.environ.setdefault("DEV_MODE", "true")
# Run MCP tools in-process during tests (same registry + authorization, no
# per-call subprocess spawn). The dedicated transport test overrides this.
os.environ.setdefault("MCP_INPROCESS", "1")

import pytest  # noqa: E402

from app import create_app  # noqa: E402
from app.database.seed import seed  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _seeded_db():
    """Reset and seed the test database once per test session."""
    counts = seed(reset=True)
    return counts


@pytest.fixture(autouse=True)
def _clear_conversation_memory():
    """Isolate conversation memory between tests.

    Memory persists by conversation_id, so without this a conversation id
    reused across tests would carry state over. Clearing the in-process store
    before each test keeps them independent.
    """
    from app.memory.conversation_memory import conversation_memory
    from app.memory.local_store import LocalMemoryStore

    store = getattr(conversation_memory, "_store", None)
    if isinstance(store, LocalMemoryStore):
        store.clear()

    # Isolate rate-limit counters and ticket idempotency between tests.
    from app.services.rate_limiter import rate_limiter
    from app.services import ticket_service

    rate_limiter.reset()
    ticket_service.reset_idempotency()
    yield


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """Force the deterministic (no-LLM) path for ALL tests.

    Tests must be fast, free, and deterministic, so they never hit a real LLM
    provider even when keys are present in the environment. Individual tests
    that need the LLM path monkeypatch ``_resolved`` back on themselves.
    """
    from app.services import llm_service as llm_module
    from app.agents import nodes

    monkeypatch.setattr(llm_module.llm_service, "_resolved", None)
    monkeypatch.setattr(nodes.llm_service, "_resolved", None)
    yield


@pytest.fixture()
def app():
    application = create_app({"TESTING": True})
    return application


@pytest.fixture()
def client(app):
    return app.test_client()
