"""LangGraph node functions for the Customer Support Agent.

Each node takes and returns the AgentState dict. Nodes are intentionally small
and testable. Security-relevant decisions (authentication_required, escalation
triggers) are enforced here in deterministic Python, not left solely to the
LLM, per the spec.

The LLM is used for (a) intent/language detection and (b) response wording. If
the LLM is not configured, nodes fall back to deterministic heuristics so the
workflow still runs end-to-end offline.
"""

from __future__ import annotations

import logging
import re

from app.agents import prompts
from app.agents.schemas import (
    normalize_intent,
    ACCOUNT_INTENTS,
    POLICY_INTENTS,
)
from app.agents.state import AgentState
from app.services.llm_service import llm_service, LLMNotConfiguredError
from app.mcp.client import mcp_client
from app.rag.retriever import hybrid_retriever
from app.guardrails import input_guardrails, output_guardrails, tool_guardrails

logger = logging.getLogger(__name__)

# Minimum fused retrieval score for a passage to count as "supporting". Below
# this, the agent must admit uncertainty and offer human support (rule 4).
_RAG_MIN_SCORE = 0.15


# Phrases that force immediate escalation (spec rule 6/7). Enforced in code.
_ESCALATION_TRIGGERS = (
    "lawyer", "legal", "sue", "lawsuit", "attorney",
    "consumer court", "consumer forum",
    "injury", "injured", "hurt", "safety", "unsafe", "dangerous",
    "fraud", "scam", "chargeback", "dispute the charge",
)

# Confidence below which we hand off rather than risk a wrong answer.
_CONFIDENCE_ESCALATION_THRESHOLD = 0.25
_HUMAN_REQUEST = (
    "human", "agent", "representative", "real person", "speak to someone",
    "talk to a person", "connect me to a person", "connect me to someone",
    "talk to a human", "speak to a human", "live agent", "to a person",
)

_ORDER_REF_RE = re.compile(r"\b([A-Z]{2,5}-\d{3,})\b", re.IGNORECASE)
_CUSTOMER_REF_RE = re.compile(r"\b(CUST[-_]\d+)\b", re.IGNORECASE)

# Lightweight keyword map for deterministic intent fallback.
# ORDER: specific POLICY/FAQ phrases are listed BEFORE the generic account
# keywords ("order"/"track") so general how-does-it-work questions route to RAG
# rather than being treated as an account lookup.
_INTENT_KEYWORDS = [
    # --- policy / FAQ phrasings (matched first) ---
    ("SHIPPING_POLICY", (
        "shipping", "delivery charge", "shipping cost", "free shipping",
        "remote pincode", "remote pin code", "ship to", "delivery take",
        "how long does delivery", "delivery time",
    )),
    ("DELIVERY_POLICY", (
        "delivery attempt", "how many delivery", "failed delivery",
        "redelivery", "re-delivery", "delivery policy",
    )),
    ("RETURN_POLICY", (
        "return", "refund policy", "send it back", "return window",
        "return in store", "return in a physical store", "return an online order",
    )),
    ("EXCHANGE_POLICY", ("exchange", "swap", "replace")),
    ("WARRANTY_POLICY", ("warranty", "guarantee", "covered under")),
    ("STORE_HOURS", ("hours", "open", "opening", "closing", "when do you open")),
    ("STORE_LOCATOR", ("store", "location", "nearest", "branch", "outlet")),
    # FAQ: "how do I track my order" is a how-to answered by the FAQ, not a
    # live account lookup.
    ("FAQ", ("how do i track", "how can i track", "how to track", "track my order")),
    # --- account-scoped (need verification) ---
    ("DELIVERY_DELAY", ("late", "delayed", "delay", "still not here", "hasn't arrived", "not arrived")),
    ("ADDRESS_CHANGE", (
        "change address", "update address", "wrong address", "new address",
        "change my address", "change my delivery address", "update my address",
        "delivery address", "shipping address",
    )),
    ("SHIPMENT_STATUS", ("tracking", "shipment", "shipped", "out for delivery", "courier")),
    ("ORDER_STATUS", ("order", "where is my", "my order", "order status")),
    ("HUMAN_SUPPORT", _HUMAN_REQUEST),
]


