"""Customer memory rights endpoints (M-4).

Lets a customer see, correct, and delete their stored memory. All endpoints
require a VERIFIED session for the customer being acted on (reusing the same
session-manager check the tools use), so one customer can never view or delete
another's memory (M-3). Requests complete immediately, well within the spec's
30-day SLA.
"""

from __future__ import annotations

import uuid

from flask import Blueprint, request, jsonify, session

from app.services import customer_memory_service
from app.auth.session_manager import session_manager

memory_bp = Blueprint("memory", __name__)


def _session_id() -> str:
    sid = session.get("sid")
    if not sid:
        sid = str(uuid.uuid4())
        session["sid"] = sid
    return sid


def _verified_customer() -> str | None:
    """Return the customer id this session is verified for, or None."""
    record = session_manager.get(_session_id())
    return record.customer_id if record else None


@memory_bp.get("/api/memory")
def view_memory():
    """See everything stored for the verified customer."""
    customer_id = _verified_customer()
    if not customer_id:
        return jsonify({"error": "Identity verification required."}), 401
    return jsonify(customer_memory_service.export_memory(customer_id))


@memory_bp.put("/api/memory/preferences")
def update_preferences():
    """Correct preferences (preferred language, contact channel)."""
    customer_id = _verified_customer()
    if not customer_id:
        return jsonify({"error": "Identity verification required."}), 401
    data = request.get_json(silent=True) or {}
    pref = customer_memory_service.set_preference(
        customer_id=customer_id,
        preferred_language=data.get("preferred_language"),
        contact_channel=data.get("contact_channel"),
    )
    return jsonify(pref)


@memory_bp.delete("/api/memory")
def delete_memory():
    """Delete all stored memory for the verified customer."""
    customer_id = _verified_customer()
    if not customer_id:
        return jsonify({"error": "Identity verification required."}), 401
    result = customer_memory_service.delete_memory(customer_id)
    return jsonify({"deleted": result, "message": "Your stored memory has been deleted."})
