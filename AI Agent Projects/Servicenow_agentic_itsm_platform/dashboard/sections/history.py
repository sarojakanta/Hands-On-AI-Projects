import streamlit as st

from dashboard.api import ApiClient
from dashboard.sections.common import render_result
from dashboard.ui import api_call, fmt_ts, header, table

STATUSES = ["", "running", "awaiting_approval", "completed", "partial", "failed", "rejected"]


def render(api: ApiClient, me: dict) -> None:
    header("Execution History", "Every workflow run with outcome, requester, provider and duration")
    f1, f2, f3, f4, f5 = st.columns([1, 1, 2, 1, 1])
    status = f1.selectbox("Status", STATUSES, key="hist-status")
    workflow = f2.selectbox("Workflow", ["", "incident", "problem", "change", "request"], key="hist-wf")
    record = f3.text_input("Record number", key="hist-rec", max_chars=50)
    size = f4.selectbox("Rows", [10, 25, 50, 100], index=1, key="hist-size")
    page = f5.number_input("Page", min_value=1, value=1, key="hist-page")
    data = api_call(api.get, "/investigations", status=status, workflow=workflow, record_number=record,
                    limit=size, offset=(page - 1) * size)
    if not data:
        return
    st.caption(f"{data['total']} run(s) match")
    rows = [{**r, "started_at": fmt_ts(r["started_at"]), "finished_at": fmt_ts(r["finished_at"])} for r in data["items"]]
    table(rows, ["record_number", "workflow", "status", "requested_by", "llm_provider", "step_count",
                 "duration_seconds", "started_at", "error"])
    if not data["items"]:
        return
    labels = {f"{r['record_number']} | {r['status']} | {fmt_ts(r['started_at'])}": r["id"] for r in data["items"]}
    run_id = labels[st.selectbox("Open a run", list(labels), key="hist-open")]
    detail = api_call(api.get, f"/investigations/{run_id}")
    if detail and detail.get("result"):
        render_result(api, me, run_id, detail["result"], key="hist-res")
    elif detail:
        st.warning("This run has no report (it did not finish). Use the Workflow Execution Monitor to resume it.")
