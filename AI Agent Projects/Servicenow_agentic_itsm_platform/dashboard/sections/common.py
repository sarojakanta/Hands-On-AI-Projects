"""Components shared by several sections."""
from __future__ import annotations

import html
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.api import ApiClient
from dashboard.ui import api_call, badge, fmt_ts, has_role, note, pct, table

EXPECTED_AGENTS = {
    "incident": ["classification", "priority", "investigation", "knowledge", "rca", "resolution", "verification"],
    "problem": ["classification", "investigation", "knowledge", "rca", "resolution", "verification"],
    "change": ["classification", "knowledge", "change_management", "resolution", "verification"],
    "request": ["classification", "knowledge", "resolution", "verification"],
}
STATUS_ORDER = ["running", "awaiting_approval", "partial", "failed", "completed", "rejected"]


def progress_of(workflow: str, steps: list[dict]) -> tuple[float, list[str]]:
    """Fraction of expected agents that have reached a final state (success/skipped/failed)."""
    expected = EXPECTED_AGENTS.get(workflow, [])
    done = {s["agent"] for s in steps if s["status"] in ("success", "skipped", "failed")}
    remaining = [a for a in expected if a not in done]
    return (1 - len(remaining) / len(expected)) if expected else 0.0, remaining


def timeline_chart(steps: list[dict]):
    rows = [s for s in steps if s["status"] != "skipped"]
    if not rows:
        st.info("No agent steps recorded yet.")
        return
    t0 = min(datetime.fromisoformat(s["started_at"]) for s in rows)
    data = []
    for s in rows:
        start = (datetime.fromisoformat(s["started_at"]) - t0).total_seconds() * 1000
        data.append({"agent": f"{s['sequence']:02d} {s['agent']}" + (f" (try {s['attempt']})" if s["attempt"] > 1 else ""),
                     "start": start, "end": start + max(s["duration_ms"], 0.5), "status": s["status"],
                     "duration_ms": round(s["duration_ms"], 2)})
    frame = pd.DataFrame(data)
    chart = alt.Chart(frame).mark_bar().encode(
        x=alt.X("start:Q", title="milliseconds since workflow start"), x2="end:Q",
        y=alt.Y("agent:N", sort=None, title=None),
        color=alt.Color("status:N", scale=alt.Scale(domain=["success", "failed"], range=["#1a7f37", "#cf222e"]),
                        legend=alt.Legend(title="status")),
        tooltip=["agent", "status", "duration_ms"]).properties(height=max(120, 26 * len(frame)))
    st.altair_chart(chart)


def provider_choices(api: ApiClient) -> list[str]:
    data = api_call(api.get, "/llm/providers", default={"items": []}, quiet=True)
    names = [p["provider"] for p in data["items"] if p["configured"]]
    return names or ["mock"]


def approval_card(api: ApiClient, me: dict, approval: dict, key: str) -> None:
    status = approval["status"]
    with st.container(border=True):
        left, right = st.columns([4, 1])
        left.markdown(f"**{html.escape(approval['action_type'].replace('_', ' ').title())}** on "
                      f"`{html.escape(approval['record_number'])}` ({html.escape(approval['record_type'])})")
        right.markdown(badge(status), unsafe_allow_html=True)
        st.caption(f"Requested by {approval['requested_by']} at {fmt_ts(approval['requested_at'])} | "
                   f"requires role: {approval['requires_role']}"
                   + ("" if approval["executable"] else " | never executed automatically"))
        for field, value in approval["payload"].items():
            st.text_area(field, value, height=110 if len(str(value)) > 120 else 60, disabled=True,
                         key=f"{key}-payload-{field}")
        if approval.get("decided_by"):
            st.caption(f"Decision by **{approval['decided_by']}** at {fmt_ts(approval['decided_at'])}: "
                       f"{approval['comment'] or '(no comment)'}")
        if approval.get("execution_result"):
            result = approval["execution_result"]
            if result.get("error"):
                st.error(f"Execution failed: {result['error']}")
            elif result.get("executed"):
                st.success("Written to ServiceNow (mode: " + str(result.get("mode", "?")) + ")")
            else:
                st.info(result.get("reason", "Recorded; no automatic execution."))
        allowed = has_role(me, approval["requires_role"])
        if status == "pending":
            comment = st.text_input("Comment (required to reject)", key=f"{key}-comment")
            a, b, _ = st.columns([1, 1, 4])
            if not allowed:
                st.caption(f"Your role does not allow deciding this request (needs {approval['requires_role']}).")
            if a.button("Approve", key=f"{key}-approve", type="primary", disabled=not allowed):
                if api_call(api.post, f"/approvals/{approval['id']}/decision",
                            {"decision": "approve", "comment": comment}):
                    st.rerun()
            if b.button("Reject", key=f"{key}-reject", disabled=not allowed):
                if api_call(api.post, f"/approvals/{approval['id']}/decision",
                            {"decision": "reject", "comment": comment}):
                    st.rerun()
        elif status == "failed" and allowed:
            if st.button("Retry execution", key=f"{key}-retry"):
                if api_call(api.post, f"/approvals/{approval['id']}/retry"):
                    st.rerun()
        full = api_call(api.get, f"/approvals/{approval['id']}", default=None, quiet=True)
        if full and full.get("audit_trail"):
            with st.expander("Audit trail"):
                table(({"time": fmt_ts(e["timestamp"]), "actor": e["actor"], "action": e["action"],
                        "details": str(e["details"])} for e in full["audit_trail"]))


