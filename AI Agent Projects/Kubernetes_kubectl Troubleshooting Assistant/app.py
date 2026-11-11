"""Streamlit UI for the Kubernetes / kubectl Troubleshooting Assistant.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from k8s_assistant.analysis import (
    AnalysisError,
    TroubleshootingAnalyzer,
    TroubleshootingReport,
)
from k8s_assistant.config import load_settings
from k8s_assistant.providers import (
    Provider,
    get_provider,
    provider_api_key,
    provider_base_url,
    provider_model,
)
from k8s_assistant.search import (
    KubernetesSearchClient,
    SearchError,
    SearchResponse,
)

st.set_page_config(
    page_title="Kubernetes Troubleshooting Assistant",
    page_icon="☸️",
    layout="wide",
)

# Button styling. Streamlit wraps each keyed widget in a container with the
# class `st-key-<key>`, which is a stable hook for targeting a specific
# button. The Investigate button (key="investigate") starts light grey and
# turns blue once the problem box has text; Clear (key="clear") stays grey.
_BUTTON_CSS = """
<style>
/* Default (empty box): both buttons light grey */
.st-key-investigate button,
.st-key-clear button {
    background-color: #e0e0e0 !important;
    border: 1px solid #bdbdbd !important;
    color: #222222 !important;
}
.st-key-investigate button:hover,
.st-key-clear button:hover {
    background-color: #d0d0d0 !important;
    border-color: #9e9e9e !important;
    color: #000000 !important;
}
</style>
"""
st.markdown(_BUTTON_CSS, unsafe_allow_html=True)

# Applied only when the problem box has text: Investigate turns blue.
_INVESTIGATE_ACTIVE_CSS = """
<style>
.st-key-investigate button {
    background-color: #1976d2 !important;
    border: 1px solid #1976d2 !important;
    color: #ffffff !important;
}
.st-key-investigate button:hover {
    background-color: #1565c0 !important;
    border-color: #1565c0 !important;
    color: #ffffff !important;
}
</style>
"""

EXAMPLE_PROBLEMS = [
    "Pod stuck in CrashLoopBackOff after deploying a new image",
    "kubectl apply fails with: error validating data: ValidationError",
    "Pods in Pending state: 0/3 nodes available: insufficient memory",
    "ImagePullBackOff: Failed to pull image, repository does not exist",
    "Service has no endpoints and traffic is not reaching the pods",
]


def _render_report(report: TroubleshootingReport, search: SearchResponse) -> None:
    """Render a TroubleshootingReport into the Streamlit page."""
    if report.summary:
        st.subheader("Summary")
        st.write(report.summary)

    if report.root_causes:
        st.subheader("Likely root causes")
        for i, rc in enumerate(report.root_causes, start=1):
            evidence = (
                f"  _(evidence: {', '.join(f'[{e}]' for e in rc.evidence)})_"
                if rc.evidence
                else ""
            )
            st.markdown(f"**{i}. {rc.cause}**{evidence}")
            if rc.explanation:
                st.markdown(rc.explanation)

    if report.troubleshooting_steps:
        st.subheader("Troubleshooting steps")
        for i, ts in enumerate(report.troubleshooting_steps, start=1):
            st.markdown(f"**Step {i}:** {ts.step}")
            if ts.detail:
                st.markdown(f"> {ts.detail}")

    if report.kubectl_commands:
        st.subheader("Recommended kubectl commands")
        for kc in report.kubectl_commands:
            if kc.purpose:
                st.caption(kc.purpose)
            st.code(kc.command, language="bash")

    sources = report.sources or [
        type("S", (), {"ref": i, "title": r.title, "url": r.url})
        for i, r in enumerate(search.results, start=1)
    ]
    if sources:
        st.subheader("Sources")
        for src in sources:
            if src.url:
                st.markdown(f"[{src.ref}] [{src.title or src.url}]({src.url})")
            else:
                st.markdown(f"[{src.ref}] {src.title}")


class LLMChoice:
    """LLM selection resolved entirely from environment variables / .env."""

    def __init__(
        self, provider: Provider, model: str, api_key: str | None, base_url: str | None
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    @property
    def ready(self) -> bool:
        """Local providers need no key; hosted ones require a non-empty key."""
        if not self.provider.requires_api_key:
            return True
        return bool(self.api_key)

    def effective_api_key(self) -> str:
        """Key to pass to the OpenAI client (placeholder for local providers)."""
        if self.api_key:
            return self.api_key
        return self.provider.placeholder_key


def _clear_problem() -> None:
    """Callback: empty the problem text box."""
    st.session_state["problem"] = ""


def _set_problem(text: str) -> None:
    """Callback: load an example problem into the text box."""
    st.session_state["problem"] = text


def _resolve_llm(default_provider: str) -> LLMChoice:
    """Resolve the active LLM from env: LLM_PROVIDER + per-provider vars."""
    provider = get_provider(default_provider)
    return LLMChoice(
        provider=provider,
        model=provider_model(provider),
        api_key=provider_api_key(provider),
        base_url=provider_base_url(provider),
    )


def _render_llm_status(llm: LLMChoice) -> None:
    """Show the env-configured LLM as read-only status in the sidebar."""
    st.write(f"Provider: **{llm.provider.label}**")
    st.write(f"Model: `{llm.model}`")
    if llm.base_url:
        st.caption(f"Endpoint: `{llm.base_url}`")
    if llm.provider.requires_api_key:
        st.write(
            f"{llm.provider.label} API key:",
            "✅ set" if llm.api_key else "❌ missing",
        )
    else:
        st.caption("No API key required (local provider).")
    st.caption(
        "Configured via `.env` — set `LLM_PROVIDER` and the matching "
        f"`{llm.provider.api_key_env}` / `{llm.provider.model_env}`."
    )


def main() -> None:
    settings = load_settings()

    st.title("☸️ Kubernetes / kubectl Troubleshooting Assistant")
    st.caption(
        "Describe a kubectl error or deployment failure. The assistant searches "
        "Kubernetes docs and community resources with Tavily, then uses an LLM to "
        "pinpoint root causes and recommend fixes."
    )

    with st.sidebar:
        st.header("Configuration")
        st.write(
            "Tavily API key:",
            "✅ set" if settings.tavily_ready else "❌ missing",
        )
        st.caption("Tavily key is read from `TAVILY_API_KEY` in `.env`.")
        st.divider()

        st.subheader("LLM")
        llm = _resolve_llm(settings.default_provider)
        _render_llm_status(llm)

        st.divider()
        max_results = st.slider("Max search results", 3, 10, 6)
        st.divider()
        st.caption("Example problems")
        for ex in EXAMPLE_PROBLEMS:
            st.button(
                ex,
                use_container_width=True,
                on_click=_set_problem,
                args=(ex,),
                key=f"example_{hash(ex)}",
            )

    problem = st.text_area(
        "Describe the problem",
        key="problem",
        height=160,
        placeholder=(
            "Paste the kubectl error output or describe the deployment failure.\n"
            "Example: Pod my-api is in CrashLoopBackOff, logs show 'exec format error'."
        ),
    )

    has_text = bool(problem.strip())

    col_run, col_clear = st.columns([1, 1])
    with col_run:
        run = st.button(
            "🔍 Investigate",
            type="primary",
            use_container_width=True,
            key="investigate",
        )
    with col_clear:
        st.button(
            "🧹 Clear",
            on_click=_clear_problem,
            use_container_width=True,
            key="clear",
        )

    # When text has been entered, highlight the Investigate button with an
    # active color. Injected after the buttons so it overrides the base grey.
    if has_text:
        st.markdown(_INVESTIGATE_ACTIVE_CSS, unsafe_allow_html=True)

    if not run:
        return

    if not has_text:
        st.info("Enter a kubectl error or deployment problem above first.")
        return

    if not settings.tavily_ready:
        st.error(
            "Missing `TAVILY_API_KEY`. Copy `.env.example` to `.env` and set it."
        )
        return
    if not llm.ready:
        st.error(
            f"Missing `{llm.provider.api_key_env}` for {llm.provider.label}. "
            "Set it in `.env`."
        )
        return

    analyzer = TroubleshootingAnalyzer(
        api_key=llm.effective_api_key(),
        model=llm.model,
        base_url=llm.base_url,
    )

    # Scope guard: reject questions that are not about Kubernetes / kubectl
    # before spending a search + analysis call on them.
    try:
        with st.spinner("Checking the question..."):
            in_scope = analyzer.is_in_scope(problem)
    except AnalysisError as exc:
        st.error(str(exc))
        return

    if not in_scope:
        st.warning(
            "This assistant only handles **Kubernetes / kubectl** problems. "
            "Please describe a Kubernetes error or deployment failure "
            "(for example a `CrashLoopBackOff`, `ImagePullBackOff`, or a "
            "`kubectl apply` validation error)."
        )
        return

    try:
        with st.spinner("Searching Kubernetes documentation and resources..."):
            search_client = KubernetesSearchClient(settings.tavily_api_key or "")
            search = search_client.search(problem, max_results=max_results)
    except SearchError as exc:
        st.error(str(exc))
        return

    if search.is_empty:
        st.warning("No search results were returned. Try rephrasing the problem.")
        return

    try:
        with st.spinner(
            f"Analyzing results with {llm.provider.label} ({llm.model})..."
        ):
            report = analyzer.analyze(problem, search)
    except AnalysisError as exc:
        st.error(str(exc))
        with st.expander("Raw search results"):
            for i, r in enumerate(search.results, start=1):
                st.markdown(f"**[{i}] [{r.title}]({r.url})**")
                st.caption(r.content)
        return

    _render_report(report, search)

    with st.expander("Raw search results"):
        if search.answer:
            st.markdown("**Tavily answer:**")
            st.write(search.answer)
        for i, r in enumerate(search.results, start=1):
            st.markdown(f"**[{i}] [{r.title}]({r.url})**  ·  score {r.score:.2f}")
            st.caption(r.content)


if __name__ == "__main__":
    main()
