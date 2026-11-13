import time

import streamlit as st

from dashboard.api import ApiClient
from dashboard.sections.common import progress_of, render_result, timeline_chart
from dashboard.ui import api_call, badge, fmt_ts, has_role, header, table


def render(api: ApiClient, me: dict) -> None:
    header("Workflow Execution Monitor", "Live progress, agent timeline, handoffs and per-agent details")
    runs = api_call(api.get, "/investigations", limit=30)
    if not runs or not runs["items"]:
        st.info("No workflow runs yet. Start one from an Incident, Problem, Change or Service Request page.")
        return
    labels = {f"{r['record_number']} | {r['workflow']} | {r['status']} | {fmt_ts(r['started_at'])}": r["id"]
              for r in runs["items"]}
    run_id = labels[st.selectbox("Workflow run", list(labels), key="monitor-run")]
    live = st.checkbox("Live refresh while running", value=True, key="monitor-live")

    tl = api_call(api.get, f"/investigations/{run_id}/timeline")
    if not tl:
        return
    ex, steps = tl["execution"], tl["steps"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Record", ex["record_number"])
    c2.markdown("**Status**")
    c2.markdown(badge(ex["status"]), unsafe_allow_html=True)
    c3.metric("Supervisor steps", ex["step_count"])
    c4.metric("Duration", "running" if ex["duration_seconds"] is None else f"{ex['duration_seconds']}s")

    fraction, remaining = progress_of(ex["workflow"], steps)
    st.progress(min(max(fraction, 0.0), 1.0),
                text=f"{fraction:.0%} complete" + (f" - waiting for: {', '.join(remaining)}" if remaining else ""))
    if ex["error"]:
        st.error(f"Run error: {ex['error']}")
    if tl["errors"]:
        st.error("Agent errors:\n\n" + "\n".join(f"- {e}" for e in tl["errors"]))
    if tl["stop_reason"] not in ("", "done"):
        st.warning(f"Workflow stopped early: {tl['stop_reason']}")

    st.subheader("Agent execution timeline")
    timeline_chart(steps)
    if tl["skipped"]:
        st.caption("Skipped (not required): " + ", ".join(tl["skipped"]))
    if tl["reruns"]:
        st.caption("Extra investigation round triggered for: " + ", ".join(sorted(set(tl["reruns"]))))

    st.subheader("Agent-level details")
    for s in steps:
        icon = {"success": "OK", "failed": "FAILED", "skipped": "SKIPPED"}.get(s["status"], s["status"])
        with st.expander(f"#{s['sequence']} {s['agent']} - {icon} - {s['duration_ms']} ms"
                         + (f" (attempt {s['attempt']})" if s["attempt"] > 1 else "")):
            a, b = st.columns(2)
            a.markdown("**Input**")
            a.json(s["input"] or {})
            b.markdown("**Output**")
            b.json(s["output"] or {})
            if s["error"]:
                st.error(s["error"])

    left, right = st.columns(2)
    with left:
        st.subheader("Handoffs")
        table(tl["handoffs"], ["from", "to", "reason"])
    with right:
        st.subheader("Supervisor decisions")
        table(tl["supervisor_log"], ["step", "next", "reason"])

    if ex["status"] in ("failed", "partial") and has_role(me, "analyst"):
        if st.button("Resume run (retry failed agents, keep completed work)", type="primary", key="monitor-resume"):
            if api_call(api.post, f"/investigations/{run_id}/resume"):
                st.rerun()
    detail = api_call(api.get, f"/investigations/{run_id}", quiet=True)
    if detail and detail.get("result"):
        with st.expander("Result", expanded=False):
            render_result(api, me, run_id, detail["result"], key="monitor-res")
    if live and ex["status"] == "running":
        time.sleep(2)
        st.rerun()
