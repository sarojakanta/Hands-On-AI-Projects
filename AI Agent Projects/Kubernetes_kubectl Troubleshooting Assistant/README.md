# ☸️ Kubernetes / kubectl Troubleshooting Assistant

A Python + Streamlit application that investigates Kubernetes deployment
failures and `kubectl` errors. It uses the **Tavily Search API** to retrieve
relevant Kubernetes documentation, error explanations, and community
troubleshooting resources, then uses an **LLM** to analyze those results and
produce root causes, step-by-step fixes, recommended `kubectl` commands, and
source references.

## How it works

```
User problem ──▶ Tavily Search ──▶ LLM analysis ──▶ Structured report
 (kubectl error)   (K8s docs, SO,     (root cause,     (causes, steps,
                    GitHub, cloud)     reasoning)        commands, sources)
```

- **`k8s_assistant/config.py`** — loads Tavily key + default provider from env.
- **`k8s_assistant/providers.py`** — presets for the supported LLM providers
  (OpenAI, Groq, local Ollama), all of which speak the OpenAI-compatible API.
- **`k8s_assistant/search.py`** — queries Tavily, biased toward authoritative
  Kubernetes sources (kubernetes.io, Stack Overflow, GitHub, cloud docs).
- **`k8s_assistant/analysis.py`** — sends the problem + search evidence to the
  selected LLM and parses a strict-JSON troubleshooting report.
- **`app.py`** — Streamlit UI that orchestrates search → analysis → display.

For a deeper walkthrough of the design, see [ARCHITECTURE.md](ARCHITECTURE.md).

## LLM providers

The assistant works with any of three providers. The active provider is chosen
in `.env` via `LLM_PROVIDER`; the sidebar shows the resolved provider, model,
and key status as read-only. All three use the OpenAI-compatible Chat
Completions API, so the analyzer code is identical across them.

| Provider        | Env key for API key | Default model                 | Notes                                            |
| --------------- | ------------------- | ----------------------------- | ------------------------------------------------ |
| OpenAI          | `OPENAI_API_KEY`    | `gpt-4o-mini`                 | Hosted. https://platform.openai.com/api-keys     |
| Groq            | `GROQ_API_KEY`      | `llama-3.3-70b-versatile`     | Hosted, fast. https://console.groq.com/keys      |
| Ollama (local)  | _none_              | `llama3.1`                    | Run `ollama serve` + `ollama pull <model>` first |

Each provider also accepts optional `*_MODEL` and `*_BASE_URL` overrides
(e.g. `GROQ_MODEL`, `OLLAMA_BASE_URL`).

To switch providers, edit `.env` and **restart the app** (config is read at
startup):

```bash
LLM_PROVIDER=openai   # or: groq | ollama
```

## Setup

1. Install dependencies (Python 3.10+):

   ```bash
   pip install -r requirements.txt
   ```

2. Configure API keys. Copy the example file and fill in your keys:

   ```bash
   cp .env.example .env
   ```

   | Variable         | Required | Description                                                     |
   | ---------------- | -------- | --------------------------------------------------------------- |
   | `TAVILY_API_KEY` | yes      | Tavily Search API key — https://app.tavily.com                  |
   | `LLM_PROVIDER`   | no       | Default provider: `openai` (default), `groq`, or `ollama`       |
   | `OPENAI_API_KEY` | if used  | OpenAI API key                                                  |
   | `GROQ_API_KEY`   | if used  | Groq API key                                                    |

   You only need a key for the provider(s) you actually use. Ollama needs no
   key but requires a local server. See the provider table above for model and
   base-URL override variables.

## Run

```bash
streamlit run app.py
```

Then open the URL Streamlit prints (usually http://localhost:8501), describe a
kubectl error or deployment failure, and click **Investigate**.

The **Investigate** button is light grey while the problem box is empty and
turns blue once you enter text, signalling it is ready to run. **Clear** empties
the box; the sidebar also has example problems you can load with one click.

## Example problems

- Pod stuck in `CrashLoopBackOff` after deploying a new image
- `kubectl apply` fails with a `ValidationError`
- Pods stuck `Pending` due to insufficient node resources
- `ImagePullBackOff` — image pull failures
- Service with no endpoints / traffic not reaching pods

## Notes

- The LLM is instructed to ground its analysis in the retrieved sources and to
  avoid inventing URLs. Source references map back to the numbered search
  results shown in the "Raw search results" panel.
- The provider, model, and API key are configured entirely in `.env`. The
  sidebar displays the active selection read-only; there are no runtime
  provider controls.
- Local models via Ollama work but can be slow on CPU-only machines. If the
  analysis step hangs, try a smaller model (e.g. `OLLAMA_MODEL=llama3.2:1b`)
  or use a hosted provider.

## Project layout

```
.
├── app.py                      # Streamlit UI + orchestration
├── k8s_assistant/
│   ├── config.py               # env-based settings (Tavily key, provider)
│   ├── providers.py            # OpenAI / Groq / Ollama presets
│   ├── search.py               # Tavily search client
│   └── analysis.py             # LLM analysis -> structured report
├── .streamlit/config.toml      # theme + server settings
├── .env.example                # configuration template
├── requirements.txt
├── README.md
└── ARCHITECTURE.md             # design document
```
