"""
app.py
------
Flask web server for the Salesforce AI Agent.

Endpoints
=========
  GET  /                  -> chat UI (index.html)
  POST /api/chat          -> synchronous JSON response
  GET  /api/stream        -> streaming SSE response (EventSource-friendly)
  DELETE /api/history     -> clear conversation memory for a session
  GET  /api/health        -> health / connectivity check
  GET  /api/suggestions   -> pre-built example prompts for the UI
  GET  /api/dashboard     -> live CRM metrics (open cases, pipeline, accounts, leads)
  POST /api/share         -> generate shareable text summary of last result
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from agent import SalesforceAgent
from salesforce_client import SalesforceClient

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", uuid.uuid4().hex)

# One shared agent instance (SalesforceClient is stateless per query;
# ConversationMemory is keyed by session_id so sharing is safe).
_agent = SalesforceAgent()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session_id() -> str:
    """Return the session id from the request (header or query-param)."""
    return (
        request.headers.get("X-Session-Id")
        or request.args.get("session_id")
        or (request.get_json(silent=True) or {}).get("session_id")
        or "default"
    )


def _sse(data: dict[str, Any]) -> str:
    """Format a dict as an SSE `data:` line."""
    return f"data: {json.dumps(data, default=str)}\n\n"


def _cors(response: Response) -> Response:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Session-Id"
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index() -> str:
    return render_template("index.html")


# ------------------------------------------------------------------
# POST /api/chat  -- synchronous (used as fallback / simple clients)
# ------------------------------------------------------------------

@app.route("/api/chat", methods=["POST", "OPTIONS"])
def chat() -> Response:
    if request.method == "OPTIONS":
        return _cors(Response(status=204))

    body: dict[str, Any] = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    session_id = body.get("session_id") or _session_id()

    if not prompt:
        return _cors(jsonify({"error": "prompt is required"})), 400  # type: ignore[return-value]

    logger.info("[%s] prompt: %s", session_id, prompt[:120])
    t0 = time.perf_counter()

    result = _agent.handle_request(prompt, session_id=session_id)

    elapsed = round(time.perf_counter() - t0, 2)
    logger.info("[%s] done in %.2fs -- tools: %s", session_id, elapsed,
                [t["tool"] for t in result.get("tool_calls", [])])

    return _cors(jsonify({
        "answer":     result.get("answer", ""),
        "tool_calls": result.get("tool_calls", []),
        "records":    result.get("records", []),
        "soql":       result.get("soql", ""),
        "object":     result.get("object", ""),
        "elapsed":    elapsed,
        "session_id": session_id,
        "error":      result.get("error"),
    }))


# ------------------------------------------------------------------
# GET /api/stream  -- Server-Sent Events streaming
# ------------------------------------------------------------------

@app.route("/api/stream")
def stream() -> Response:
    """
    Query params:
      prompt     (required) -- the user message
      session_id (optional) -- conversation session key

    Yields SSE events:
      data: {"type": "tool_start",  "tool": "...", "args": {...}}
      data: {"type": "tool_end",    "tool": "...", "summary": "..."}
      data: {"type": "text_chunk",  "text": "..."}
      data: {"type": "done",        "soql": "...", "records": [...], "tool_calls": [...]}
      data: {"type": "error",       "message": "..."}
    """
    prompt = (request.args.get("prompt") or "").strip()
    session_id = request.args.get("session_id") or "default"

    if not prompt:
        def _err():
            yield _sse({"type": "error", "message": "prompt is required"})
        return _cors(Response(stream_with_context(_err()), mimetype="text/event-stream"))

    logger.info("[%s] stream: %s", session_id, prompt[:120])

    def _generate():
        try:
            for ndjson_line in _agent.stream_request(prompt, session_id=session_id):
                try:
                    event = json.loads(ndjson_line.strip())
                    yield _sse(event)
                except json.JSONDecodeError:
                    continue
        except GeneratorExit:
            pass
        except Exception as exc:
            logger.exception("Stream error")
            yield _sse({"type": "error", "message": str(exc)})

    return _cors(Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    ))


# ------------------------------------------------------------------
# DELETE /api/history  -- clear conversation memory
# ------------------------------------------------------------------

@app.route("/api/history", methods=["DELETE", "OPTIONS"])
def clear_history() -> Response:
    if request.method == "OPTIONS":
        return _cors(Response(status=204))

    session_id = _session_id()
    _agent.clear_history(session_id)
    logger.info("[%s] history cleared", session_id)
    return _cors(jsonify({"cleared": True, "session_id": session_id}))


# ------------------------------------------------------------------
# GET /api/health  -- connectivity probe
# ------------------------------------------------------------------

@app.route("/api/health")
def health() -> Response:
    import shutil
    from agent import llm_config_summary

    sf_cli = bool(
        shutil.which("sf")
        or shutil.which("sf.cmd")
        or shutil.which("sf.exe")
        or os.path.exists(r"C:\Program Files\sf\bin\sf.cmd")
    )
    sf_username = bool(
        os.getenv("SF_USERNAME") or os.getenv("SALESFORCE_USERNAME")
    )

    llm = llm_config_summary()
    has_key = llm["has_key"]

    status = "ok" if (sf_cli and sf_username) else "degraded"

    return _cors(jsonify({
        "status":       status,
        "sf_cli":       sf_cli,
        "sf_username":  sf_username,
        # LLM info
        "openai_key":   has_key,          # kept for UI backward-compat
        "llm_key":      has_key,
        "llm_provider": llm["provider"],
        "llm_model":    llm["model"],
        "mode":         "llm" if has_key else "fallback",
        "timestamp":    time.time(),
    }))


# ------------------------------------------------------------------
# GET /api/suggestions  -- example prompts for the UI quick-actions
# ------------------------------------------------------------------

@app.route("/api/suggestions")
def suggestions() -> Response:
    data = [
        {
            "category": "Cases",
            "icon": "\U0001F4C1",   # file folder
            "prompts": [
                "Show me all open high-priority cases",
                "List cases escalated this week",
                "How many open cases do we have per account?",
                "Show all cases with status as New",
            ],
        },
        {
            "category": "Accounts",
            "icon": "\U0001F3E2",   # office building
            "prompts": [
                "List all accounts in the Technology industry",
                "Show accounts in the United States with over 500 employees",
                "Which accounts have the most open opportunities?",
                "Find accounts without a primary contact",
            ],
        },
        {
            "category": "Opportunities",
            "icon": "\U0001F4BC",   # briefcase
            "prompts": [
                "Show opportunities closing this quarter",
                "List deals worth more than $50,000 in Proposal stage",
                "What is the total pipeline value by stage?",
                "Find stalled deals with no activity in 14 days",
            ],
        },
        {
            "category": "Contacts & Leads",
            "icon": "\U0001F465",   # busts in silhouette
            "prompts": [
                "Show me all cases created by me",
                "List all my open cases",
                "Show all contacts",
                "List all leads",
            ],
        },
        {
            "category": "Analytics",
            "icon": "\U0001F4CA",   # bar chart
            "prompts": [
                "How many cases were created this month?",
                "What is the average opportunity amount by stage?",
                "Show case volume by priority",
                "Count open opportunities per owner",
            ],
        },
    ]
    return _cors(jsonify(data))


# ------------------------------------------------------------------
# GET /api/dashboard  -- live CRM metrics
# ------------------------------------------------------------------

@app.route("/api/dashboard")
def dashboard() -> Response:
    """
    Returns a snapshot of key CRM metrics pulled live from Salesforce.
    Uses simple SELECT queries (no GROUP BY aggregates) so it works on
    Developer Edition orgs that restrict aggregate functions via the CLI.
    All counts and breakdowns are derived from the fetched records.
    """
    sf = SalesforceClient()
    metrics: dict[str, Any] = {}

    def _safe_records(soql: str) -> list[dict[str, Any]]:
        try:
            result = sf.query(soql)
            return result.get("records", [])
        except Exception as exc:
            logger.warning("Dashboard query failed [%s]: %s", soql[:60], exc)
            return []

    # __ Cases ______________________________________________________
    # Fetch up to 200 open cases -- enough to derive accurate counts
    open_cases = _safe_records(
        "SELECT Id, CaseNumber, Subject, Status, Priority, CreatedDate, Account.Name "
        "FROM Case WHERE IsClosed = false ORDER BY CreatedDate DESC LIMIT 200"
    )

    # Derive breakdowns from the fetched records
    priority_counts: dict[str, int] = {"High": 0, "Medium": 0, "Low": 0}
    status_counts:   dict[str, int] = {}
    for r in open_cases:
        p = r.get("Priority") or "Low"
        priority_counts[p] = priority_counts.get(p, 0) + 1
        s = r.get("Status") or "Unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    metrics["cases"] = {
        "open_total":  len(open_cases),
        "by_priority": priority_counts,
        "by_status":   status_counts,
    }
    # Most recent 8 for the table
    metrics["recent_cases"] = open_cases[:8]

    # __ Opportunities / Pipeline ____________________________________
    opps = _safe_records(
        "SELECT Id, Name, StageName, Amount, CloseDate, Account.Name "
        "FROM Opportunity WHERE IsClosed = false ORDER BY CloseDate ASC LIMIT 200"
    )

    stage_map: dict[str, dict[str, Any]] = {}
    for r in opps:
        stage = r.get("StageName") or "Unknown"
        amt   = r.get("Amount") or 0
        if stage not in stage_map:
            stage_map[stage] = {"count": 0, "amount": 0.0}
        stage_map[stage]["count"]  += 1
        stage_map[stage]["amount"] += float(amt)

    pipeline_total = sum(v["amount"] for v in stage_map.values())
    by_stage = sorted(
        [{"stage": k, "count": v["count"], "amount": v["amount"]} for k, v in stage_map.items()],
        key=lambda x: x["amount"], reverse=True
    )

    metrics["pipeline"] = {
        "total_value": pipeline_total,
        "by_stage":    by_stage,
    }

    # __ Accounts ____________________________________________________
    accounts = _safe_records(
        "SELECT Id, Name, Industry, BillingCountry FROM Account ORDER BY Name LIMIT 200"
    )
    industry_map: dict[str, int] = {}
    for r in accounts:
        ind = r.get("Industry") or "Other"
        industry_map[ind] = industry_map.get(ind, 0) + 1

    by_industry = sorted(
        [{"industry": k, "count": v} for k, v in industry_map.items()],
        key=lambda x: x["count"], reverse=True
    )[:8]

    metrics["accounts"] = {
        "total":       len(accounts),
        "by_industry": by_industry,
    }

    # __ Leads _______________________________________________________
    leads = _safe_records(
        "SELECT Id, FirstName, LastName, Company, Status, LeadSource "
        "FROM Lead WHERE IsConverted = false ORDER BY CreatedDate DESC LIMIT 200"
    )
    lead_status_map: dict[str, int] = {}
    for r in leads:
        s = r.get("Status") or "Unknown"
        lead_status_map[s] = lead_status_map.get(s, 0) + 1

    by_lead_status = sorted(
        [{"status": k, "count": v} for k, v in lead_status_map.items()],
        key=lambda x: x["count"], reverse=True
    )

    metrics["leads"] = {
        "total":     len(leads),
        "by_status": by_lead_status,
    }

    # __ AI recommendations __________________________________________
    recommendations: list[dict[str, str]] = []
    high_count = priority_counts.get("High", 0)
    if high_count > 0:
        recommendations.append({
            "type":   "warning",
            "icon":   "_",
            "title":  f"{high_count} High-Priority Case{'s' if high_count != 1 else ''} Open",
            "body":   "Review and assign these cases immediately to prevent SLA breaches.",
            "action": "Show me all open high-priority cases",
        })
    if pipeline_total > 0 and by_stage:
        top_stage = by_stage[0]["stage"]
        recommendations.append({
            "type":   "info",
            "icon":   "_",
            "title":  f"${pipeline_total:,.0f} in Open Pipeline",
            "body":   f"Most value is in '{top_stage}'. Consider follow-up actions to advance these deals.",
            "action": f"Show opportunities in {top_stage} stage",
        })
    if len(leads) > 5:
        recommendations.append({
            "type":   "success",
            "icon":   "_",
            "title":  f"{len(leads)} Unconverted Lead{'s' if len(leads) != 1 else ''}",
            "body":   "Assign leads to sales reps or run a nurturing campaign.",
            "action": "List new leads from the Technology industry",
        })
    open_total = len(open_cases)
    if open_total > 10:
        new_count = status_counts.get("New", 0)
        if new_count > 0:
            recommendations.append({
                "type":   "warning",
                "icon":   "_",
                "title":  f"{new_count} Case{'s' if new_count != 1 else ''} in 'New' Status",
                "body":   "These cases haven't been picked up yet. Assign them to avoid delays.",
                "action": "Show me all open cases with status New",
            })
    if not recommendations:
        recommendations.append({
            "type":   "info",
            "icon":   "_",
            "title":  "CRM looks healthy",
            "body":   "No immediate actions required. Keep monitoring your pipeline and cases.",
            "action": "Show opportunities closing this quarter",
        })

    metrics["recommendations"] = recommendations
    metrics["generated_at"] = time.time()

    return _cors(jsonify(metrics))


# ------------------------------------------------------------------
# POST /api/share  -- generate shareable summary
# ------------------------------------------------------------------

@app.route("/api/share", methods=["POST", "OPTIONS"])
def share() -> Response:
    if request.method == "OPTIONS":
        return _cors(Response(status=204))

    body: dict[str, Any] = request.get_json(silent=True) or {}
    prompt   = body.get("prompt", "")
    answer   = body.get("answer", "")
    soql     = body.get("soql", "")
    records  = body.get("records", [])
    obj_name = body.get("object", "")

    lines: list[str] = [
        "______________________________",
        "  Salesforce AI Agent -- Result",
        "______________________________",
        "",
        f"Query: {prompt}",
        "",
    ]

    if answer:
        lines += ["Response:", answer, ""]

    if records:
        count = len(records)
        lines.append(f"Records returned: {count}" + (f" ({obj_name})" if obj_name else ""))
        # First 5 records as a simple text table
        if records:
            keys = [k for k in records[0].keys() if k not in ("attributes", "Id")][:6]
            col_w = 18
            header = " | ".join(k[:col_w].ljust(col_w) for k in keys)
            sep    = "-+-".join("-" * col_w for _ in keys)
            lines += ["", header, sep]
            for rec in records[:5]:
                row_vals = []
                for k in keys:
                    v = rec.get(k)
                    if isinstance(v, dict):
                        v = v.get("Name", str(v))
                    row_vals.append(str(v or "--")[:col_w].ljust(col_w))
                lines.append(" | ".join(row_vals))
            if count > 5:
                lines.append(f"  ... and {count - 5} more record(s)")
        lines.append("")

    lines += [f"Generated: {time.strftime('%Y-%m-%d %H:%M')} by Salesforce AI Agent"]

    share_text = "\n".join(lines)
    return _cors(jsonify({"text": share_text}))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    logger.info("Starting Salesforce AI Agent on http://0.0.0.0:%d  debug=%s", port, debug)
    app.run(debug=debug, host="0.0.0.0", port=port)
