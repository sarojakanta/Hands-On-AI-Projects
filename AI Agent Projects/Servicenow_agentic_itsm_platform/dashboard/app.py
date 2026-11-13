"""Enterprise Agentic ITSM dashboard (Streamlit). Run: streamlit run dashboard/app.py"""
import os
import sys

import streamlit as st

# Make `dashboard.*` importable when launched as `streamlit run dashboard/app.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import api as api_module  # noqa: E402
from dashboard.sections import SECTIONS  # noqa: E402
from dashboard.ui import badge, inject_css  # noqa: E402


def get_client() -> api_module.ApiClient:
    if "api" not in st.session_state:
        st.session_state.api = api_module.ApiClient(api_module.API_URL, http=api_module.make_http())
    return st.session_state.api


def login_screen(client: api_module.ApiClient) -> None:
    st.markdown('<p class="itsm-title">Enterprise Agentic ITSM Platform</p>', unsafe_allow_html=True)
    st.markdown('<p class="itsm-sub">Sign in to continue</p>', unsafe_allow_html=True)
    with st.form("login"):
        username = st.text_input("Username", max_chars=100)
        password = st.text_input("Password", type="password", max_chars=200)
        if st.form_submit_button("Sign in", type="primary"):
            try:
                client.login(username, password)
                st.session_state.me = client.me()
                st.rerun()
            except api_module.ApiError as exc:
                st.error(str(exc))


def current_user(client: api_module.ApiClient) -> dict | None:
    if client.token is None and "me" not in st.session_state:
        try:  # succeeds without a token only when the API runs with authentication disabled (development)
            st.session_state.me = client.me()
        except api_module.ApiError:
            return None
    if client.token is not None and "me" not in st.session_state:
        try:
            st.session_state.me = client.me()
        except api_module.ApiError:
            client.logout()
            return None
    return st.session_state.get("me")


def main() -> None:
    st.set_page_config(page_title="Agentic ITSM", page_icon=":compass:", layout="wide")
    inject_css()
    client = get_client()
    me = current_user(client)
    if me is None:
        login_screen(client)
        st.stop()

    with st.sidebar:
        st.markdown("### Agentic ITSM")
        st.caption(f"Signed in as **{me['username']}**")
        st.markdown(" ".join(badge(r) for r in me["roles"]), unsafe_allow_html=True)
        if not me.get("auth_enabled", True):
            st.warning("Authentication is disabled (development only).")
        section = st.radio("Navigation", [label for label, _ in SECTIONS], label_visibility="collapsed")
        if client.token is not None and st.button("Sign out"):
            client.logout()
            st.session_state.pop("me", None)
            st.rerun()
        st.caption(f"API: {api_module.API_URL}")

    render = dict(SECTIONS)[section]
    try:
        render(client, me)
    except api_module.ApiError as exc:  # expired token etc.
        if exc.status == 401 and client.token is not None:
            client.logout()
            st.session_state.pop("me", None)
            st.rerun()
        st.error(str(exc))


main()