def _record(state: AgentState, step: str) -> None:
    state.setdefault("workflow_steps", []).append(step)


def _extract_customer_ref(text: str) -> str | None:
    m = _CUSTOMER_REF_RE.search(text or "")
    return m.group(1).upper() if m else None


def _extract_order_ref(text: str) -> str | None:
    """Extract an order reference without mistaking a customer ID for one."""
    m = _ORDER_REF_RE.search(text or "")
    if not m:
        return None
    candidate = m.group(1).upper()
    return None if _CUSTOMER_REF_RE.fullmatch(candidate) else candidate


def _extract_new_address(text: str) -> str | None:
    """Best-effort extraction of a new address from an address-change message.

    Looks for 'to <address>' or 'new address is <address>'. Returns None if no
    address is clearly present (the response node will then ask for one).
    """
    patterns = [
        r"new address (?:is|:)?\s*(.+)$",
        r"change (?:my )?(?:delivery |shipping )?address to\s+(.+)$",
        r"\bto\s+(.+)$",
    ]
    for pat in patterns:
        m = re.search(pat, (text or "").strip(), re.IGNORECASE)
        if m:
            candidate = m.group(1).strip(" .")
            if len(candidate) >= 5:
                return candidate
    return None


# Greetings / small talk get a warm reply rather than the "I'm not sure"
# fallback. Matched as whole words on short messages so "hi" never matches
# inside words like "shipping".
_GREETING_WORDS = {
    "hi", "hello", "hey", "heya", "hiya", "yo", "namaste", "hola",
    "good morning", "good afternoon", "good evening", "greetings",
    "thanks", "thank you", "thankyou", "ok", "okay", "bye", "goodbye",
    "how are you", "whats up", "what's up", "sup",
}
_CLOSING_PHRASES = {
    "thanks", "thank you", "thankyou", "many thanks", "thanks a lot",
    "thank you so much", "no thanks", "no thank you", "nothing else",
    "nothing else thanks", "that is all", "that's all", "all good",
    "i am done", "i'm done", "thanks bye", "thank you bye",
    "okay thanks", "ok thanks", "okay thank you", "ok thank you",
    "alright thanks", "alright thank you", "got it thanks",
    "got it thank you", "yes thanks", "yes thank you",
}


