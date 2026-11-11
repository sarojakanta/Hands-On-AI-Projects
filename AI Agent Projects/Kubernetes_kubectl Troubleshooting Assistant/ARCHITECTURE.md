# Architecture — Kubernetes / kubectl Troubleshooting Assistant

This document describes the design of the assistant: its components, the data
flow, key decisions, and extension points.

## 1. Overview

The application is a single-page Streamlit app backed by a small Python package
(`k8s_assistant`). A user describes a Kubernetes problem (a `kubectl` error or a
deployment failure). The app:

1. Searches the web for relevant Kubernetes resources using the **Tavily**
   Search API.
2. Sends the problem plus the search evidence to an **LLM** (OpenAI, Groq, or a
   local Ollama model).
3. Parses the LLM's response into a structured troubleshooting report and
   renders it.

The LLM is instructed to ground its analysis in the retrieved sources and cite
them, which keeps answers grounded and traceable.

## 2. High-level data flow

```
┌──────────────┐     problem text      ┌────────────────────────┐
│  Streamlit   │ ────────────────────▶ │  KubernetesSearchClient │
│  UI (app.py) │                       │      (search.py)        │
│              │ ◀──────────────────── │   Tavily Search API     │
└──────┬───────┘   SearchResponse      └────────────────────────┘
       │
       │  problem + SearchResponse
       ▼
┌────────────────────────┐    OpenAI-compatible    ┌──────────────────┐
│ TroubleshootingAnalyzer │ ──── chat.completions ─▶│  LLM provider    │
│      (analysis.py)      │                         │ OpenAI/Groq/Ollama│
│                         │ ◀──── JSON response ────│                  │
└──────────┬──────────────┘                         └──────────────────┘
           │ TroubleshootingReport
           ▼
   ┌───────────────┐
   │  Rendered UI  │  summary, root causes, steps, kubectl commands, sources
   └───────────────┘
```

## 3. Components

### 3.1 `app.py` — UI and orchestration
- Renders the Streamlit page: title, sidebar (config status, max-results
  slider, example problems), the problem text area, and the
  **Investigate** / **Clear** buttons.
- Resolves the active LLM from environment variables at startup via
  `_resolve_llm()` and shows it read-only through `_render_llm_status()`.
- On **Investigate**, it validates inputs, runs the search, runs the analysis,
  and renders the report. Errors from each stage are caught and surfaced as
  friendly messages, with a "Raw search results" expander as a fallback.
- Button styling is driven by CSS keyed to Streamlit's `st-key-<key>` wrapper
  classes: both buttons are light grey by default; Investigate turns blue once
  the problem box contains text.

### 3.2 `k8s_assistant/config.py` — configuration
- `load_settings()` reads a `.env` file (via `python-dotenv`) and the process
  environment. Environment variables take precedence over the file.
- `Settings` holds only the Tavily key and the default provider
  (`LLM_PROVIDER`). Per-provider details live in `providers.py` so the provider
  can be swapped without touching this module.

### 3.3 `k8s_assistant/providers.py` — provider presets
- Defines a `Provider` dataclass and a `PROVIDERS` registry for `openai`,
  `groq`, and `ollama`. Each entry carries the base URL, default model,
  suggested models, whether an API key is required, and the env-var names it
  reads from.
- Helper functions resolve the model, API key, and base URL for a provider,
  honoring optional `*_MODEL` and `*_BASE_URL` overrides.
- Because all three providers expose an **OpenAI-compatible** Chat Completions
  API, the analyzer code is identical across them; only base URL / model / key
  differ.

### 3.4 `k8s_assistant/search.py` — web search
- `KubernetesSearchClient` wraps the Tavily client.
- `build_query()` frames the raw problem as a focused Kubernetes query.
- `search()` calls Tavily with advanced depth, requests a summarized answer,
  and softly prefers authoritative domains (kubernetes.io, Stack Overflow,
  GitHub, major cloud docs).
