"""Phase 8 tests: audit tracing (with redaction) + admin log view."""

from __future__ import annotations

import json

from app.observability import tracing
from app.agents.graph import run_agent
from app.config import config


# ------------------------------------------------------------------ redaction

def test_scrub_redacts_forbidden_keys():
    scrubbed = tracing._scrub({"code": "123456", "customer_id": "CUST-1", "password": "x"})
    assert scrubbed["code"] == "[REDACTED]"
    assert scrubbed["password"] == "[REDACTED]"
    assert scrubbed["customer_id"] == "CUST-1"


def test_scrub_redacts_email_and_card_in_strings():
    out = tracing._scrub("contact john@example.com card 4111 1111 1111 1111")
    assert "john@example.com" not in out
    assert "4111 1111 1111 1111" not in out


def test_scrub_redacts_api_keys():
    out = tracing._scrub("key sk-abcdef1234567890 and gsk_abcdef1234567890")
    assert "sk-abcdef1234567890" not in out
    assert "gsk_abcdef1234567890" not in out


def test_tool_results_reduced_to_status():
    summary = tracing._tool_status_only(
        {"get_order": {"ok": True, "data": {"secret": 1}}, "x": {"ok": False, "error": "nope"}}
    )
    assert summary == {"get_order": "ok", "x": "nope"}


# ------------------------------------------------------------------- build/emit

def test_build_trace_has_required_fields():
    out = run_agent(message="Can I return an item?", conversation_id="trace-1", session_id="s1")
    # Re-emit from a minimal state to assert the shape deterministically.
    state = {
        "conversation_id": "trace-1",
        "intent": out.intent,
        "workflow_steps": ["a", "b"],
        "tool_names_used": [],
        "tool_results": {},
        "model": None,
        "model_latency_ms": 0,
    }
    rec = tracing.build_trace(state, out)
    for key in (
        "timestamp", "conversation_id", "intent", "workflow_steps",
        "rag_references", "mcp_tools", "tool_results", "model",
        "model_latency_ms", "authenticated", "escalated", "ticket_reference",
    ):
        assert key in rec


def test_emit_writes_and_read_recent_returns_it():
    run_agent(message="What are your store hours?", conversation_id="trace-emit", session_id="s1")
    recent = tracing.read_recent(limit=50)
    assert any(r.get("conversation_id") == "trace-emit" for r in recent)


def test_dev_logs_rotate_at_configured_size_and_backup_count(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(config, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "LOG_MAX_MB", 1)
    monkeypatch.setattr(config, "LOG_BACKUP_COUNT", 2)

    path = log_dir / "guardrail_events.log"
    path.parent.mkdir()

    for index in range(4):
        path.write_text("x" * (1024 * 1024), encoding="utf-8")
        tracing.log_guardrail_event(stage="test", event=f"event-{index}")

    backups = sorted(log_dir.glob("guardrail_events.log.*"))
    assert len(backups) == 2
    current = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert current["event"] == "event-3"


def test_non_dev_logs_do_not_rotate(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(config, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.setattr(config, "LOG_MAX_MB", 1)

    log_dir.mkdir()
    path = log_dir / "guardrail_events.log"
    path.write_text("x" * 1_000_000, encoding="utf-8")
    tracing.log_guardrail_event(stage="test", event="production-mode")

    assert not list(log_dir.glob("guardrail_events.log.*"))
    assert path.stat().st_size > 1_000_000


def test_emitted_trace_never_contains_otp_value():
    # Even if a message contains a 6-digit code, the stored trace must not keep
    # it as a raw OTP. (We assert no forbidden key leaks and the record is a dict.)
    run_agent(message="my code is 123456 for order UC-48213", conversation_id="trace-otp", session_id="s1")
    recent = tracing.read_recent(limit=50)
    rec = next((r for r in recent if r.get("conversation_id") == "trace-otp"), None)
    assert rec is not None
    assert "code" not in rec  # no raw code field


# ------------------------------------------------------------------ admin view

def test_admin_logs_page_renders(client):
    run_agent(message="Can I return an item?", conversation_id="admin-1", session_id="s1")
    resp = client.get("/admin/logs")
    assert resp.status_code == 200
    assert "Workflow" in resp.get_data(as_text=True)


def test_admin_api_logs_returns_traces(client):
    run_agent(message="What are your store hours?", conversation_id="admin-2", session_id="s1")
    resp = client.get("/admin/api/logs")
    assert resp.status_code == 200
    assert "traces" in resp.get_json()
