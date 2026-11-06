"""Customer authentication REST endpoints (mock OTP).

Endpoints:
  POST /api/auth/initiate  - identify customer by order/customer ref, send OTP.
  POST /api/auth/verify    - verify submitted OTP, create verified session.
  GET  /api/auth/status    - report whether the current session is verified.

The browser session id is stored in a signed Flask session cookie so OTP
challenges and verified state are bound to the exact browser session. The OTP
value is only ever surfaced in dev mode (via the provider), never otherwise.
"""

from __future__ import annotations

import uuid

from flask import Blueprint, request, jsonify, session, current_app, abort

from app.auth.auth_service import AuthService
from app.auth.session_manager import session_manager

auth_bp = Blueprint("auth", __name__)
_auth_service = AuthService()


def _session_id() -> str:
    """Return a stable per-browser session id, creating one if needed."""
    sid = session.get("sid")
    if not sid:
        sid = str(uuid.uuid4())
        session["sid"] = sid
    return sid


@auth_bp.post("/api/auth/initiate")
def initiate():
    data = request.get_json(silent=True) or {}
    order_ref = (data.get("order_ref") or "").strip() or None
    customer_ref = (data.get("customer_ref") or "").strip() or None
    sid = _session_id()

    result = _auth_service.initiate(
        session_id=sid, order_ref=order_ref, customer_ref=customer_ref
    )

    if not result.success:
        return jsonify({"success": False, "message": result.message}), 400

    payload = {
        "success": True,
        "message": result.message,
        # customer_id is an opaque business reference, not PII; needed by the
        # client to submit verification against the right challenge.
        "customer_id": result.customer_id,
        "masked_destination": result.masked_destination,
        "channel": result.channel,
    }
    # Expose the dev OTP to the UI ONLY in dev mode.
    if current_app.config.get("DEV_MODE") and result.dev_code:
        payload["dev_otp"] = result.dev_code

    return jsonify(payload)


@auth_bp.post("/api/auth/verify")
def verify():
    data = request.get_json(silent=True) or {}
    customer_id = (data.get("customer_id") or "").strip()
    code = (data.get("code") or "").strip()
    sid = _session_id()

    if not customer_id or not code:
        return (
            jsonify({"success": False, "message": "Customer ID and code are required."}),
            400,
        )

    result = _auth_service.verify(session_id=sid, customer_id=customer_id, code=code)
    status_code = 200 if result.success else 400
    return (
        jsonify(
            {
                "success": result.success,
                "status": result.status,
                "attempts_remaining": result.attempts_remaining,
                "message": result.message,
            }
        ),
        status_code,
    )


@auth_bp.get("/api/auth/status")
def status():
    sid = _session_id()
    record = session_manager.get(sid)
    return jsonify(
        {
            "verified": record is not None,
            "customer_id": record.customer_id if record else None,
        }
    )


@auth_bp.post("/api/auth/dev-clear-session")
def dev_clear_session():
    """Clear this browser's app session in development mode only."""
    if not current_app.config.get("DEV_MODE"):
        abort(404)

    sid = session.get("sid")
    if sid:
        session_manager.clear(sid)
    session.clear()
    return jsonify({"success": True})