def _normalize_small_talk(message: str) -> str:
    text = (message or "").strip().lower()
    text = re.sub(r"[^a-z'\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_closing(message: str) -> bool:
    return _normalize_small_talk(message) in _CLOSING_PHRASES


def _is_greeting(message: str) -> bool:
    text = (message or "").strip().lower().strip(" !.?,")
    if not text or len(text) > 30:
        return False
    if text in _GREETING_WORDS:
        return True
    words = set(re.findall(r"[a-z']+", text))
    # Filler words that commonly accompany a greeting.
    filler = {"there", "team", "support", "a", "you", "u", "please", "pls"}
    allowed = {w for g in _GREETING_WORDS for w in g.split()} | filler
    # Short message dominated by greeting tokens, with at least one real
    # greeting word present.
    greeting_core = {w for g in _GREETING_WORDS for w in g.split()}
    return bool(words) and words <= allowed and bool(words & greeting_core)


def _keyword_intent(message: str) -> str:
    if _is_closing(message) or _is_greeting(message):
        return "GREETING"
    low = (message or "").lower()
    for intent, keywords in _INTENT_KEYWORDS:
        if any(k in low for k in keywords):
            return intent
    return "OTHER"


# ---------------------------------------------------------- input guardrails

def apply_input_guardrails(state: AgentState) -> AgentState:
    """First node: run deterministic input checks on the raw message.

    Sets ``blocked`` + a safe reply when the input must be rejected outright
    (empty/too long). Otherwise records flags (injection/abuse/PII) that later
    nodes and the output guardrail use.
    """
    _record(state, "apply_input_guardrails")
    result = input_guardrails.check_input(state.get("message", ""))

    state["input_flags"] = result.flags
    state["injection_detected"] = result.injection_detected
    state["contains_sensitive_pii"] = result.contains_sensitive_pii
    state["pii_warning"] = result.pii_warning

    # Log input guardrail events (injection, abuse, pasted PII) for audit.
    from app.observability import tracing

    conv_id = state.get("conversation_id")
    for flag in result.flags:
        tracing.log_guardrail_event(stage="input", event=flag, conversation_id=conv_id)

    if not result.ok:
        state["blocked"] = True
        state["reply"] = result.message or "I couldn't process that message."
        state["intent"] = "OTHER"
        state["language"] = "en"
        state["confidence"] = 0.0
    else:
        state["blocked"] = False
    return state


# -------------------------------------------------------------- detection node

def detect_intent_and_language(state: AgentState) -> AgentState:
    """Detect language + intent. Uses the LLM when configured, else heuristics."""
    message = state.get("message", "")
    _record(state, "detect_intent_and_language")

    intent = "OTHER"
    language = "en"
    confidence = 0.4

    if llm_service.configured:
        try:
            parsed, result = llm_service.structured(prompts.detection_prompt(message))
            intent = normalize_intent(parsed.get("intent"))
            language = (parsed.get("language") or "en").strip() or "en"
            try:
                confidence = float(parsed.get("confidence", 0.6))
            except (TypeError, ValueError):
                confidence = 0.6
            state["model"] = result.model
            state["model_latency_ms"] = state.get("model_latency_ms", 0) + result.latency_ms
        except LLMNotConfiguredError:
            intent = _keyword_intent(message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM intent detection failed, using heuristics: %s", exc)
            intent = _keyword_intent(message)
    else:
        intent = _keyword_intent(message)
        confidence = 0.5

    # Courtesy and closing phrases are deterministic conversational controls,
    # not unsupported questions. Override an LLM's OTHER classification so a
    # simple "no thank you" always receives an appropriate acknowledgement.
    if _is_closing(message) or _is_greeting(message):
        intent = "GREETING"
        confidence = max(confidence, 0.95)

    order_ref = _extract_order_ref(message)
    customer_ref = _extract_customer_ref(message)
    pending_intent = state.get("pending_intent")

    # A customer reference identifies who is verifying, not which order to
    # fetch. Treat a bare customer ID as an account request so it reaches the
    # OTP gate, but never place it in ``order_ref`` or call order tools with it.
    if customer_ref and intent in {"OTHER", "FAQ"}:
        intent = (
            pending_intent
            if pending_intent in ACCOUNT_INTENTS
            else "ORDER_STATUS"
        )
        confidence = max(confidence, 0.9)

    # An explicit order reference always indicates an account-scoped lookup
    # when detection is otherwise unclear or labels it as a generic tracking
    # FAQ. Restore a pending account intent when available; otherwise default
    # to order status. This deterministic override ensures identifiers such as
    # "UC-48230" cannot bypass the OTP gate because of an LLM misclassification.
    if order_ref and intent in {"OTHER", "FAQ"}:
        intent = (
            pending_intent
            if pending_intent in ACCOUNT_INTENTS
            else "ORDER_STATUS"
        )
        confidence = max(confidence, 0.9)

    pending_order_ref = state.get("pending_order_ref")
    if _extract_customer_ref(pending_order_ref or ""):
        # Also recover safely from conversation state written by older builds
        # that incorrectly stored CUST-* as an order reference.
        pending_order_ref = None

    state["intent"] = intent
    state["language"] = language
    state["confidence"] = confidence
    state["order_ref"] = order_ref or (
        pending_order_ref if intent in ACCOUNT_INTENTS else None
    )
    return state


# ---------------------------------------------------- authentication decision

def decide_authentication(state: AgentState) -> AgentState:
    """Decide (in code) whether this request needs a verified session.

    Account intents require authentication. The decision is deterministic and
    never delegated to the LLM. ``authentication_required`` is only True when
    the request needs account data AND the session is not already verified.
    """
    _record(state, "decide_authentication")
    intent = state.get("intent", "OTHER")
    needs_account = intent in ACCOUNT_INTENTS
    authenticated = bool(state.get("authenticated"))

    state["needs_account_data"] = needs_account
    state["authentication_required"] = needs_account and not authenticated
    return state


# ------------------------------------------------------------ escalation check

def check_escalation(state: AgentState) -> AgentState:
    """Decide escalation (in code): sensitive triggers, abuse state machine,
    explicit human requests, low confidence, and repeated unresolved turns."""
    _record(state, "check_escalation")
    message = (state.get("message") or "").lower()
    flags = state.get("input_flags", [])

    def _escalate(reason: str) -> AgentState:
        state["escalate"] = True
        state["escalation_reason"] = reason
        return state

    # 1) Sensitive/legal/safety triggers -> immediate handoff.
    for trigger in _ESCALATION_TRIGGERS:
        if trigger in message:
            return _escalate(
                f"Customer mentioned a sensitive/legal/safety issue ('{trigger}')."
            )

    # 2) Explicit human request.
    if state.get("intent") == "HUMAN_SUPPORT" or any(h in message for h in _HUMAN_REQUEST):
        return _escalate("Customer requested a human agent.")

    # 3) Abuse: calm reply the first time, escalate on repeat.
    if "abuse" in flags:
        count = int(state.get("abuse_count", 0)) + 1
        state["abuse_count"] = count
        if count >= 2:
            return _escalate("Repeated abusive/threatening language; handing to a human.")
        # First time: do not escalate; the response node sends a calm reply.
        state["calm_abuse_reply"] = True

    # 4) Low detection confidence -> don't risk a wrong answer.
    try:
        confidence = float(state.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    if confidence < _CONFIDENCE_ESCALATION_THRESHOLD and state.get("intent") == "OTHER":
        return _escalate("Low confidence in understanding the request.")

    # 5) Customer still unhappy after two unresolved attempts.
    if int(state.get("unresolved_attempts", 0)) >= 2:
        return _escalate("Issue unresolved after multiple attempts.")

    state.setdefault("escalate", False)
    return state


# ------------------------------------------------------------------ tool node

def call_tools(state: AgentState) -> AgentState:
    """Call MCP tools for account/store intents, honouring authorization.

    Account data is only fetched when the session is verified. Store lookups
    are public. All business data flows through MCP tools - never raw SQL.
    """
    _record(state, "call_tools")
    intent = state.get("intent", "OTHER")
    results: dict = state.get("tool_results", {})
    used: list[str] = state.get("tool_names_used", [])

    session_id = state.get("session_id", "")
    customer_id = state.get("authenticated_customer_id")
    order_ref = state.get("order_ref")
    conv_id = state.get("conversation_id")

    # Guarded write: a CONFIRMED, verified address change calls the oms-mcp
    # request_address_change tool. Confirmation is required (handled in the
    # response node); here we only act once confirmed + verified.
    if (
        intent == "ADDRESS_CHANGE"
        and state.get("authenticated")
        and state.get("confirmed")
        and customer_id
        and order_ref
    ):
        new_address = _extract_new_address(state.get("message", ""))
        if new_address:
            resp = mcp_client.call_tool(
                "request_address_change",
                {
                    "session_id": session_id,
                    "customer_id": customer_id,
                    "order_id": order_ref,
                    "new_address": new_address,
                    "idempotency_key": f"{conv_id}:addr:{order_ref}",
                },
                conversation_id=conv_id,
            )
            results["request_address_change"] = resp
            used.append("request_address_change")

    # Account intents: fetch order + shipment only when verified.
    if intent in ACCOUNT_INTENTS and state.get("authenticated") and customer_id and order_ref:
        order_resp = mcp_client.call_tool(
            "get_order",
            {"session_id": session_id, "customer_id": customer_id, "order_id": order_ref},
            conversation_id=conv_id,
        )
        results["get_order"] = order_resp
        used.append("get_order")

        ship_resp = mcp_client.call_tool(
            "get_shipment_status",
            {"session_id": session_id, "customer_id": customer_id, "order_id": order_ref},
            conversation_id=conv_id,
        )
        results["get_shipment_status"] = ship_resp
        used.append("get_shipment_status")

    # Store intents: public.
    elif intent == "STORE_HOURS" and state.get("order_ref") is None:
        # store id may be embedded; otherwise fall back to a search.
        store_match = re.search(r"\bSTORE-\d+\b", state.get("message", ""), re.IGNORECASE)
        if store_match:
            resp = mcp_client.call_tool(
                "get_store_hours",
                {"store_id": store_match.group(0).upper()},
                conversation_id=conv_id,
            )
            results["get_store_hours"] = resp
            used.append("get_store_hours")
    elif intent == "STORE_LOCATOR":
        resp = mcp_client.call_tool(
            "find_store", {"query": state.get("message", "")}, conversation_id=conv_id
        )
        results["find_store"] = resp
        used.append("find_store")

    # Count tool failures this turn; two failures -> escalate (spec trigger).
    failures = sum(
        1
        for r in results.values()
        if isinstance(r, dict) and not r.get("ok") and r.get("error") in ("timeout", "tool_error")
    )
    state["tool_failures"] = failures
    if failures >= 2 and not state.get("escalate"):
        state["escalate"] = True
        state["escalation_reason"] = "A backend service failed repeatedly; handing to a human."

    state["tool_results"] = results
    state["tool_names_used"] = used
    return state


# -------------------------------------------------------------- RAG node

# Intents answerable from policy/FAQ passages via RAG. Deliberately excludes
# OTHER: a question we couldn't classify should not be dressed up as a
# policy-grounded answer. It bypasses retrieval, tools, OTP, and generative
# response calls and receives a fixed scope redirect instead.
_RAG_INTENTS = POLICY_INTENTS | {"STORE_HOURS", "DELIVERY_DELAY", "ADDRESS_CHANGE"}


def retrieve_policy(state: AgentState) -> AgentState:
    """Retrieve supporting policy passages for policy/FAQ questions.

    Populates ``retrieved_passages`` and ``source_references``. If no passage
    clears the support threshold, both stay empty, which signals the response
    node to admit uncertainty and offer human support (rule 4).
    """
    _record(state, "retrieve_policy")
    intent = state.get("intent", "OTHER")
    if intent not in _RAG_INTENTS:
        state.setdefault("retrieved_passages", [])
        state.setdefault("source_references", [])
        return state

    # R-4: scope retrieval to the customer's region/language. Defaults come
    # from config; a verified profile could override these per customer.
    passages = hybrid_retriever.retrieve(
        state.get("message", ""),
        region=state.get("region"),
        language=state.get("language"),
    )
    state["retrieved_passages"] = [p.to_dict() for p in passages]
    # Source references include the policy name + effective date (R-5).
    state["source_references"] = hybrid_retriever.source_references(passages)
    return state


# -------------------------------------------------------------- response node

def _summarize_tool_results(results: dict) -> str:
    """Flatten successful tool data into a compact grounding string."""
    parts: list[str] = []
    for name, resp in (results or {}).items():
        if isinstance(resp, dict) and resp.get("ok"):
            parts.append(f"{name}={resp.get('data')}")
    return "; ".join(parts)


def generate_response(state: AgentState) -> AgentState:
    """Produce the customer-facing reply.

    If authentication is required, return a verification prompt (no account
    data). Otherwise, generate a grounded reply via the LLM, or a deterministic
    reply when the LLM is unavailable.
    """
    _record(state, "generate_response")

    # If input was blocked by guardrails, the reply is already set.
    if state.get("blocked"):
        return state

    # Abuse, first occurrence: one calm, de-escalating reply (not an escalation).
    if state.get("calm_abuse_reply") and not state.get("escalate"):
        state["reply"] = (
            "I want to help and I'll do my best for you. Let's keep it "
            "respectful so I can sort this out. Could you tell me the order or "
            "issue you'd like help with?"
        )
        return state

    # Escalation for sensitive/legal/human-request triggers takes precedence
    # over the auth gate: if we're handing off to a human, say so rather than
    # asking for an OTP first (fixes the earlier precedence quirk).
    if state.get("escalate") and state.get("escalation_reason"):
        state["reply"] = (
            "I understand, and I want to get this to the right person. I'm "
            "connecting you with a human support specialist who will have the "
            "full context of our conversation."
        )
        return state

    # Sensitive operation confirmation (spec §11): address changes must be
    # explicitly confirmed by the customer before we act.
    if tool_guardrails.requires_confirmation(state.get("intent", "")):
        acr = state.get("tool_results", {}).get("request_address_change")
        if acr is not None:
            # The guarded write ran; report its real result (rule 13).
            if acr.get("ok"):
                state["reply"] = (
                    "Your address-change request has been submitted. "
                    "We'll update your order before it ships. Anything else?"
                )
            else:
                state["reply"] = acr.get("message") or (
                    "I couldn't change the address for that order."
                )
            return state
        if not state.get("confirmed"):
            if state.get("authentication_required"):
                state["reply"] = (
                    "Changing a delivery address needs identity verification first. "
                    "Please verify with the one-time code, then confirm the change."
                )
            else:
                state["reply"] = (
                    "I can help change your delivery address. For your security, "
                    "please confirm you want to proceed, and share the new address."
                )
            return state

    # Authentication gate: never reveal account data first (rule 1).
    if state.get("authentication_required"):
        state["reply"] = (
            "To protect your account, I need to verify your identity first. "
            "Please verify using the one-time code to continue."
        )
        return state

    # Verification identifies the customer, but an order-scoped request still
    # needs a specific order. Keep the pending intent so a bare order ID in the
    # next turn can complete this request.
    if (
        state.get("intent") in ACCOUNT_INTENTS
        and state.get("authenticated")
        and not state.get("order_ref")
    ):
        state["reply"] = (
            "Your identity is verified. Please enter the order ID you want to check."
        )
        return state

    # Greetings and conversational closings get context-appropriate replies.
    if state.get("intent") == "GREETING":
        if _is_closing(state.get("message", "")):
            state["reply"] = (
                "You're welcome! I'm glad I could help. Have a great day."
            )
        else:
            state["reply"] = (
                "Hi! I'm the DemoCartService support assistant. I can help with your "
                "orders, delivery, store info, and our policies. What can I do for "
                "you today?"
            )
        return state

    # Out-of-scope/unknown requests take the cheapest safe path. Do not invoke
    # OTP, RAG, MCP tools, or the response-generation LLM for arbitrary topics.
    # Intent detection may have used the classifier, but no answer is generated
    # from model knowledge.
    if state.get("intent") == "OTHER":
        state["reply"] = (
            "I can only help with DemoCartService orders, deliveries, stores, and "
            "policies. Please ask a DemoCartService support question, or request a "
            "human support agent if you need further assistance."
        )
        return state

    # M-5 precedence: account/policy answers are built ONLY from fresh tool
    # results and retrieved policy passages - never from remembered facts. So a
    # stale remembered order status can never override a live tool result.
    tool_context = _summarize_tool_results(state.get("tool_results", {}))
    policy_context = ""
    for p in state.get("retrieved_passages", []) or []:
        policy_context += f"[{p.get('source')}] {p.get('text')}\n"

    if llm_service.configured:
        try:
            messages = prompts.response_prompt(
                message=state.get("message", ""),
                intent=state.get("intent", "OTHER"),
                language=state.get("language", "en"),
                authenticated=bool(state.get("authenticated")),
                tool_context=tool_context,
                policy_context=policy_context,
                history=state.get("history"),
            )
            result = llm_service.chat(messages, temperature=0.2, max_tokens=300)
            state["reply"] = result.text
            state["model"] = result.model
            state["model_latency_ms"] = state.get("model_latency_ms", 0) + result.latency_ms
            return state
        except LLMNotConfiguredError:
            pass  # expected when offline; use deterministic reply
        except Exception as exc:  # noqa: BLE001
            # Log (without the key) so failures are visible, then degrade.
            logger.warning("LLM response generation failed: %s", exc)

    state["reply"] = _fallback_reply(state, tool_context)
    return state


def _first_meaningful_line(text: str) -> str:
    """Return the first non-heading, non-empty line of a chunk, trimmed.

    Skips markdown headings (lines starting with '#') so the deterministic
    citation shows actual policy content rather than a section title.
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:220]
    return (text or "").strip()[:220]


def _fallback_reply(state: AgentState, tool_context: str) -> str:
    """Deterministic reply used when the LLM is not configured/available."""
    intent = state.get("intent", "OTHER")
    if state.get("escalate"):
        return (
            "I understand this needs extra attention. I'm connecting you with a "
            "human support specialist who will have your full conversation."
        )
    if intent in ACCOUNT_INTENTS:
        order_result = state.get("tool_results", {}).get("get_order")
        if isinstance(order_result, dict) and not order_result.get("ok"):
            return order_result.get("message") or (
                "No such order was found for your account. Please check the order ID."
            )
    if intent in ACCOUNT_INTENTS and tool_context:
        order = state.get("tool_results", {}).get("get_order", {}).get("data") or {}
        ship = state.get("tool_results", {}).get("get_shipment_status", {}).get("data") or {}
        status = order.get("status", "unknown")
        eta = ship.get("expected_delivery_date", "not available")
        return (
            f"Your order {order.get('order_id', '')} status is {status}. "
            f"Estimated delivery: {eta}. Let me know if you need anything else."
        )
    passages = state.get("retrieved_passages") or []
    if intent in POLICY_INTENTS or (intent in _RAG_INTENTS and passages):
        if not passages:
            # Rule 4: no supporting passage -> admit uncertainty + offer human.
            return (
                "I'm not certain about that from our policy information, so I "
                "don't want to give you a wrong answer. Would you like me to "
                "connect you with a human support specialist?"
            )
        top = passages[0]
        snippet = _first_meaningful_line(top["text"])
        return (
            f"Based on our {top['source']}: {snippet} "
            f"(Source: {top['source']}.) Can I help with anything else?"
        )
    if intent == "STORE_LOCATOR" or intent == "STORE_HOURS":
        stores = (state.get("tool_results", {}).get("find_store", {}).get("data") or {}).get("stores")
        if stores:
            s = stores[0]
            return f"Our {s['name']} is at {s['address']} ({s['opening_hours']})."
        return "I can help you find a store. Which city are you in?"
    return (
        "I can only help with DemoCartService orders, deliveries, stores, and "
        "policies. Please ask a DemoCartService support question, or request a "
        "human support agent if you need further assistance."
    )


# ---------------------------------------------------------- escalation ticket

def _build_issue_summary(state: AgentState) -> str:
    """Compose a short handoff summary from the conversation + tools.

    Kept concise and free of secrets so a human can take over quickly.
    """
    parts: list[str] = []
    intent = state.get("intent", "OTHER")
    parts.append(f"Intent: {intent}.")
    if state.get("escalation_reason"):
        parts.append(f"Reason: {state['escalation_reason']}")

    # Recent conversation turns (from memory/history) for context.
    history = state.get("history") or []
    recent = [h for h in history if h.get("role") in ("user", "assistant")][-4:]
    if recent:
        snippet = " | ".join(
            f"{h['role']}: {h['content'][:80]}" for h in recent if h.get("content")
        )
        if snippet:
            parts.append(f"Recent: {snippet}")

    tool_ctx = _summarize_tool_results(state.get("tool_results", {}))
    if tool_ctx:
        parts.append(f"Tool data: {tool_ctx[:200]}")
    return " ".join(parts)


def create_escalation_ticket(state: AgentState) -> AgentState:
    """Auto-create a support ticket when the turn is escalated.

    Carries full context for a human handoff (spec §13): customer id,
    conversation id, category, escalation reason, original request,
    conversation summary, relevant tool results, authentication status, and a
    timestamp (set by the ticket store). Avoids creating more than one ticket
    per conversation.
    """
    if not state.get("escalate"):
        return state

    _record(state, "create_escalation_ticket")

    # Guard against duplicate tickets for the same conversation.
    conversation_id = state.get("conversation_id")
    if conversation_id:
        from app.services import ticket_service

        existing = [
            t
            for t in ticket_service.list_tickets()
            if t.get("conversation_id") == conversation_id
        ]
        if existing:
            state["ticket_reference"] = existing[0]["ticket_id"]
            return state

    from app.services import ticket_service

    summary = _build_issue_summary(state)
    state["issue_summary"] = summary
    auth_status = "VERIFIED" if state.get("authenticated") else "UNVERIFIED"
    category = state.get("intent", "OTHER")

    ticket = ticket_service.create_ticket(
        category=category,
        customer_id=state.get("authenticated_customer_id"),
        conversation_id=conversation_id,
        priority="HIGH",
        escalation_reason=state.get("escalation_reason"),
        original_request=state.get("message"),
        summary=summary,
        tool_results=state.get("tool_results") or None,
        authentication_status=auth_status,
    )
    state["ticket_reference"] = ticket["ticket_id"]

    # Append the ticket reference to the customer-facing reply so they get a
    # confirmation + number (the response node already wrote the handoff text).
    ref = ticket["ticket_id"]
    if ref and ref not in (state.get("reply") or ""):
        state["reply"] = (state.get("reply") or "").rstrip()
        state["reply"] += f" Your reference number is {ref}."
    return state


# -------------------------------------------------------- output guardrails

def apply_output_guardrails(state: AgentState) -> AgentState:
    """Final node: sanitize/validate the reply before it leaves the system."""
    _record(state, "apply_output_guardrails")
    tool_data_present = bool(_summarize_tool_results(state.get("tool_results", {})))
    result = output_guardrails.check_output(
        state.get("reply", ""), tool_data_present=tool_data_present
    )
    reply = result.reply

    # Prepend the "don't share" warning when the customer pasted sensitive PII.
    if state.get("pii_warning"):
        reply = f"{state['pii_warning']}\n\n{reply}".strip()

    state["reply"] = reply
    state["output_violations"] = result.violations

    # Log output guardrail violations for audit.
    from app.observability import tracing

    conv_id = state.get("conversation_id")
    for v in result.violations:
        tracing.log_guardrail_event(stage="output", event=v, conversation_id=conv_id)
    if result.violations:
        logger.info("Output guardrail adjustments: %s", result.violations)

    # Track unresolved attempts: a turn is "unresolved" when we neither
    # escalated, nor returned tool-backed data, nor cited a policy. Two such
    # turns in a row trigger escalation next time (handled in check_escalation).
    resolved = (
        state.get("escalate")
        or tool_data_present
        or bool(state.get("source_references"))
        or state.get("authentication_required")
        or state.get("intent") in {"GREETING", "OTHER"}
    )
    if resolved:
        state["unresolved_attempts"] = 0
    else:
        state["unresolved_attempts"] = int(state.get("unresolved_attempts", 0)) + 1
    return state
