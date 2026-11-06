"""LangGraph agent state.

A single mutable dict flows through the graph nodes. Keys are grouped by the
stage that populates them. Session/auth context is provided by the caller
(the Flask chat route) and is authoritative - the LLM never sets it.
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # ---- inputs (provided by the caller) ----
    message: str
    conversation_id: str
    session_id: str
    # Authoritative verified-session context, set by backend, not the LLM.
    authenticated: bool
    authenticated_customer_id: str | None
    history: list[dict]  # [{role, content}, ...] prior turns (Phase 6)
    # Pending account request carried across OTP and order-reference prompts.
    pending_intent: str | None
    pending_order_ref: str | None
    awaiting_order_ref: bool

    # ---- input guardrails ----
    input_flags: list[str]
    injection_detected: bool
    contains_sensitive_pii: bool
    pii_warning: str | None
    blocked: bool
    confirmed: bool  # customer confirmed a sensitive operation
    abuse_count: int
    unresolved_attempts: int
    tool_failures: int
    calm_abuse_reply: bool

    # ---- detection stage ----
    language: str
    intent: str
    confidence: float

    # ---- routing decisions ----
    authentication_required: bool
    needs_account_data: bool

    # ---- tool stage ----
    order_ref: str | None
    tool_results: dict[str, Any]
    tool_names_used: list[str]

    # ---- RAG stage (Phase 5) ----
    source_references: list[str]
    retrieved_passages: list[dict]

    # ---- output stage ----
    reply: str
    escalate: bool
    escalation_reason: str | None
    ticket_reference: str | None
    # Category to file the ticket under (derived from intent).
    issue_category: str
    # Conversation summary carried into the escalation ticket.
    issue_summary: str | None

    # ---- output guardrails ----
    output_violations: list[str]

    # ---- tracing ----
    workflow_steps: list[str]
    model: str | None
    model_latency_ms: int