def download_buttons(api: ApiClient, run_id: str, record_id: str, key: str) -> None:
    a, b, _ = st.columns([1, 1, 4])
    md = api_call(api.download, f"/investigations/{run_id}/report", default=None, quiet=True, format="md")
    js = api_call(api.download, f"/investigations/{run_id}/report", default=None, quiet=True, format="json")
    if md:
        a.download_button("Report (Markdown)", md, f"{record_id}-report.md", "text/markdown", key=f"{key}-md")
    if js:
        b.download_button("Report (JSON)", js, f"{record_id}-report.json", "application/json", key=f"{key}-json")


def render_result(api: ApiClient, me: dict, run_id: str, result: dict, key: str = "res") -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Category", result["category"])
    c2.metric("Recommended priority", f"P{result['priority']}")
    c3.metric("Confidence (hypothesis)", pct(result["confidence"]))
    c4.markdown("**Status**")
    c4.markdown(badge(result["status"]), unsafe_allow_html=True)
    st.markdown(f"**Assessment:** {result['probable_root_cause']}")
    note("Root causes are unverified hypotheses unless confirmed independently. Nothing has been written to "
         "ServiceNow; every change below needs a human approval.")
    if result.get("security_flags"):
        st.warning("Security flags: " + ", ".join(result["security_flags"]) +
                   ". Ticket or document text contained instruction-like content and was treated as data only.")
    if result.get("errors"):
        st.error("Degraded run: " + "; ".join(result["errors"]))

    tabs = st.tabs(["Summary", "Evidence & hypotheses", "Knowledge & history", "Actions & approvals",
                    "Verification", "Trace"])
    with tabs[0]:
        st.write(f"**Affected service:** {result['affected_service'] or 'n/a'}  \n"
                 f"**Business impact:** {result['business_impact'] or 'n/a'}")
        if result["missing_information"]:
            st.markdown("**Missing information**")
            for item in result["missing_information"]:
                st.write(f"- {item}")
        if result.get("change_assessment"):
            ca = result["change_assessment"]
            st.markdown(f"**Change risk:** {badge(ca['risk_level'])} &nbsp; decision: `{ca['recommended_decision']}`",
                        unsafe_allow_html=True)
            st.write("Missing: " + (", ".join(ca["missing_requirements"]) or "none"))
        st.markdown("**Recommendations**")
        for item in result["recommendations"]:
            st.write(f"- {item}")
    with tabs[1]:
        for h in result["hypotheses"]:
            with st.expander(f"{h['cause']}  -  {pct(h['confidence'])} ({h['status']}, {h['source']})"):
                for e in h["evidence"]:
                    st.write(f"- {e}")
        if not result["hypotheses"]:
            st.info("No hypothesis could be formed from the available evidence.")
        st.markdown("**Collected evidence**")
        for e in result["evidence_items"]:
            st.write(f"- {e['text']}  _[{e['source']}]_")
    with tabs[2]:
        for k in result["knowledge_sources"]:
            with st.expander(f"{k.get('citation') or k['source']}  (score {k['score']})"):
                st.write(k["excerpt"])
                if k.get("suspicious"):
                    st.warning("This excerpt contains instruction-like text.")
        if not result["knowledge_sources"]:
            st.info("No relevant knowledge found.")
        for c in result["knowledge_conflicts"]:
            st.warning(f"Conflicting guidance: {' vs '.join(c['sources'])} ({c['reason']})")
        if result["similar_incidents"]:
            st.markdown("**Similar historical incidents**")
            table(result["similar_incidents"], ["number", "short_description", "state", "score"])
    with tabs[3]:
        if not result["proposed_actions"]:
            st.info("No actions were proposed.")
        if result["proposed_actions"] and not has_role(me, "analyst"):
            st.caption("Approval details are visible to analysts and above.")
            table(({"action": a["action_type"], "record": a["record_number"], "status": a.get("status", "")}
                   for a in result["proposed_actions"]))
        for i, action in enumerate(result["proposed_actions"] if has_role(me, "analyst") else []):
            if action.get("approval_id"):
                approval = api_call(api.get, f"/approvals/{action['approval_id']}", default=None)
                if approval:
                    approval_card(api, me, approval, f"{key}-ap{i}")
            else:
                st.error(f"Proposal rejected by policy: {action.get('error', 'invalid')}")
    with tabs[4]:
        v = result.get("verification") or {}
        for c in v.get("checks", []):
            st.write(("PASS" if c["passed"] else "FAIL") + f" - **{c['name']}**: {c['detail']}")
        st.write(f"Closure allowed: **{v.get('closure_allowed', False)}**. {v.get('reason', '')}")
    with tabs[5]:
        for line in result["agent_trace"]:
            st.write(f"- {line}")
        if result["skipped_agents"]:
            st.caption("Skipped (not needed): " + ", ".join(result["skipped_agents"]))
    download_buttons(api, run_id, result["record_id"], key)
