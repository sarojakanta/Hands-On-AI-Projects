"""Structured tracing / audit logging.

Emits one JSON-lines record per agent turn to logs/audit.log, capturing the
workflow for observability and audit (spec §15):

  timestamp, conversation_id, customer_id, intent, language, confidence,
  workflow_steps, rag_references, mcp_tools, tool_results (status only),
  model, model_latency_ms, guardrail decisions (input flags + output
  violations), authentication result, escalation decision, ticket reference.

SECURITY: this logger NEVER records OTP values, passwords, API keys, or raw
PII. A redaction pass scrubs common secret/PII patterns from any string before
it is written, and tool results are reduced to status (ok/error) rather than
their full data payload.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.config import config

_LOCK = threading.Lock()


def _append_log(path: Path, line: str) -> None:
    """Append one JSONL record, rotating bounded files in development mode."""
    config.ensure_dirs()
    with _LOCK:
        if config.DEV_MODE and config.LOG_MAX_MB > 0:
            handler = RotatingFileHandler(
                path,
                maxBytes=config.LOG_MAX_MB * 1_000_000,
                backupCount=max(0, config.LOG_BACKUP_COUNT),
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            try:
                record = logging.LogRecord(
                    name="support_agent",
                    level=logging.INFO,
                    pathname=str(path),
                    lineno=0,
                    msg=line,
                    args=(),
                    exc_info=None,
                )
                handler.emit(record)
            finally:
                handler.close()
            return

        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# Patterns scrubbed from any logged string.
_REDACTIONS = (
    (re.compile(r"\b\d{4,8}\b(?=.*otp|otp.*)", re.IGNORECASE), "[REDACTED_OTP]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{10,}"), "[REDACTED_KEY]"),
    (re.compile(r"gsk_[A-Za-z0-9]{10,}"), "[REDACTED_KEY]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[REDACTED_CARD]"),
    (re.compile(r"\+?\d[\d -]{9,}\d"), "[REDACTED_PHONE]"),
)

# Keys that must never be logged even if present in an input dict.
_FORBIDDEN_KEYS = {"code", "otp", "password", "api_key", "secret", "security_token", "mobile"}


def _redact_str(value: str) -> str:
    out = value
    for pattern, replacement in _REDACTIONS:
        out = pattern.sub(replacement, out)
    return out


def _scrub(value: Any) -> Any:
    """Recursively scrub secrets/PII from a value before logging."""
    if isinstance(value, str):
        return _redact_str(value)
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if k.lower() in _FORBIDDEN_KEYS else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _audit_path() -> Path:
    return Path(config.LOG_DIR) / "audit.log"


def _tool_status_only(tool_results: dict | None) -> dict:
    """Reduce tool results to {tool: ok|error} - never log the data payload."""
    summary: dict[str, str] = {}
    for name, resp in (tool_results or {}).items():
        if isinstance(resp, dict):
            summary[name] = "ok" if resp.get("ok") else (resp.get("error") or "error")
        else:
            summary[name] = "unknown"
    return summary


def build_trace(state: dict, output: Any) -> dict:
    """Build a redacted trace record from the final agent state + output."""
    from app.agents.prompts import PROMPT_VERSION

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt_version": PROMPT_VERSION,
        "conversation_id": state.get("conversation_id"),
        "customer_id": state.get("authenticated_customer_id"),
        "intent": getattr(output, "intent", state.get("intent")),
        "language": getattr(output, "language", state.get("language")),
        "confidence": getattr(output, "confidence", state.get("confidence")),
        "workflow_steps": state.get("workflow_steps", []),
        "rag_references": getattr(output, "source_references", []),
        "mcp_tools": state.get("tool_names_used", []),
        "tool_results": _tool_status_only(state.get("tool_results")),
        "model": state.get("model"),
        "model_latency_ms": state.get("model_latency_ms", 0),
        "input_flags": state.get("input_flags", []),
        "output_violations": state.get("output_violations", []),
        "authenticated": bool(state.get("authenticated")),
        "authentication_required": getattr(output, "authentication_required", False),
        "escalated": getattr(output, "escalate", False),
        "escalation_reason": getattr(output, "escalation_reason", None),
        "ticket_reference": getattr(output, "ticket_reference", None),
        "blocked": bool(state.get("blocked")),
    }
    # Final scrub of the whole record as a safety net.
    return _scrub(record)


def emit(state: dict, output: Any) -> dict:
    """Build, write, and return the trace record for one turn."""
    record = build_trace(state, output)
    try:
        line = json.dumps(record, ensure_ascii=False)
        _append_log(_audit_path(), line)
    except Exception:
        # Tracing must never break the request path.
        pass
    return record


def _tool_audit_path() -> Path:
    return Path(config.LOG_DIR) / "tool_calls.log"


def _guardrail_path() -> Path:
    return Path(config.LOG_DIR) / "guardrail_events.log"


def log_guardrail_event(
    *,
    stage: str,
    event: str,
    conversation_id: str | None = None,
    detail: str | None = None,
) -> None:
    """Append a guardrail-event record (injection, abuse, pii, etc.).

    ``detail`` is scrubbed so no secret/PII value is ever written.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "event": event,
        "conversation_id": conversation_id,
        "detail": _redact_str(detail) if detail else None,
    }
    try:
        _append_log(_guardrail_path(), json.dumps(record, ensure_ascii=False))
    except Exception:
        pass


def read_recent_guardrail_events(limit: int = 100) -> list[dict]:
    path = _guardrail_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out


def log_tool_call(*, session_id: str | None, tool: str, arguments: dict, status: str) -> None:
    """Append a per-tool-call audit record (T-5).

    Logs session id, tool name, MASKED arguments, and result status. Never logs
    secrets or raw PII (arguments are masked by the caller).
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "tool": tool,
        "arguments": arguments,
        "status": status,
    }
    try:
        _append_log(_tool_audit_path(), json.dumps(record, ensure_ascii=False))
    except Exception:
        pass


def read_recent_tool_calls(limit: int = 100) -> list[dict]:
    path = _tool_audit_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out


def read_recent(limit: int = 100) -> list[dict]:
    """Return the most recent trace records (newest first)."""
    path = _audit_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict] = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    records.reverse()
    return records
