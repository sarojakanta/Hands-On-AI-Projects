"""Customer-facing chat routes.

The chat endpoint runs the LangGraph Customer Support Agent. The verified-
session context is read from the backend session manager (keyed by the signed
session cookie), NOT from anything the client sends - so the LLM can never be
tricked into treating an unverified caller as authenticated.
"""

from __future__ import annotations

import uuid

from flask import Blueprint, render_template, request, jsonify, current_app, session

from app.agents.graph import run_agent
from app.auth.session_manager import session_manager

chat_bp = Blueprint("chat", __name__)


def _session_id() -> str:
    """Return a stable per-browser session id, creating one if needed.

    Shares the same 'sid' cookie key as the auth routes so OTP verification and
    chat refer to the same session.
    """
    sid = session.get("sid")
    if not sid:
        sid = str(uuid.uuid4())
        session["sid"] = sid
    return sid


@chat_bp.get("/")
def index():
    """Render the customer support chat interface."""
    return render_template(
        "chat.html",
        dev_mode=current_app.config.get("DEV_MODE", False),
    )


@chat_bp.get("/health")
def health():
    """Simple liveness probe used by tests and local checks."""
    return jsonify({"status": "ok", "phase": 4})


@chat_bp.post("/api/chat")
def chat():
    """Run one agent turn and return the structured response."""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    conversation_id = data.get("conversation_id") or str(uuid.uuid4())

    if not message:
        return jsonify({"error": "Message is required."}), 400

    sid = _session_id()

    # Authoritative auth context from the backend - never trust the client.
    verified = session_manager.get(sid)
    authenticated = verified is not None
    authenticated_customer_id = verified.customer_id if verified else None

    output = run_agent(
        message=message,
        conversation_id=conversation_id,
        session_id=sid,
        authenticated=authenticated,
        authenticated_customer_id=authenticated_customer_id,
    )

    payload = output.to_dict()
    payload["conversation_id"] = conversation_id

    # E-4 shadow mode: the agent drafts, a human sends. Flag the reply as a
    # draft so the client/ops layer knows not to auto-send it.
    if current_app.config.get("SHADOW_MODE"):
        payload["shadow"] = True
        payload["draft"] = True

    return jsonify(payload)


@chat_bp.post("/api/feedback")
def feedback():
    """Record a thumbs up/down for a conversation (CSAT signal)."""
    from app.eval.feedback import record_feedback

    data = request.get_json(silent=True) or {}
    conversation_id = (data.get("conversation_id") or "").strip()
    thumbs_up = bool(data.get("thumbs_up"))
    if not conversation_id:
        return jsonify({"error": "conversation_id is required."}), 400
    record_feedback(conversation_id, thumbs_up)
    return jsonify({"ok": True})
