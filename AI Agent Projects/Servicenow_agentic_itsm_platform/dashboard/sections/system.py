import streamlit as st

from dashboard.api import ApiClient
from dashboard.ui import api_call, badge, has_role, header, note, table


def render(api: ApiClient, me: dict) -> None:
    header("System Configuration", "Component health and effective configuration (secrets are always masked)")
    status = api_call(api.get, "/system/status")
    if status:
        st.markdown(f"Environment: {badge(status['environment'])}", unsafe_allow_html=True)
        for name, comp in status["components"].items():
            ok = comp.get("ok", True)
            with st.container(border=True):
                a, b = st.columns([1, 5])
                a.markdown(f"**{name}**")
                a.markdown(badge("ready" if ok else "failed"), unsafe_allow_html=True)
                details = {k: v for k, v in comp.items() if k not in ("ok", "providers")}
                b.json(details)

    if not has_role(me, "admin"):
        st.info("Configuration values and user management are available to administrators.")
        return
    config = api_call(api.get, "/system/config")
    if config:
        note("Change settings through environment variables or your secret store and restart the services. "
             "Values below are read-only.")
        groups = {
            "Application": ["app_env", "log_level", "log_json", "database_url", "auto_create_tables"],
            "Security": ["jwt_secret", "jwt_algorithm", "jwt_expire_minutes", "auth_enabled", "approval_ttl_hours",
                         "approval_allow_self_approval"],
            "ServiceNow": [k for k in config if k.startswith("servicenow_")],
            "LLM": [k for k in config if k.startswith(("llm_", "groq_", "ollama_", "openai_"))],
            "Knowledge": ["knowledge_dir", "chroma_dir", "embedding_provider", "embedding_model", "embedding_base_url",
                          "chunk_size", "chunk_overlap", "knowledge_min_score", "similar_min_score"],
            "Orchestration": ["max_workflow_steps", "max_agent_retries"],
        }
        tabs = st.tabs(list(groups))
        for tab, keys in zip(tabs, groups.values(), strict=False):
            with tab:
                table(({"setting": k, "value": str(config[k])} for k in keys if k in config))

    st.subheader("Users")
    users = api_call(api.get, "/users")
    if users:
        table(({**u, "roles": ", ".join(u["roles"])} for u in users), ["username", "email", "roles", "is_active"])
    with st.expander("Create a user"):
        with st.form("create-user", clear_on_submit=True):
            username = st.text_input("Username", max_chars=100)
            password = st.text_input("Password (8-72 characters)", type="password", max_chars=72)
            roles = st.multiselect("Roles", ["viewer", "analyst", "approver", "admin"], default=["viewer"])
            if st.form_submit_button("Create", type="primary"):
                if api_call(api.post, "/users", {"username": username, "password": password, "roles": roles}):
                    st.success(f"Created {username}")
    if users:
        with st.expander("Change roles or deactivate a user"):
            with st.form("update-user"):
                target = st.selectbox("User", [u["username"] for u in users])
                new_roles = st.multiselect("Roles", ["viewer", "analyst", "approver", "admin"], key="upd-roles")
                active = st.checkbox("Active", value=True)
                if st.form_submit_button("Apply"):
                    body = {"is_active": active}
                    if new_roles:
                        body["roles"] = new_roles
                    if api_call(api.patch, f"/users/{target}", body):
                        st.success("Updated")
