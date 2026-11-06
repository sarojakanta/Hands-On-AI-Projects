# Multiagentic AI Researcher system

A Python multi-agent assistant built with LangGraph. An OpenRouter-powered
supervisor routes each question to the right specialists: stable
general-knowledge and historical questions are answered directly by the LLM,
current or source-backed questions go to a Tavily web-search agent, and weather
questions go to an OpenWeather agent. The supervisor can run both specialists
for questions that need current web information and weather together. An
OpenRouter-powered formatter composes the final, sourced answer.

The web interface presents the run as three panels: the agent pipeline and live
agent flow on the left, the chat in the center, and run metrics (token usage,
external API calls, and agent performance) on the right.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Supervisor  │ ──▶ │  web_search  │ ──▶ │              │
│  (LLM route) │     │  (Tavily)    │     │   Formatter  │ ──▶ answer
│              │ ──▶ │  weather     │ ──▶ │     (LLM)    │
│              │ ──▶ │  direct ─────┼────▶│              │
└──────────────┘     └──────────────┘     └──────────────┘
```

Source layout (`src/multi_agent_assistant/`):

- `config.py` — loads and validates settings from the environment.
- `agents.py` — the agent nodes (supervisor, web search, weather, formatter)
  and the OpenRouter chat helper.
- `reporting.py` — observability helpers kept separate from agent logic: token
  extraction/aggregation, retry policy, per-agent timing, and external-API call
  recording.
- `graph.py` — wires the nodes into a LangGraph `StateGraph`.
- `web.py` — the Flask app factory, JSON chat API, and server entry point.
- `cli.py` — the command-line interface.
- `templates/`, `static/` — the three-panel web UI.

For a deeper walkthrough of the design, the LangGraph state machine, the
request lifecycle, and the data contracts, see
[docs/DESIGN.md](docs/DESIGN.md).

### Reporting

Every request returns structured metrics alongside the answer:

- **LLM Token Usage** — input, output, and total tokens per LLM call, with a
  combined total.
- **External API Usage** — each Tavily/OpenWeather call with provider,
  operation, status, and duration.
- **Agent Performance** — execution time, success/failure, and retry count per
  agent. LLM calls retry up to three times on transient errors (timeouts, 5xx)
  but never on `429`, so rate limits surface immediately.

## Setup

1. Create and activate a virtual environment.
2. Install the project. Either install the package (recommended, provides the
   `multi-agent-assistant` and `multi-agent-assistant-web` commands):

   ```powershell
   pip install -e .
   ```

   or install the pinned runtime dependencies from `requirements.txt`:

   ```powershell
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in your Tavily, OpenWeather, and
   OpenRouter API keys. The default model is `nvidia/nemotron-3.5-lightning:free`.

## Run

Command line:

```powershell
multi-agent-assistant "What is the weather in Tokyo today?"
```

Or as a module:

```powershell
python -m multi_agent_assistant "Find recent news about renewable energy"
```

Web interface:

```powershell
multi-agent-assistant-web
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000). The server binds to
localhost and runs with Flask debug mode disabled. To change the bind address
or port, set `WEB_HOST` and `WEB_PORT`.

## Docker

Build the image:

```powershell
docker build -t multiagentic-researcher .
```

Run it, passing your API keys via an env file (the container binds to
`0.0.0.0:5000`):

```powershell
docker run --rm -p 5000:5000 --env-file .env multiagentic-researcher
```

Then open [http://127.0.0.1:5000](http://127.0.0.1:5000). The container serves
the app with gunicorn (2 workers, 4 threads) rather than the Flask dev server.

## Tests

```powershell
python -m unittest discover -s tests -v
```

## API keys

- `TAVILY_API_KEY`: Tavily Search API key.
- `OPENWEATHER_API_KEY`: OpenWeather API key with access to Current Weather Data.
- `OPENROUTER_API_KEY`: OpenRouter API key.
- `OPENROUTER_MODEL` (optional): model identifier, defaulting to
  `nvidia/nemotron-3.5-lightning:free`.

## Notes on free models

Free (`:free`) OpenRouter models share rate limits across all users and have a
per-account daily request cap. If you see an "OpenRouter has rate-limited the
configured model" message, either wait for the daily reset, switch
`OPENROUTER_MODEL` to another free model from a different provider, or add your
own provider key in OpenRouter settings for dedicated limits. Tavily is only
called when the supervisor routes a question to web search; ordinary stable or
historical questions do not consume a Tavily search. Weather questions without a
recognizable location are answered with a request for a city. API and network
errors are raised rather than being presented as successful results.
