"""Incident / Problem / Change / Service Request pages share one implementation."""
import streamlit as st

from dashboard.api import ApiClient
from dashboard.sections.common import provider_choices, render_result
from dashboard.ui import api_call, badge, has_role, header, table

TITLES = {
    "incident": ("Incident Management", "Triage, investigate and propose resolutions for incidents"),
    "problem": ("Problem Management", "Find systemic causes behind recurring incidents"),
    "change": ("Change Management", "Risk and readiness assessment; the CAB always decides"),
    "request": ("Service Requests", "Fulfilment checks for service requests and their requested items"),
}


def render(api: ApiClient, me: dict, workflow: str) -> None:
    title, subtitle = TITLES[workflow]
    header(title, subtitle)
    f1, f2, f3, f4 = st.columns([3, 1, 1, 1])
    search = f1.text_input("Search number or text", key=f"{workflow}-search", max_chars=80)
    page_size = f4.selectbox("Rows", [10, 25, 50], index=1, key=f"{workflow}-rows")
    page = f3.number_input("Page", min_value=1, value=1, step=1, key=f"{workflow}-page")
    data = api_call(api.get, f"/records/{workflow}", search=search, limit=page_size, offset=(page - 1) * page_size)
    if not data:
        return
    items = data["items"]
    states = sorted({r["state"] for r in items})
    chosen_state = f2.selectbox("State", ["All"] + states, key=f"{workflow}-state")
    priority = st.multiselect("Priority", sorted({r["priority"] for r in items}), key=f"{workflow}-prio")
    shown = [r for r in items if (chosen_state in ("All", r["state"])) and (not priority or r["priority"] in priority)]
    st.caption(f"{len(shown)} shown of {data['total'] if data['total'] is not None else '?'} matching records")
    table(shown, ["number", "short_description", "state", "priority", "category", "assignment_group", "source"])
    if not shown:
        return

    labels = {f"{r['number']} - {r['short_description']}": r for r in shown}
    record = labels[st.selectbox("Select a record", list(labels), key=f"{workflow}-select")]
    with st.container(border=True):
        a, b, c = st.columns([2, 1, 1])
        a.markdown(f"**{record['number']}**  \n{record['short_description']}")
        b.markdown(f"State  \n{record['state']}")
        c.markdown(f"Priority  \n{badge('high' if record['priority'] in ('1', '2') else 'low')} P{record['priority']}",
                   unsafe_allow_html=True)
        st.write(record["description"] or "_No description_")
        if workflow == "request":
            items_resp = api_call(api.get, f"/records/request/{record['number']}/items", quiet=True)
            if items_resp and items_resp["items"]:
                st.markdown("**Requested items**")
                table(items_resp["items"], ["number", "short_description", "state"])

    st.divider()
    if not has_role(me, "analyst"):
        st.info("Your role is read-only. An analyst or higher can run the agent workflow.")
    else:
        p1, p2, p3 = st.columns([1, 1, 2])
        provider = p1.selectbox("LLM provider", provider_choices(api), key=f"{workflow}-provider")
        background = p2.checkbox("Run in background", help="Follow progress in the Workflow Execution Monitor",
                                 key=f"{workflow}-bg")
        force = p3.checkbox("Start a new run even if one is awaiting approval", key=f"{workflow}-force")
        with st.expander("Optional diagnostic evidence"):
            diag_key = st.text_input("Label (e.g. cpu_metrics)", key=f"{workflow}-dk", max_chars=100)
            diag_val = st.text_area("Value (logs, metrics - secrets are masked)", key=f"{workflow}-dv", max_chars=4000)
        if st.button("Run agent workflow", type="primary", key=f"{workflow}-run"):
            body = {"record_id": record["number"], "workflow": workflow, "llm_provider": provider, "force": force}
            if diag_key and diag_val:
                body["diagnostics"] = {diag_key: diag_val}
            with st.spinner("Agents are working..."):
                resp = api_call(api.post, "/investigations", body, params={"wait": str(not background).lower()})
            if resp:
                st.session_state[f"{workflow}-last"] = resp
                if resp["status"] == "running":
                    st.success(f"Started run {resp['run_id']}. Follow it in the Workflow Execution Monitor.")
    last = st.session_state.get(f"{workflow}-last")
    if last and last.get("result") and last["result"]["record_id"] == record["number"]:
        if last["result"].get("duplicate"):
            st.info("An equivalent run is already awaiting approval, so it is shown instead of starting a new one.")
        st.subheader("Agent workflow results")
        render_result(api, me, last["run_id"], last["result"], key=f"{workflow}-res")
