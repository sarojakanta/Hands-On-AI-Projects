"""Human support dashboard routes.

A separate Flask surface for support agents to triage escalated tickets. It
lists/filters tickets, shows full handoff context + conversation history, and
lets an agent assign, update status, add internal notes, and record a
resolution. Reads/writes go through ticket_service (provider-agnostic).

This is a demo dashboard with no auth of its own; in production it would sit
behind staff authentication.
"""

from __future__ import annotations

import json

from flask import Blueprint, render_template, request, jsonify, redirect, url_for

from app.services import ticket_service
from app.models.ticket import TICKET_STATUSES
from app.memory.conversation_memory import conversation_memory

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.get("/")
def index():
    """List tickets, optionally filtered by ?status=OPEN|IN_PROGRESS|..."""
    status = (request.args.get("status") or "").strip().upper() or None
    if status and status not in TICKET_STATUSES:
        status = None
    tickets = ticket_service.list_tickets(status=status)

    # Simple counts for the status tabs.
    all_tickets = ticket_service.list_tickets()
    counts = {s: 0 for s in TICKET_STATUSES}
    for t in all_tickets:
        counts[t["status"]] = counts.get(t["status"], 0) + 1

    return render_template(
        "dashboard.html",
        tickets=tickets,
        counts=counts,
        total=len(all_tickets),
        active_status=status or "ALL",
        statuses=TICKET_STATUSES,
    )


@dashboard_bp.get("/ticket/<ticket_id>")
def ticket_detail(ticket_id: str):
    ticket = ticket_service.get_ticket(ticket_id)
    if not ticket:
        return render_template("dashboard_detail.html", ticket=None, history=[]), 404

    # Pull conversation history from memory so the human sees the full thread.
    history: list[dict] = []
    if ticket.get("conversation_id"):
        convo = conversation_memory.load(ticket["conversation_id"])
        history = convo.messages

    # Pretty-print tool results if present.
    tool_results = None
    if ticket.get("tool_results"):
        try:
            tool_results = json.dumps(json.loads(ticket["tool_results"]), indent=2)
        except (json.JSONDecodeError, TypeError):
            tool_results = ticket["tool_results"]

    return render_template(
        "dashboard_detail.html",
        ticket=ticket,
        history=history,
        tool_results=tool_results,
        statuses=TICKET_STATUSES,
    )


@dashboard_bp.post("/ticket/<ticket_id>/update")
def update_ticket(ticket_id: str):
    """Update status / assignment / notes / resolution from the detail form."""
    fields = {
        "status": request.form.get("status"),
        "assigned_agent": request.form.get("assigned_agent"),
        "internal_notes": request.form.get("internal_notes"),
        "resolution": request.form.get("resolution"),
        "priority": request.form.get("priority"),
    }
    # Drop blanks so we don't overwrite with empty values.
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    updated = ticket_service.update_ticket(ticket_id, **fields)
    if updated is None:
        return jsonify({"error": "Ticket not found."}), 404
    return redirect(url_for("dashboard.ticket_detail", ticket_id=ticket_id))


# --- JSON API variants (handy for tests / integrations) ---

@dashboard_bp.get("/api/tickets")
def api_tickets():
    status = (request.args.get("status") or "").strip().upper() or None
    if status and status not in TICKET_STATUSES:
        status = None
    return jsonify({"tickets": ticket_service.list_tickets(status=status)})


@dashboard_bp.get("/api/tickets/<ticket_id>")
def api_ticket(ticket_id: str):
    ticket = ticket_service.get_ticket(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found."}), 404
    return jsonify(ticket)


@dashboard_bp.post("/api/tickets/<ticket_id>")
def api_update_ticket(ticket_id: str):
    data = request.get_json(silent=True) or {}
    allowed = {"status", "assigned_agent", "internal_notes", "resolution", "priority"}
    fields = {k: v for k, v in data.items() if k in allowed and v not in (None, "")}
    updated = ticket_service.update_ticket(ticket_id, **fields)
    if updated is None:
        return jsonify({"error": "Ticket not found."}), 404
    return jsonify(updated)
