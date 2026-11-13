import sqlite3

from alembic import command
from alembic.config import Config

from app.db import models as m
from app.db.base import session_scope
from app.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    LLMUsageRepository,
    UserRepository,
    WorkflowExecutionRepository,
)


def test_user_roundtrip():
    with session_scope() as s:
        UserRepository(s).add(m.User(username="alice", password_hash="x", roles="analyst"))
    with session_scope() as s:
        user = UserRepository(s).by_username("alice")
        assert user is not None and user.roles == "analyst"


def test_workflow_execution_with_agent_rows_and_duplicate_lookup():
    with session_scope() as s:
        repo = WorkflowExecutionRepository(s)
        ex = repo.add(m.WorkflowExecution(workflow="incident", record_number="INC1", status="running"))
        repo.add_agent_execution(ex.id, sequence=1, agent_name="classification", status="success", duration_ms=1.5)
        ex_id = ex.id
    with session_scope() as s:
        repo = WorkflowExecutionRepository(s)
        found = repo.find_active("incident", "INC1")
        assert found is not None and found.id == ex_id
        assert len(found.agent_executions) == 1
        assert repo.find_active("incident", "INC2") is None
        assert repo.status_counts() == {"running": 1}


def test_approval_pending_and_audit():
    with session_scope() as s:
        ApprovalRepository(s).add(m.ApprovalRequest(action_type="incident_update", record_number="INC1"))
        AuditRepository(s).record("bob", "approval.requested", "approval", "1", note="n")
    with session_scope() as s:
        assert len(ApprovalRepository(s).pending()) == 1
        events = AuditRepository(s).search(action="approval.requested")
        assert events[0].details == {"note": "n"}


def test_llm_usage_summary():
    with session_scope() as s:
        repo = LLMUsageRepository(s)
        repo.add(m.LLMUsage(provider="groq", prompt_tokens=10, completion_tokens=5, latency_ms=100, estimated_cost=0.1))
        repo.add(m.LLMUsage(provider="groq", success=False, error="boom"))
        summary = repo.summary()
    assert summary["calls"] == 2 and summary["failures"] == 1 and summary["prompt_tokens"] == 10


def test_alembic_migration_matches_models(tmp_path, monkeypatch):
    db_file = tmp_path / "mig.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    from app import config

    monkeypatch.setattr(config.settings, "database_url", f"sqlite:///{db_file.as_posix()}")
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    tables = {r[0] for r in sqlite3.connect(db_file).execute("select name from sqlite_master where type='table'")}
    expected = {t.name for t in m.Base.metadata.sorted_tables}
    assert expected <= tables
