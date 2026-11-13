import pandas as pd
import streamlit as st

from dashboard.api import ApiClient
from dashboard.ui import api_call, badge, fmt_ts, has_role, header, table

PROVIDERS = ["(default)", "mock", "groq", "ollama", "openai_compatible"]


def render(api: ApiClient, me: dict) -> None:
    header("Agent Management", "Responsibilities, configuration and run statistics of every agent")
    data = api_call(api.get, "/agents")
    if not data:
        return
    agents = data["items"]
    rows = []
    for a in agents:
        s = a["stats"]
        rate = (s["success"] / s["runs"]) if s["runs"] else None
        rows.append({"agent": a["name"], "enabled": a["enabled"], "critical": a["critical"],
                     "runs": s["runs"], "failed": s["failed"], "skipped": s["skipped"],
                     "success rate": f"{rate:.0%}" if rate is not None else "n/a",
                     "avg ms": s["avg_duration_ms"], "last run": fmt_ts(s["last_run"]),
                     "LLM override": a["llm_provider"] or "-", "retries": a["max_retries"]})
    table(rows)
    chart = pd.DataFrame([{"agent": r["agent"], "avg ms": r["avg ms"]} for r in rows if r["runs"]])
    if not chart.empty:
        st.subheader("Average execution time (ms)")
        st.bar_chart(chart.set_index("agent"))

    st.subheader("Responsibilities")
    for a in agents:
        with st.expander(f"{a['name']}  {'(critical)' if a['critical'] else ''}"):
            st.write(a["description"])
            st.markdown(badge("ready" if a["enabled"] else "skipped"), unsafe_allow_html=True)

    if not has_role(me, "admin"):
        st.info("Only administrators can change agent configuration.")
        return
    st.subheader("Configure an agent")
    names = [a["name"] for a in agents]
    name = st.selectbox("Agent", names, key="agent-pick")
    current = next(a for a in agents if a["name"] == name)
    locked = current["critical"] or name == "supervisor"
    with st.form("agent-config"):
        enabled = st.checkbox("Enabled", value=current["enabled"], disabled=locked,
                              help="The supervisor and critical agents can not be disabled")
        retries = st.number_input("Max retries", 0, 5, int(current["max_retries"] or 0))
        provider = st.selectbox("LLM provider override", PROVIDERS,
                                index=PROVIDERS.index(current["llm_provider"]) if current["llm_provider"] in PROVIDERS else 0)
        model = st.text_input("LLM model override (optional)", current["llm_model"] or "", max_chars=100)
        if st.form_submit_button("Save", type="primary"):
            body = {"enabled": enabled, "max_retries": int(retries), "llm_model": model or None}
            if provider == "(default)":
                body["clear_llm_override"] = True
            else:
                body["llm_provider"] = provider
            if api_call(api.patch, f"/agents/{name}", body):
                st.success(f"Saved configuration for {name}")
                st.rerun()
