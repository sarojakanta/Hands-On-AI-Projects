"""Admin routes: inspect workflow audit logs and ticket activity.

A basic observability surface for operators (spec §15). Shows the most recent
redacted trace records and recent ticket activity. Like the dashboard, this is
demo-scoped and would sit behind admin authentication in production.
"""

from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify

from app.observability import tracing
from app.services import ticket_service

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.get("/logs")
def logs():
    limit = request.args.get("limit", default=100, type=int)
    traces = tracing.read_recent(limit=limit)
    tickets = ticket_service.list_tickets()[:25]
    return render_template("admin_logs.html", traces=traces, tickets=tickets)


@admin_bp.get("/api/logs")
def api_logs():
    limit = request.args.get("limit", default=100, type=int)
    return jsonify({"traces": tracing.read_recent(limit=limit)})
