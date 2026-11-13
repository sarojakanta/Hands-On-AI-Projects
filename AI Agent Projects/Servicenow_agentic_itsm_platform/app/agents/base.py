"""Agent framework: base class, shared context and the execution wrapper.

Agents are pure-ish functions ``run(ctx, state) -> partial_update``. They never
build LLM clients (they use ``ctx.llm``), never mutate ``state`` and never write
to ServiceNow (``ctx.servicenow`` is a read-only view). ``run_agent`` adds
retries, timing and input/output capture around every invocation.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import settings
from app.security.sanitize import mask_secrets

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    llm: Any                                  # LLMManager
    knowledge: Any                            # KnowledgeRetriever-like (search())
    servicenow: Any                           # read-only ServiceNow client
    execution_id: str | None = None
    agent_configs: dict[str, dict] = field(default_factory=dict)
    sleep: Callable[[float], None] = time.sleep
    force_llm: bool = False                   # exercise the LLM path even with the mock provider (tests)

    def config(self, name: str) -> dict:
        return self.agent_configs.get(name, {})

    def enabled(self, name: str) -> bool:
        return bool(self.config(name).get("enabled", True))

    def retries(self, name: str) -> int:
        return int(self.config(name).get("max_retries", settings.max_agent_retries))

    def llm_kwargs(self, name: str) -> dict:
        """Per-agent provider/model overrides plus accounting identifiers."""
        cfg = self.config(name)
        return {"provider": cfg.get("llm_provider") or None, "model": cfg.get("llm_model") or None,
                "agent": name, "execution_id": self.execution_id}

    @property
    def llm_enabled(self) -> bool:
        if self.llm is None:
            return False
        return self.force_llm or not getattr(self.llm, "is_mock", True)


class BaseAgent(ABC):
    name = "base"
    description = ""
    critical = False                # a final failure of a critical agent aborts the workflow
    input_keys: tuple[str, ...] = ()

    @abstractmethod
    def run(self, ctx: AgentContext, state: dict) -> dict:
        """Return a partial state update. Must not mutate ``state``."""
        raise NotImplementedError


def summarize(value: Any, limit: int = 200) -> Any:
    """Small JSON-safe summary of a state value for the execution history."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return mask_secrets(value)[:limit]
    if isinstance(value, (list, tuple)):
        return {"count": len(value)}
    if isinstance(value, dict):
        return {"keys": sorted(map(str, value))[:12]}
    return str(type(value).__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def history_entry(agent: str, status: str, *, attempt: int = 1, input_: dict | None = None,
                  output: dict | None = None, error: str | None = None, duration_ms: float = 0.0,
                  started_at: str | None = None) -> dict:
    return {"agent": agent, "attempt": attempt, "status": status, "input": input_ or {},
            "output": output or {}, "error": error, "duration_ms": round(duration_ms, 2),
            "started_at": started_at or _now()}


def run_agent(agent: BaseAgent, ctx: AgentContext, state: dict) -> dict:
    """Execute an agent with retries and full history capture.

    Always returns a state update; it never raises. On final failure the update
    records ``failed``/``errors`` so the supervisor can recover or abort.
    """
    attempts_allowed = 1 + max(0, ctx.retries(agent.name))
    input_summary = {k: summarize(state.get(k)) for k in agent.input_keys}
    entries: list[dict] = []
    last_error = ""
    for attempt in range(1, attempts_allowed + 1):
        started, t0 = _now(), time.perf_counter()
        try:
            update = agent.run(ctx, state) or {}
            duration = (time.perf_counter() - t0) * 1000
            entries.append(history_entry(
                agent.name, "success", attempt=attempt, input_=input_summary,
                output={k: summarize(v) for k, v in update.items() if k not in ("trace",)},
                duration_ms=duration, started_at=started))
            update = dict(update)
            update["history"] = entries
            return update
        except Exception as exc:  # noqa: BLE001 - boundary: convert to recorded failure
            duration = (time.perf_counter() - t0) * 1000
            last_error = mask_secrets(f"{type(exc).__name__}: {exc}")[:500]
            logger.warning("Agent %s attempt %d/%d failed: %s", agent.name, attempt, attempts_allowed, last_error)
            entries.append(history_entry(agent.name, "failed", attempt=attempt, input_=input_summary,
                                         error=last_error, duration_ms=duration, started_at=started))
            if attempt < attempts_allowed:
                ctx.sleep(min(0.2 * attempt, 1.0))
    return {
        "history": entries,
        "failed": [agent.name],
        "errors": [f"{agent.name}: {last_error}"],
        "trace": [f"{agent.name}: FAILED after {attempts_allowed} attempt(s) ({last_error})"],
    }
