import pandas as pd
import streamlit as st

from dashboard.api import ApiClient
from dashboard.sections.common import STATUS_ORDER
from dashboard.ui import api_call, badge, fmt_ts, header, pct, table


def render(api: ApiClient, me: dict) -> None:
    header("Executive Dashboard", "Operational overview of ITSM records, agent workflows, approvals and LLM usage")
    data = api_call(api.get, "/dashboard/summary")
    if not data:
        return
    st.markdown(f"ServiceNow mode: {badge('live' if data['servicenow_mode'] == 'live' else 'mock')}",
                unsafe_allow_html=True)
    t = data["tickets"]
    cols = st.columns(4)
    for col, (label, key) in zip(cols, [("Incidents", "incident"), ("Problems", "problem"),
                                        ("Changes", "change"), ("Service requests", "request")], strict=False):
        col.metric(label, "unavailable" if t.get(key) is None else t[key])
    ex = data["executions"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Workflow runs", ex["total"])
    c2.metric("Run success rate", pct(ex["success_rate"]))
    c3.metric("Pending approvals", data["pending_approvals"])
    c4.metric("Avg run time", "n/a" if ex["avg_duration_seconds"] is None else f"{ex['avg_duration_seconds']}s")
    llm = data["llm"]
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("LLM calls", llm["calls"])
    d2.metric("LLM tokens", llm["prompt_tokens"] + llm["completion_tokens"])
    d3.metric("Avg LLM latency", f"{llm['avg_latency_ms']:.0f} ms")
    d4.metric("LLM failures", llm["failures"])

    left, right = st.columns(2)
    with left:
        st.subheader("Runs by status")
        counts = ex["by_status"]
        if counts:
            frame = pd.DataFrame({"status": [s for s in STATUS_ORDER if s in counts],
                                  "runs": [counts[s] for s in STATUS_ORDER if s in counts]}).set_index("status")
            st.bar_chart(frame)
        else:
            st.info("No workflow runs yet. Start one from a record page.")
    with right:
        st.subheader("Agent activity")
        agents = data["agents"]
        if agents:
            frame = pd.DataFrame([{"agent": n, "runs": a["runs"], "failed": a["failed"]} for n, a in agents.items()
                                  if n != "supervisor"]).set_index("agent")
            st.bar_chart(frame)
        else:
            st.info("No agent executions yet.")

    st.subheader("Recent workflow runs")
    table(({**r, "started_at": fmt_ts(r["started_at"])} for r in data["recent_executions"]),
          ["record_number", "workflow", "status", "requested_by", "llm_provider", "duration_seconds", "started_at"])
    if data["recent_problems"]:
        st.subheader("Runs needing attention")
        for r in data["recent_problems"]:
            st.markdown(f"{badge(r['status'])} &nbsp; **{r['record_number']}** ({r['workflow']}) - "
                        f"{r['error'] or 'degraded run'}", unsafe_allow_html=True)
