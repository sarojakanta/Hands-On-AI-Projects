import streamlit as st

from dashboard.api import ApiClient
from dashboard.ui import api_call, fmt_ts, has_role, header, table


def render(api: ApiClient, me: dict) -> None:
    header("Audit Logs", "Who did what and when: workflow, approval, user and knowledge events; ServiceNow API calls")
    if not has_role(me, "approver"):
        st.info("Audit logs are available to approvers and administrators.")
        return
    tab_events, tab_sn = st.tabs(["Audit events", "ServiceNow API calls"])
    with tab_events:
        f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
        action = f1.text_input("Action (exact, e.g. approval.approved)", key="audit-action", max_chars=100)
        actor = f2.text_input("Actor", key="audit-actor", max_chars=100)
        entity = f3.text_input("Entity ID", key="audit-entity", max_chars=64)
        page = f4.number_input("Page", min_value=1, value=1, key="audit-page")
        data = api_call(api.get, "/audit", action=action, actor=actor, entity_id=entity, limit=50, offset=(page - 1) * 50)
        if data:
            st.caption(f"{data['total']} event(s)")
            table(({**e, "timestamp": fmt_ts(e["timestamp"]), "details": str(e["details"])} for e in data["items"]),
                  ["timestamp", "actor", "action", "entity_type", "entity_id", "details"])
    with tab_sn:
        only_failed = st.checkbox("Only failures", key="audit-sn-failed")
        data = api_call(api.get, "/servicenow/calls", limit=100, only_failures="true" if only_failed else "")
        if data:
            a, b = st.columns(2)
            a.metric("Total calls", data["total_calls"])
            b.metric("Failed calls", data["failed_calls"])
            table(({**c, "timestamp": fmt_ts(c["timestamp"])} for c in data["items"]),
                  ["timestamp", "method", "table", "status_code", "duration_ms", "attempts", "success", "mode", "error"])
