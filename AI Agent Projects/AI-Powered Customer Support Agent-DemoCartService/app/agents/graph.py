"""LangGraph assembly for the single Customer Support Agent.

Flow (mirrors the spec's workflow diagram):

    detect -> escalation_check -> auth_decision -> [route]
        route == "auth"   -> respond            (ask for OTP, no account data)
        route == "tools"  -> tools -> respond   (verified account / store data)
        route == "direct" -> respond            (policy/other)

Escalation triggers are detected early and surfaced in the final structured
output. The graph is compiled once and reused.
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from app.agents.state import AgentState
from app.agents import nodes
from app.agents.schemas import AgentOutput, ACCOUNT_INTENTS


def _route_after_input(state: AgentState) -> str:
    """If input guardrails blocked the message, skip straight to output."""
    return "blocked" if state.get("blocked") else "detect"


def _route_after_auth(state: AgentState) -> str:
    """Decide the path after the authentication decision."""
    if state.get("authentication_required"):
        return "auth"
    intent = state.get("intent", "OTHER")
    # Account intents (verified) and store lookups use MCP tools.
    if intent in ACCOUNT_INTENTS or intent in ("STORE_HOURS", "STORE_LOCATOR"):
        return "tools"
    # Everything else goes through RAG retrieval (policy/FAQ/other).
    return "rag"


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("input_guard", nodes.apply_input_guardrails)
    graph.add_node("detect", nodes.detect_intent_and_language)
    graph.add_node("escalation_check", nodes.check_escalation)
    graph.add_node("auth_decision", nodes.decide_authentication)
    graph.add_node("tools", nodes.call_tools)
    graph.add_node("rag", nodes.retrieve_policy)
    graph.add_node("respond", nodes.generate_response)
    graph.add_node("escalation_ticket", nodes.create_escalation_ticket)
    graph.add_node("output_guard", nodes.apply_output_guardrails)

    graph.add_edge(START, "input_guard")
    graph.add_conditional_edges(
        "input_guard",
        _route_after_input,
        {"blocked": "respond", "detect": "detect"},
    )
    graph.add_edge("detect", "escalation_check")
    graph.add_edge("escalation_check", "auth_decision")

    graph.add_conditional_edges(
        "auth_decision",
        _route_after_auth,
        {"auth": "respond", "tools": "tools", "rag": "rag"},
    )
    # After tools (account/store), also retrieve policy context, then respond.
    graph.add_edge("tools", "rag")
    graph.add_edge("rag", "respond")
    # After responding, auto-create an escalation ticket when needed, then
    # every reply passes through the output guardrail before leaving.
    graph.add_edge("respond", "escalation_ticket")
    graph.add_edge("escalation_ticket", "output_guard")
    graph.add_edge("output_guard", END)

    return graph.compile()


# Compile once at import.
_COMPILED = build_graph()


def run_agent(
    *,
    message: str,
    conversation_id: str,
    session_id: str,
    authenticated: bool = False,
    authenticated_customer_id: str | None = None,
    history: list[dict] | None = None,
    confirmed: bool = False,
    use_memory: bool = True,
) -> AgentOutput:
    """Run the agent for a single turn and return structured output.

    Conversation memory is loaded before the turn (so prior context is
    available) and updated after (so the next turn remembers this one). Memory
    is isolated per conversation_id and expires with the session TTL.
    """
    from app.memory.conversation_memory import conversation_memory
    from app.guardrails.tool_guardrails import is_confirmation

    convo = None
    if use_memory:
        convo = conversation_memory.load(conversation_id)
        # Prior turns feed the LLM so the customer need not repeat themselves.
        history = history or [dict(m) for m in convo.messages]
        # A sensitive op is "confirmed" if the caller says so OR the customer's
        # message is an affirmative and the prior turn asked for confirmation.
        confirmed = confirmed or is_confirmation(message)

    initial: AgentState = {
        "message": message,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "authenticated": authenticated,
        "authenticated_customer_id": authenticated_customer_id,
        "history": history or [],
        "confirmed": confirmed,
        "pending_intent": convo.pending_intent if convo else None,
        "pending_order_ref": convo.pending_order_ref if convo else None,
        "awaiting_order_ref": convo.awaiting_order_ref if convo else False,
        # Carry the guardrail counters in from memory so the abuse / repeated-
        # unhappy state machine spans turns.
        "abuse_count": convo.abuse_count if convo else 0,
        "unresolved_attempts": convo.unresolved_attempts if convo else 0,
        "tool_failures": 0,
        "tool_results": {},
        "tool_names_used": [],
        "source_references": [],
        "retrieved_passages": [],
        "workflow_steps": [],
    }

    final: AgentState = _COMPILED.invoke(initial)

    output = AgentOutput(
        reply=final.get("reply", ""),
        language=final.get("language", "en"),
        intent=final.get("intent", "OTHER"),
        confidence=float(final.get("confidence", 0.0)),
        escalate=bool(final.get("escalate", False)),
        escalation_reason=final.get("escalation_reason"),
        authentication_required=bool(final.get("authentication_required", False)),
        source_references=final.get("source_references", []),
        ticket_reference=final.get("ticket_reference"),
    )

    if use_memory and convo is not None:
        convo.customer_id = authenticated_customer_id or convo.customer_id
        convo.intent = output.intent
        convo.authenticated = authenticated
        convo.escalated = convo.escalated or output.escalate
        convo.abuse_count = int(final.get("abuse_count", convo.abuse_count))
        convo.unresolved_attempts = int(
            final.get("unresolved_attempts", convo.unresolved_attempts)
        )
        if final.get("issue_summary"):
            convo.issue_summary = final.get("issue_summary")
        current_order_ref = final.get("order_ref")
        convo.active_order = current_order_ref or convo.active_order
        convo.language = output.language or convo.language
        tctx = final.get("tool_results") or {}
        if tctx:
            convo.tool_results_summary = ", ".join(
                f"{k}:{'ok' if isinstance(v, dict) and v.get('ok') else 'err'}"
                for k, v in tctx.items()
            )

        # Keep account requests pending across OTP verification and follow-up
        # messages. Clear them only after a successful order lookup.
        order_lookup = tctx.get("get_order") if isinstance(tctx, dict) else None
        order_lookup_succeeded = bool(
            isinstance(order_lookup, dict) and order_lookup.get("ok")
        )
        if output.intent in ACCOUNT_INTENTS:
            if order_lookup_succeeded:
                convo.pending_intent = None
                convo.pending_order_ref = None
                convo.awaiting_order_ref = False
            else:
                convo.pending_intent = output.intent
                convo.pending_order_ref = current_order_ref or convo.pending_order_ref
                convo.awaiting_order_ref = not bool(convo.pending_order_ref)
        conversation_memory.append_turn(convo, role="user", content=message)
        conversation_memory.append_turn(convo, role="assistant", content=output.reply)
        conversation_memory.save(convo)

        # Episodic memory (per-customer): record a one-paragraph issue summary
        # when the turn escalated, for a verified customer (M-1/M-3).
        if authenticated_customer_id and output.escalate and output.escalation_reason:
            from app.services import customer_memory_service

            customer_memory_service.record_issue(
                customer_id=authenticated_customer_id,
                conversation_id=conversation_id,
                category=output.intent,
                summary=final.get("issue_summary") or output.escalation_reason,
                outcome="ESCALATED",
            )

    # Emit a redacted audit/trace record for this turn (spec §15). Tracing is
    # best-effort and never breaks the request path.
    from app.observability import tracing

    tracing.emit(final, output)

    return output