- Results are normalized into `SearchResult` objects and aggregated into a
  `SearchResponse`. `SearchResult.as_prompt_block()` renders a numbered block
  used to build the LLM prompt, which is what lets the model cite sources by
  number.

### 3.5 `k8s_assistant/analysis.py` — LLM analysis
- `TroubleshootingAnalyzer` uses the `openai` client (pointed at the selected
  provider's base URL).
- A system prompt casts the model as a senior Kubernetes SRE and requires a
  **strict JSON** response (requested via `response_format={"type":
  "json_object"}`) matching a fixed schema.
- `_build_user_prompt()` assembles the user's problem, the Tavily summary, and
  the numbered search results.
- `_parse()` converts the JSON into typed dataclasses (`RootCause`,
  `TroubleshootingStep`, `KubectlCommand`, `SourceRef`, `TroubleshootingReport`).
- `_backfill_sources()` repairs/augments the cited sources using the real
  search result titles and URLs, so references always point at genuine pages
  even if the model is sloppy.

## 4. Key design decisions

- **OpenAI-compatible abstraction.** Treating every provider as an
  OpenAI-compatible endpoint keeps a single analysis code path and makes adding
  providers a config-only change.
- **Config-driven provider selection.** The provider, model, and key are set in
  `.env` (read at startup), not through runtime UI controls. This keeps secrets
  out of the UI and makes deployments reproducible.
- **Grounded, cited answers.** The prompt forces source citations and the code
  backfills real URLs, reducing hallucinated links and making the report
  auditable.
- **Strict JSON contract.** Requesting JSON and parsing into dataclasses gives a
  stable rendering layer and localized failure handling (a parse error degrades
  gracefully to showing raw search results).
- **Separation of concerns.** Search, analysis, provider config, and UI are
  independent modules, so each can be tested or replaced on its own.

## 5. Error handling

- `SearchError` — raised for empty input or Tavily failures; shown as a UI
  error and aborts the run.
- `AnalysisError` — raised for LLM request failures or invalid JSON; shown as a
  UI error, with the raw search results still displayed so the user is not left
  empty-handed.
- Missing keys are detected before any network call: a missing `TAVILY_API_KEY`
  or provider key produces a specific message naming the exact env variable.

## 6. Configuration surface

| Variable          | Purpose                                              |
| ----------------- | ---------------------------------------------------- |
| `TAVILY_API_KEY`  | Required. Tavily Search API key.                     |
| `LLM_PROVIDER`    | `openai` (default), `groq`, or `ollama`.             |
| `OPENAI_API_KEY`  | Key for OpenAI (when selected).                      |
| `GROQ_API_KEY`    | Key for Groq (when selected).                        |
| `*_MODEL`         | Per-provider model override.                         |
| `*_BASE_URL`      | Per-provider endpoint override.                      |

Ollama requires no key but needs a local server (`ollama serve`) with the model
pulled (`ollama pull <model>`).

## 7. Runtime notes

- Streamlit reads `.streamlit/config.toml` for theme (primary color) and server
  options (`headless = true` to skip the first-run email prompt).
- Configuration is loaded once at startup, so changing `.env` requires an app
  restart.
- Local Ollama models can be slow on CPU-only hardware; the first call also
  loads the model into memory. Prefer a small model or a hosted provider if the
  analysis step stalls.

## 8. Extension points

- **Live cluster context.** Add a `cluster.py` that collects read-only
  `kubectl describe` / `logs` / `get events` output and feeds it into the
  prompt for far richer, cluster-aware diagnosis.
- **Additional providers.** Add an entry to `PROVIDERS`; no analysis changes
  needed as long as the endpoint is OpenAI-compatible.
- **Caching.** Memoize search + analysis by problem text to cut latency and API
  cost on repeated queries.
- **Scope guard.** Add a classifier step to reject non-Kubernetes questions
  instead of forcing them through the Kubernetes-framed pipeline.
- **Observability / export.** Post reports to Slack/Jira, or add tracing
  (LangSmith/Langfuse) for token and latency monitoring.
