"""Shared presentation helpers. Every dynamic value rendered as HTML is escaped (ticket text is untrusted)."""
from __future__ import annotations

import html
from typing import Callable, Iterable

import pandas as pd
import streamlit as st

from dashboard.api import ApiError

ROLE_ORDER = ("viewer", "analyst", "approver", "admin")

STATUS_COLORS = {
    "completed": "#1a7f37", "success": "#1a7f37", "executed": "#1a7f37", "approved": "#2f81f7", "ready": "#1a7f37",
    "awaiting_approval": "#9a6700", "pending": "#9a6700", "pending_approval": "#9a6700", "running": "#0969da",
    "partial": "#bc4c00", "failed": "#cf222e", "rejected": "#cf222e", "expired": "#6e7781", "skipped": "#6e7781",
    "high": "#cf222e", "medium": "#bc4c00", "low": "#1a7f37",
}

CSS = """
<style>
.block-container {padding-top: 1.4rem; max-width: 1400px;}
[data-testid="stMetric"] {background:#f6f8fa; border:1px solid #d0d7de; border-radius:10px; padding:12px 16px;}
.itsm-badge {display:inline-block; padding:2px 10px; border-radius:12px; color:#fff; font-size:0.78rem;
             font-weight:600; letter-spacing:.02em; white-space:nowrap;}
.itsm-title {font-size:1.7rem; font-weight:700; margin-bottom:0;}
.itsm-sub {color:#57606a; margin-top:0; margin-bottom:1rem;}
.itsm-card {border:1px solid #d0d7de; border-radius:10px; padding:14px 16px; margin-bottom:10px; background:#fff;}
.itsm-note {border-left:4px solid #9a6700; background:#fff8c5; padding:8px 12px; border-radius:4px; margin:6px 0;}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def badge(status: str) -> str:
    color = STATUS_COLORS.get(str(status).lower(), "#57606a")
    label = html.escape(str(status).replace("_", " "))
    return f'<span class="itsm-badge" style="background:{color}">{label}</span>'


def show_badge(status: str) -> None:
    st.markdown(badge(status), unsafe_allow_html=True)


def header(title: str, subtitle: str = "") -> None:
    st.markdown(f'<p class="itsm-title">{html.escape(title)}</p>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<p class="itsm-sub">{html.escape(subtitle)}</p>', unsafe_allow_html=True)


def has_role(me: dict, role: str) -> bool:
    top = max((ROLE_ORDER.index(r) for r in me.get("roles", []) if r in ROLE_ORDER), default=-1)
    return top >= ROLE_ORDER.index(role)


def api_call(fn: Callable, *args, default=None, quiet: bool = False, **kwargs):
    """Run an API call; show failures (with request id) instead of crashing the page."""
    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        if not quiet:
            st.error(f"{exc.message}" + (f"  \n`request id: {exc.request_id}`" if exc.request_id else ""))
        return default


def table(rows: Iterable[dict], columns: list[str] | None = None, rename: dict | None = None, key: str | None = None):
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        st.info("Nothing to show.")
        return frame
    if columns:
        frame = frame[[c for c in columns if c in frame.columns]]
    if rename:
        frame = frame.rename(columns=rename)
    st.dataframe(frame, hide_index=True, key=key)
    return frame


def note(text: str) -> None:
    st.markdown(f'<div class="itsm-note">{html.escape(text)}</div>', unsafe_allow_html=True)


def pct(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def fmt_ts(value: str | None) -> str:
    return (value or "").replace("T", " ")[:19]
