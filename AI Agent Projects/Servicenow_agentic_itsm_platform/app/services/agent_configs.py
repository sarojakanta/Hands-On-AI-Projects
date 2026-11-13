"""Agent configuration persistence (enable/disable, retries, per-agent LLM overrides)."""
from __future__ import annotations

from app.agents import agent_catalog
from app.config import settings
from app.db.base import session_scope
from app.db.models import AgentConfig


def seed_agent_configs() -> None:
    """Create a default row for every agent that has none. Existing rows are left untouched."""
    with session_scope() as session:
        for item in agent_catalog():
            if session.get(AgentConfig, item["name"]) is None:
                session.add(AgentConfig(name=item["name"], description=item["description"],
                                        max_retries=settings.max_agent_retries))


def load_agent_configs() -> dict[str, dict]:
    seed_agent_configs()
    with session_scope() as session:
        rows = session.query(AgentConfig).all()
        return {r.name: {"enabled": r.enabled, "max_retries": r.max_retries, "llm_provider": r.llm_provider,
                         "llm_model": r.llm_model, "timeout_seconds": r.timeout_seconds,
                         "description": r.description, "settings": r.settings} for r in rows}
