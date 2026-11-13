"""Supervisor: decides which agent(s) run next, based on the live shared state.

The plan per workflow is a *baseline ordering of stages*, not a fixed pipeline:
agents are skipped when not applicable, disabled, or when the state says they
would add nothing; a stage may contain several agents that run in parallel;
and an extra investigation round is triggered only when evidence is weak.
Hard limits (step budget, one re-investigation, critical-failure abort) prevent
runaway loops.
"""
from __future__ import annotations

import time

from app.agents.base import AgentContext, history_entry
from app.config import settings

# A stage is a name or a tuple of names that can run in parallel.
PLANS: dict[str, list] = {
    "incident": ["classification", "priority", ("investigation", "knowledge"), "rca", "resolution", "verification"],
    "problem": ["classification", ("investigation", "knowledge"), "rca", "resolution", "verification"],
    "change": ["classification", "knowledge", "change_management", "resolution", "verification"],
    "request": ["classification", "knowledge", "resolution", "verification"],
}
APPLICABLE = {
    "priority": {"incident"},
    "investigation": {"incident", "problem"},
    "rca": {"incident", "problem"},
    "change_management": {"change"},
}
CRITICAL = {"classification"}
RERUN_ON_WEAK_EVIDENCE = ["investigation", "knowledge", "rca"]


def successes(state: dict, name: str) -> int:
    return sum(1 for h in state.get("history", []) if h["agent"] == name and h["status"] == "success")


def settled(state: dict, name: str, rerun: list[str]) -> bool:
    """An agent is settled once it has run (successfully or not) as often as required."""
    attempts = successes(state, name) + state.get("failed", []).count(name)
    return attempts >= 1 + rerun.count(name)


class Supervisor:
    name = "supervisor"
    description = "Selects and delegates to agents dynamically; enforces limits and handles failures."

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    def skip_reason(self, name: str, state: dict) -> str | None:
        wf = state["workflow"]
        if name in state.get("disabled_agents", []) or (name not in CRITICAL and not self.ctx.enabled(name)):
            return "disabled by configuration"
        if wf not in APPLICABLE.get(name, {wf}):
            return f"not applicable to {wf} workflow"
        if name == "rca" and state.get("insufficient_information"):
            return "information is insufficient; requesting details instead of speculating"
        return None

    def decide(self, state: dict) -> dict:
        started = time.perf_counter()
        step = state.get("step_count", 0) + 1
        wf = state["workflow"]
        rerun = list(state.get("rerun", []))
        update: dict = {"step_count": step, "next": []}
        history: list[dict] = []
        trace: list[str] = []
        new_skips: list[str] = []

        def finish(next_agents: list[str], reason: str, **extra) -> dict:
            update.update(extra)
            update["next"] = next_agents
            update["history"] = history + [history_entry(
                self.name, "success", input_={"step": step},
                output={"next": next_agents, "reason": reason},
                duration_ms=(time.perf_counter() - started) * 1000)]
            update["supervisor_log"] = [{"step": step, "next": next_agents, "reason": reason}]
            update["trace"] = trace + [f"supervisor: {reason}"]
            if new_skips:
                update["skipped"] = new_skips
            return update

        failed_critical = [a for a in state.get("failed", []) if a in CRITICAL]
        if failed_critical:
            return finish([], f"critical agent failed ({', '.join(failed_critical)}); aborting",
                          status="failed", stop_reason="critical_agent_failed")
        if step > settings.max_workflow_steps:
            return finish([], f"step limit {settings.max_workflow_steps} reached; stopping to prevent a runaway loop",
                          status="partial", stop_reason="step_limit")

        # Weak evidence after RCA -> one bounded extra investigation round.
        if (wf in ("incident", "problem") and settled(state, "rca", rerun) and successes(state, "rca") >= 1
                and state.get("needs_more_investigation") and "investigation" not in rerun
                and not state.get("insufficient_information")):
            rerun += RERUN_ON_WEAK_EVIDENCE
            update["rerun"] = RERUN_ON_WEAK_EVIDENCE
            update["handoffs"] = [{"from": "rca", "to": "investigation",
                                   "reason": "low confidence with open information gaps"}]
            trace.append("supervisor: handoff rca -> investigation (low confidence, one extra round allowed)")

        pending: list[str] = []
        for stage in PLANS[wf]:
            names = stage if isinstance(stage, tuple) else (stage,)
            stage_pending = []
            for name in names:
                if settled(state, name, rerun):
                    continue
                reason = self.skip_reason(name, state)
                if reason:
                    if name not in state.get("skipped", []) and name not in new_skips:
                        new_skips.append(name)
                        history.append(history_entry(name, "skipped", output={"reason": reason}))
                        trace.append(f"supervisor: skipped {name} ({reason})")
                    continue
                stage_pending.append(name)
            if stage_pending:
                pending = stage_pending
                break

        if not pending:
            failed = state.get("failed", [])
            status = "partial" if failed else "completed"
            reason = "all required agents settled" + (f"; degraded by failed agent(s): {', '.join(failed)}" if failed else "")
            return finish([], reason, status=status, stop_reason="done")

        last = next((h["agent"] for h in reversed(state.get("history", [])) if h["agent"] != self.name), "start")
        mode = " in parallel" if len(pending) > 1 else ""
        update.setdefault("handoffs", [])
        update["handoffs"] = update["handoffs"] + [{"from": last, "to": ", ".join(pending), "reason": "next stage"}]
        return finish(pending, f"delegating to {', '.join(pending)}{mode}")
