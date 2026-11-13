"""LangGraph orchestration: a supervisor node routes to agent nodes dynamically.

supervisor --(conditional)--> one agent | several agents in parallel (Send) | END
every agent --> supervisor   (handoff back for the next decision)
"""
from __future__ import annotations

import logging
from typing import Callable

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from app.agents import AGENT_CLASSES, AgentContext, Supervisor, run_agent
from app.config import settings
from app.workflows.state import ITSMState

logger = logging.getLogger(__name__)


def build_workflow(ctx: AgentContext):
    supervisor = Supervisor(ctx)
    agents = {name: cls() for name, cls in AGENT_CLASSES.items()}

    def make_node(agent):
        return lambda state: run_agent(agent, ctx, state)

    def route(state: ITSMState):
        nxt = state.get("next") or []
        if not nxt:
            return END
        if len(nxt) == 1:
            return nxt[0]
        return [Send(name, state) for name in nxt]  # parallel fan-out

    graph = StateGraph(ITSMState)
    graph.add_node("supervisor", supervisor.decide)
    for name, agent in agents.items():
        graph.add_node(name, make_node(agent))
        graph.add_edge(name, "supervisor")
    graph.set_entry_point("supervisor")
    graph.add_conditional_edges("supervisor", route, [*agents.keys(), END])
    return graph.compile()


def run_workflow(ctx: AgentContext, state: ITSMState,
                 on_step: Callable[[dict], None] | None = None) -> dict:
    """Run to completion, streaming full-state snapshots to ``on_step``.

    ``state`` may be a previously persisted (partial) state: agents already settled
    in its history are not re-run, which gives partial-workflow recovery.
    """
    graph = build_workflow(ctx)
    config = {"recursion_limit": settings.max_workflow_steps * 3 + 10}
    last: dict = dict(state)
    try:
        for snapshot in graph.stream(state, config=config, stream_mode="values"):
            last = snapshot
            if on_step:
                on_step(snapshot)
    except GraphRecursionError:
        logger.error("Workflow aborted by recursion limit")
        last = {**last, "status": "partial", "stop_reason": "recursion_limit",
                "errors": [*last.get("errors", []), "workflow: recursion limit reached"]}
    if last.get("status") == "running":  # graph ended without the supervisor finalising
        last = {**last, "status": "partial" if last.get("failed") else "completed"}
    return last
