# Optimization Report and Implementation Plan

Baseline: v0.2.0 (7 passing tests, mock mode).

## 1. Findings

### Architecture
| # | Finding | Severity |
|---|---------|----------|
| A1 | Workflow is a fixed linear chain of 7 nodes; every record runs every agent. No supervisor, branching, parallelism, retries or loop limits. | High |
| A2 | Required agents missing: Supervisor, Change Management Agent. Classification/Priority are keyword rules only. | High |
| A3 | `state["knowledge"]` stores a retriever object inside LangGraph state (not serializable, blocks checkpointing). | Medium |
| A4 | Agents mutate and return the same dict; no per-agent input/output/timing/status capture (trace is a list of strings). | High |
| A5 | `ITSMState` is a loose TypedDict with `Any` fields. | Medium |
| A6 | A new graph is compiled on every request. | Low |

### LLM
| # | Finding | Severity |
|---|---------|----------|
| L1 | `LLMManager` is constructed per request and passed into agents; no fallback, retry, timeout config, rate-limit handling. | High |
| L2 | No token/latency/cost tracking. | Medium |
| L3 | JSON parsed with bare `json.loads`; no schema validation, no fence stripping. | Medium |
| L4 | Mock provider returns `""` so LLM path is never exercised in tests. | Medium |
| L5 | No prompt-injection defence: record text is passed to the model unmarked. | High |

### ServiceNow
| # | Finding | Severity |
|---|---------|----------|
| S1 | Basic auth only; no OAuth. Credentials read at call time from global settings. | High |
| S2 | No pagination (hard `sysparm_limit=50`), no retries, no rate-limit (429) handling. | High |
| S3 | Field list and table names hardcoded; `assignment_group` is stored as a raw reference object stringified. | Medium |
| S4 | No read/write permission separation; `update_record` is callable by anything. | High |
| S5 | `sysparm_query` built by string interpolation of user input (query injection of `^` operators). | High |
| S6 | No API call log; no response validation. | Medium |

### RAG
| # | Finding | Severity |
|---|---------|----------|
| R1 | Keyword substring scoring over `*.md` only; no chunking, embeddings, metadata, citations, PDF/DOCX. | High |
| R2 | Re-reads every file on each query. No update/delete. No historical-incident retrieval. | Medium |

### Backend / Persistence / Security
| # | Finding | Severity |
|---|---------|----------|
| B1 | Runs stored in `self.runs` dict: lost on restart, not shared across workers. | High |
| B2 | Single `main.py`; no routers, versioning, DI, central error handling, pagination, logging. | Medium |
| B3 | No authentication or RBAC; **anyone can call `/approve`**, which writes to ServiceNow. | Critical |
| B4 | Approval is a boolean flag; no approver identity, comments, timestamp, audit trail, rejection, or idempotency (can approve twice). | High |
| B5 | Duplicate investigation of the same record creates unlimited runs. | Medium |
| B6 | Error messages from upstream exceptions returned verbatim (`502 detail=str(exc)`), may leak URLs/config. | Medium |
| B7 | README claims "Docker and GitHub Actions CI" but no `.github/workflows` exists. | Low |

### Dashboard / Ops
| # | Finding | Severity |
|---|---------|----------|
| D1 | Single page; none of the 13 required sections. Performs `st.stop()` on API error. | Medium |
| D2 | Dockerfile runs as root, single stage, no healthcheck, no DB service. | Medium |
| D3 | Tests: 5 integration tests only. No unit tests for agents, LLM, SNOW, RAG, approvals, or failure scenarios. | High |
| D4 | `requirements.txt` includes pytest in runtime deps; no pinned lower/upper reproducibility; no DB/chroma/jwt libs. | Low |

### Working functionality to preserve
- Mock ServiceNow records and the four workflow types.
- Endpoints `/health`, `/workflows`, `/records/...`, `/investigations...` (kept as legacy aliases; new API under `/api/v1`).
- Mock LLM provider, rule-based RCA fallback, "closure not authorized" verification.

## 2. Plan (priority order)

| Phase | Scope | Key outputs |
|-------|-------|-------------|
| 2 | Config + DB | Pydantic settings (grouped), SQLAlchemy 2 models, Alembic migration, repositories; SQLite dev / PostgreSQL prod |
| 3 | LLM Manager | Provider adapters (Groq, Ollama, OpenAI-compat, mock), retry/timeout/fallback, usage tracking, JSON schema validation, secret redaction |
| 4 | Agents + graph | Supervisor-driven LangGraph, `AgentResult` records, retries, loop/step limits, parallel fan-out for investigation/knowledge, recovery |
| 5 | ServiceNow | Auth strategies (basic, OAuth), pagination, retries, field mapping config, read/write client split, call log, safe query building |
| 6 | RAG | ChromaDB store, embedding abstraction (hash-local, sentence-transformers optional, OpenAI-compatible), ingestion (PDF/DOCX/TXT/MD), citations, historical incidents |
| 7 | Approvals | Approval service + policy, state machine, audit, resume workflow |
| 8 | API + Dashboard | `/api/v1` routers, JWT + RBAC, error handlers, structured logging, 13-section Streamlit app |
| 9 | Tests/Security | Unit + integration + failure scenarios; prompt-injection guards; masking |
| 10 | Ops/Docs | Multi-stage non-root Docker, Postgres compose, GitHub Actions, README with diagrams |

## 3. Design decisions
- **Keep** the existing package layout (`app/agents`, `app/llm`, `app/servicenow`, `app/knowledge`, `app/workflows`, `app/services`) and extend in place; add `app/db`, `app/api`, `app/security`, `app/approvals`.
- **No duplicate modules**: `app/llm/manager.py`, `app/servicenow/client.py`, `app/knowledge/retriever.py`, `app/agents/*` are evolved, not parallel copies.
- **SQLite for local/tests, PostgreSQL for production**, selected by `DATABASE_URL`.
- **Embeddings default** to a deterministic local hashing embedder so tests and offline use need no model downloads. `sentence-transformers` and OpenAI-compatible embeddings are optional.
- **Environment constraint**: Python 3.14 is in use locally; some heavy packages (e.g. `chromadb` wheels, `psycopg`) may lack 3.14 wheels. Compatibility will be verified in Phase 2 and 6 and reported honestly.
