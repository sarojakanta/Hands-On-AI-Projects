import streamlit as st

from dashboard.api import ApiClient
from dashboard.sections.common import approval_card
from dashboard.ui import api_call, has_role, header, note

TABS = [("Pending", "pending"), ("Failed (retry)", "failed"), ("Executed", "executed"), ("Rejected", "rejected"),
        ("Expired", "expired"), ("All", "")]


def render(api: ApiClient, me: dict) -> None:
    header("Human Approval Center", "Every ServiceNow change proposed by the agents waits here for a person")
    if not has_role(me, "analyst"):
        st.info("Approval requests are visible to analysts, approvers and administrators.")
        return
    note("The AI can only propose actions. Approving writes exactly the content shown to ServiceNow, attributed to "
         "you. Remediation steps are recorded but never executed automatically.")
    if not has_role(me, "approver"):
        st.info("You can review requests, but deciding needs the approver role.")
    tabs = st.tabs([t for t, _ in TABS])
    for tab, (_label, status) in zip(tabs, TABS, strict=False):
        with tab:
            data = api_call(api.get, "/approvals", status=status, limit=50)
            if not data:
                continue
            st.caption(f"{data['total']} request(s)")
            if not data["items"]:
                st.info("Nothing here.")
            for i, approval in enumerate(data["items"]):
                approval_card(api, me, approval, f"center-{status or 'all'}-{i}")
