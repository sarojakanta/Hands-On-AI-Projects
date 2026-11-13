"""Shared, JSON-serialisable workflow state.

Fields annotated with ``operator.add`` are append-only reducers so that agents
running in parallel can contribute without overwriting each other. Every agent
returns a *partial* update; nothing mutates the state in place.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class ITSMState(TypedDict, total=False):
    # --- input ---
    workflow: str
    record: dict                      # ITSMRecord.model_dump()
    diagnostics: dict                 # optional operator-supplied evidence
    disabled_agents: list[str]

    # --- classification / priority ---
    category: str
    affected_service: str
    business_impact: str
    priority: str
    priority_rationale: str

    # --- investigation / knowledge ---
    evidence: list[dict]              # {"text": str, "source": str}
    missing_information: list[str]
    insufficient_information: bool
    needs_more_investigation: bool
    similar_incidents: list[dict]
    knowledge_results: list[dict]     # {"title","source","excerpt","score","metadata"}
    knowledge_conflicts: list[dict]

    # --- RCA / resolution / change / verification ---
    hypotheses: list[dict]            # always status="unverified" unless confirmed externally
    root_cause: str
    root_cause_status: str            # unverified_hypothesis | insufficient_evidence | not_applicable
    confidence: float
    corrective_actions: list[str]
    preventive_actions: list[str]
    recommendations: list[str]
    proposed_actions: list[dict]      # require human approval; never executed by agents
    change_assessment: dict
    verification: dict

    # --- orchestration (append-only; safe for parallel agents) ---
    security_flags: Annotated[list[str], operator.add]  # written by investigation AND knowledge in parallel
    history: Annotated[list[dict], operator.add]
    trace: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    failed: Annotated[list[str], operator.add]
    skipped: Annotated[list[str], operator.add]
    rerun: Annotated[list[str], operator.add]
    handoffs: Annotated[list[dict], operator.add]
    supervisor_log: Annotated[list[dict], operator.add]

    # --- orchestration (last write wins; written only by the supervisor) ---
    next: list[str]
    step_count: int
    status: str                       # running | completed | partial | failed
    stop_reason: str


def initial_state(record: dict, workflow: str, diagnostics: dict | None = None,
                  disabled_agents: list[str] | None = None) -> ITSMState:
    return ITSMState(
        workflow=workflow, record=record, diagnostics=diagnostics or {},
        disabled_agents=disabled_agents or [], history=[], trace=[], errors=[], failed=[],
        skipped=[], rerun=[], handoffs=[], supervisor_log=[], next=[], step_count=0, status="running",
    )
