"""Temporary: end-to-end run with the real Ollama model against MOCK ServiceNow (no writes, no secrets printed)."""
import os
import tempfile

os.environ["SERVICENOW_MODE"] = "mock"
os.environ["CHROMA_DIR"] = tempfile.mkdtemp()

from app.db.base import configure_database, session_scope
from app.db.models import LLMUsage
from app.services.investigation import InvestigationService

configure_database("sqlite://", create_tables=True)
run_id, r = InvestigationService().investigate("INC0010025", "incident", "ollama", requested_by="smoke")
print("status:", r.status, "| category:", r.category, "| service:", r.affected_service)
print("root cause:", r.probable_root_cause, f"({r.confidence:.0%}, {r.root_cause_status})")
print("hypothesis sources:", [(h["source"], round(h["confidence"], 2)) for h in r.hypotheses])
print("llm-related trace:", [t for t in r.agent_trace if "LLM" in t or "llm" in t or "refined" in t])
print("errors:", r.errors, "| proposed:", [(a["action_type"], a["status"]) for a in r.proposed_actions])
with session_scope() as s:
    rows = s.query(LLMUsage).all()
    print("LLM calls:", [(u.agent_name, u.model, u.prompt_tokens, u.completion_tokens, round(u.latency_ms), u.success) for u in rows])
