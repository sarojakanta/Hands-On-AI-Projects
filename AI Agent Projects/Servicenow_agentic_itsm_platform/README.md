# Enterprise Agentic ITSM Platform

Reusable multi-agent IT Service Management platform. ServiceNow is the first integration, with mock and live modes. The initial release includes Incident, Problem, Change and Service Request workflows.

## Current capabilities
- LangGraph workflow with specialized classification, priority, investigation, knowledge, RCA, resolution and verification nodes.
- LLM provider selection: mock, Groq, Ollama and generic OpenAI-compatible endpoint.
- ServiceNow Table API adapter with mock/live configuration.
- Streamlit dashboard and FastAPI API.
- Explicit approval before submitting record updates.
- Local Markdown knowledge retrieval (keyword baseline; vector RAG is a planned enhancement).
- Pytest coverage for all four workflow types.
- Docker and GitHub Actions CI.

## Quick start (Windows PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```
In a second terminal:
```powershell
.venv\Scripts\activate
streamlit run dashboard/app.py
```
Run tests:
```powershell
pytest -v
```

## LLM provider configuration
Choose provider in the dashboard or pass `llm_provider` in `POST /investigations`.

**Groq**
```env
GROQ_API_KEY=...
GROQ_MODEL=llama-3.3-70b-versatile
```

**Ollama**
```env
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.1
```
Ensure the Ollama model is pulled and the Ollama service is running. For Docker, `localhost` inside the API container refers to that container; use `host.docker.internal` on Docker Desktop when Ollama runs on the Windows host.

**Mock**
```env
LLM_PROVIDER=mock
```

## ServiceNow configuration
Start with `SERVICENOW_MODE=mock`. For live mode:
```env
SERVICENOW_MODE=live
SERVICENOW_INSTANCE=https://YOUR_INSTANCE.service-now.com
SERVICENOW_USERNAME=...
SERVICENOW_PASSWORD=...
```
The configured integration account must have appropriate read access to `incident`, `problem`, `change_request` and `sc_request`, plus narrowly scoped update access for work notes (and incident priority). ServiceNow table access and field names vary by instance and role configuration; validate them in your PDI. Basic authentication is used by this starter adapter; use OAuth and a secrets manager before production.

## API
- `GET /health`
- `GET /workflows`
- `GET /records/{incident|problem|change|request}`
- `GET /records/{workflow}/{record_id}`
- `POST /investigations` with `{ "record_id": "INC0010025", "workflow": "incident", "llm_provider": "mock" }`
- `GET /investigations/{run_id}`
- `POST /investigations/{run_id}/approve`

## Important implementation boundaries
This release is a runnable development baseline, not production-ready autonomous remediation. The current workflow uses deterministic rules and a sequential LangGraph graph; provider configuration is wired and ready for the next reasoning integration, but the LLM is not yet used to make decisions in this release. Local knowledge retrieval is keyword-based, not embedding-based vector RAG. Approval submits limited notes/priority fields; it does not execute shell commands or restart services. Run state is currently in process memory and is lost when the API restarts.

Before production: implement OAuth, RBAC, durable state/checkpointing, tenant isolation, approval policy enforcement, secrets management, rate limiting, structured audit persistence, observability and organization-specific field mappings.
