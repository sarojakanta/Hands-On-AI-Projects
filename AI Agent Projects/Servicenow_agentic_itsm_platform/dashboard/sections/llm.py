import pandas as pd
import streamlit as st

from dashboard.api import ApiClient
from dashboard.ui import api_call, badge, fmt_ts, has_role, header, note, table


def render(api: ApiClient, me: dict) -> None:
    header("LLM Provider Configuration", "Providers, fallback chain, connectivity checks and usage metrics")
    prov = api_call(api.get, "/llm/providers")
    if not prov:
        return
    a, b, c = st.columns(3)
    a.metric("Default provider", prov["default_provider"])
    b.metric("Fallbacks", ", ".join(prov["fallback_providers"]) or "none")
    c.metric("Timeout / retries", f"{prov['timeout_seconds']}s / {prov['max_retries']}")
    st.subheader("Providers")
    for p in prov["items"]:
        col1, col2, col3, col4 = st.columns([2, 1, 3, 1])
        col1.markdown(f"**{p['provider']}**")
        col2.markdown(badge("ready" if p["configured"] else "failed"), unsafe_allow_html=True)
        col3.caption(f"model: {p['model'] or '-'}  |  endpoint: {p['base_url'] or '-'}")
        col4.caption("API key set" if p["has_api_key"] else "no key")
    note("API keys and endpoints are set through environment variables (.env or your secret store), never through "
         "this page, and are never displayed. Restart the API after changing them.")

    if has_role(me, "admin"):
        st.subheader("Connectivity check")
        c1, c2 = st.columns([1, 3])
        choice = c1.selectbox("Provider", [p["provider"] for p in prov["items"]], key="llm-test-provider")
        if c2.button("Test provider", key="llm-test"):
            result = api_call(api.post, "/llm/test", {"provider": choice})
            if result:
                (st.success if result["ok"] else st.error)(
                    f"{choice}: {'reachable' if result['ok'] else result['error']} ({result['latency_ms']} ms)")

    usage = api_call(api.get, "/llm/usage", limit=50)
    if not usage:
        return
    s = usage["summary"]
    st.subheader("Usage")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Calls", s["calls"])
    m2.metric("Prompt tokens", s["prompt_tokens"])
    m3.metric("Completion tokens", s["completion_tokens"])
    m4.metric("Estimated cost", f"${s['estimated_cost']:.4f}")
    m5.metric("Failures", s["failures"])
    left, right = st.columns(2)
    with left:
        st.markdown("**By provider and model**")
        table(usage["by_model"])
    with right:
        st.markdown("**Tokens by agent**")
        if usage["by_agent"]:
            st.bar_chart(pd.DataFrame(usage["by_agent"]).set_index("agent")[["tokens"]])
        else:
            st.info("No LLM calls recorded yet. The mock provider makes no model calls.")
    with st.expander("Recent calls"):
        table(({**r, "timestamp": fmt_ts(r["timestamp"])} for r in usage["recent"]),
              ["timestamp", "agent", "provider", "model", "prompt_tokens", "completion_tokens", "latency_ms",
               "success", "error"])
