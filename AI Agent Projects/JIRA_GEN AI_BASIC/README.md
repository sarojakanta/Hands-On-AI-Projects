# Jira GenAI Solution

A Generative AI solution that connects to Jira (via an MCP server) and uses an LLM to
generate test cases, summarize issues, and visualize project metrics in a dashboard.

## Architecture

```
┌────────────────────┐      HTTP      ┌───────────────────────┐      MCP       ┌──────────────┐
│   React + Vite UI  │  ───────────>  │    FastAPI backend    │  ───────────>  │  Jira MCP    │
│  (dashboard+charts)│  <───────────  │  (GenAI + MCP client) │  <───────────  │   server     │
└────────────────────┘                └───────────┬───────────┘                └──────────────┘
                                                   │ LLM API
                                                   v
                                            ┌─────────────┐
                                            │  OpenAI /   │
                                            │  local LLM  │
                                            └─────────────┘
```

- **Backend** (`backend/`): FastAPI. Talks to a Jira MCP server through an MCP client layer
  and to an LLM for GenAI features. Falls back to realistic mock data so the app runs with
  zero external credentials.
- **Frontend** (`frontend/`): React + Vite dashboard with charts (Recharts) for Jira status,
  priority, test coverage, and sprint burndown, plus a GenAI panel.

## Features

- **Dashboard graphs**: Jira status distribution, priority breakdown, test case coverage,
  sprint burndown.
- **GenAI**: generate test cases from a Jira issue, summarize issues.
- **MCP integration**: pulls issues and test cases from a Jira MCP server (mockable).

## Quick start

### Backend

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then edit as needed
uvicorn app.main:app --reload --port 8000
```

The backend runs fine with no credentials — it serves mock data by default.
Set values in `.env` to connect a real LLM and Jira MCP server.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the URL Vite prints (default http://localhost:5173).

## Configuration

See `backend/.env.example`. Key settings:

- `USE_MOCK_DATA` — `true` (default) to use bundled sample data, `false` for live MCP.
- `OPENAI_API_KEY` — enables real LLM output; without it, GenAI uses a template engine.
- `MCP_SERVER_COMMAND` / `MCP_SERVER_ARGS` — how to launch the Jira MCP server.
