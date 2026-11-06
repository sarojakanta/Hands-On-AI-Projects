# AI-Powered Customer Support Agent

> For testing purposes only.

The customer-facing demo brand is **DemoCartService**. The brand is used in
the chat page, assistant replies and prompts, and seeded store names. The
development database's seeded store names have been updated. When upgrading a
pre-existing database, update its seeded store names too; historical audit logs
are intentionally retained as records of past conversations.

A modular, web-based AI customer support agent built with **Flask**, orchestrated
with **LangGraph**, grounded with **RAG**, and acting only through scoped
**MCP** tools. Business data is simulated (SQLite). This is a demonstration
project, not connected to any real e-commerce, CRM, or courier system.

> Status: **All 8 phases complete.** The app is a working, end-to-end AI
> customer support agent: Flask chat UI + seeded SQLite (Phase 1), mock-OTP auth
> (Phase 2), a separate MCP server with 8 scoped tools (Phase 3), a single
> LangGraph agent with intent detection + structured output (Phase 4), RAG
> policy-grounded answers (Phase 5), conversation memory + three guardrail
> stages (Phase 6), a human support dashboard with auto-ticket-on-escalation
> (Phase 7), and structured audit logging + an admin log view + a golden /
> regression test set (Phase 8). **253 tests pass.**

## Architecture (target)

```
Customer Browser -> Flask -> Input Guardrails -> Support Agent (LangGraph)
  -> RAG (ChromaDB + BM25 hybrid) | MCP tools | Conversation memory
  -> Output Guardrails -> Customer response / Human escalation
  -> Tracing, evaluation & audit logs
```

Business operations are only reachable through scoped MCP tools. The LLM never
gets raw database or SQL access, and never decides authentication/OTP validity;
those are enforced in backend code.

## Tech stack

- Backend: Python, Flask, REST APIs, SQLAlchemy (SQLite)
- Frontend: HTML, CSS, JavaScript, Bootstrap
- AI: LangGraph, LangChain (where useful), configurable OpenAI, Groq, Anthropic,
  or OpenRouter provider
- RAG: ChromaDB, local sentence-transformers embeddings, BM25, hybrid retrieval
- Memory: Redis when configured, local fallback otherwise
- MCP: Python MCP SDK (separate server process over stdio)
- Testing: pytest

## Requirements

- Windows 11 (instructions below use PowerShell)
- **Python 3.12** (recommended and verified). Create the venv with `py -3.12`.

> Note: the project was moved to Python 3.12 from Phase 3 onward because the
> AI/RAG dependency stack (chromadb, sentence-transformers, torch) builds
> cleanly there. On Python 3.14 some of these wheels are not yet available.

## Setup (Windows PowerShell)

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
Copy-Item .env.example .env
# Edit .env and add your OPENROUTER_API_KEY (needed from Phase 4 for live replies)

# 4. Initialise and seed the database
python scripts/init_db.py

# 5. Build the RAG indexes (first run downloads the local embedding model)
python scripts/ingest_kb.py

# 6. Run the app
python run.py
```

Then open http://127.0.0.1:5000/ in your browser.

## Configuration

All settings are environment variables (see `.env.example`). Nothing sensitive
is hardcoded. Key values:

| Variable | Purpose | Default |
|---|---|---|
| `LLM_PROVIDER` | `auto` \| `openai` \| `groq` \| `anthropic` \| `openrouter` | `auto` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | OpenAI direct | _(empty)_ / `gpt-4o-mini` |
| `GROQ_API_KEY` / `GROQ_MODEL` | Groq direct | _(empty)_ / `llama-3.1-8b-instant` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Anthropic Claude direct | _(empty)_ / `claude-3-5-sonnet-20241022` |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | OpenRouter gateway | _(empty)_ / `openai/gpt-4o-mini` |
| `DATABASE_URL` | SQLite database URL | `sqlite:///data/support_agent.db` |
| `DEV_MODE` | Enables mock-OTP dev indicators | `true` |
| `DEV_OTP` | Hardcoded development OTP | `123456` |
| `TICKET_PROVIDER` | Ticket backend: `local` or `salesforce` | `local` |
| `TICKET_FALLBACK_LOCAL` | Keep tickets local if Salesforce fails | `true` |
| `DEV_TICKET_MAX_COUNT` | Dev/local ticket cap; oldest resolved/closed tickets are pruned | `50` |
| `LOG_MAX_MB` | Dev-mode rotation threshold per log file | `80` |
| `LOG_BACKUP_COUNT` | Rotated backup files to keep per log file | `3` |
| `SALESFORCE_USERNAME` / `SALESFORCE_PASSWORD` / `SALESFORCE_SECURITY_TOKEN` | Salesforce username-password login | _(empty)_ |
| `SALESFORCE_DOMAIN` | Salesforce production (`login`) or sandbox (`test`) | `login` |
| `TWILIO_ACCOUNT_SID` / `TWILIO_API_KEY_SID` / `TWILIO_API_KEY_SECRET` / `TWILIO_VERIFY_SERVICE_SID` | Twilio Verify credentials (provider wiring currently incomplete) | _(empty)_ |
| `REDIS_URL` | Optional Redis; blank uses local fallback | _(empty)_ |
| `EMBEDDING_MODEL` | Local embedding model | `all-MiniLM-L6-v2` |

## Running tests

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

## Project layout

See `app/` (Flask app, routes, models, database), `mcp_server/` (MCP tools),
`knowledge_base/` (policy docs for RAG), `scripts/` (setup utilities),
`tests/` (pytest suites), `data/` and `logs/` (generated at runtime).

## Phase status

- [x] **Phase 1** — Project structure, Flask app, chat UI shell, SQLite DB, seed data
- [x] **Phase 2** — Authentication module + mock OTP + session management
- [x] **Phase 3** — Separate MCP server + 8 scoped business tools
- [x] **Phase 4** — OpenRouter + LangGraph agent + intent detection + structured output
- [x] **Phase 5** — RAG (ChromaDB + BM25 hybrid) policy-grounded answers
- [x] **Phase 6** — Conversation memory + input/tool/output guardrails
- [x] **Phase 7** — Human support dashboard + auto-ticket on escalation
- [x] **Phase 8** — Tracing/audit logs + admin view + golden/regression suite

### Tracing & audit logs (Phase 8)

Every agent turn emits a redacted JSON-lines record to `logs/audit.log`
(`app/observability/tracing.py`), capturing conversation id, customer id,
intent, workflow steps, RAG references, MCP tool names + result status, model +
latency, guardrail decisions, authentication result, escalation decision, and
ticket reference. A redaction pass scrubs OTPs, API keys, emails, card/phone
numbers, and forbidden keys; tool results are reduced to status only — **no
secrets or raw PII are ever written**.

The admin view at `/admin/logs` (`app/routes/admin_routes.py`) shows recent
traces and ticket activity; `/admin/api/logs` returns them as JSON.

### Golden & regression suite (Phase 8)

`tests/test_golden/` contains a golden case set with **at least three cases for
each of the 13 numbered agent rules** (spec §12). They are deterministic
(LLM forced offline) and marked `regression`. Run the release gate with:

```powershell
.\.venv\Scripts\python.exe -m pytest -m regression
```

Run the full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

### Human support dashboard (Phase 7)

When the agent escalates, it **auto-creates a ticket** carrying full handoff
context (customer id, conversation id, category, escalation reason, the
customer's original request, a generated conversation summary, relevant tool
results, authentication status, timestamp). The ticket reference is returned in
the structured output and shown to the customer. Only one ticket is created per
conversation.

The dashboard (`app/routes/dashboard_routes.py`, `/dashboard`) lets support
staff:
- list tickets and filter by status (open / in progress / resolved / closed),
- open a ticket to see escalation reason, summary, original request, tool
  results, and the full conversation history (pulled from memory),
- assign the ticket, change status, add internal notes, and record a
  resolution.

Both HTML views and a small JSON API (`/dashboard/api/tickets...`) are provided.

### Ticket provider (local vs Salesforce)

Ticket storage is behind a provider abstraction (`app/services/ticket_providers/`):
`TICKET_PROVIDER=local` (SQLite, default, self-contained) or `salesforce`
(also creates a Case in a real org, with a local mirror so the dashboard still
works and a graceful fallback to local on failure). Salesforce needs the
`SALESFORCE_USERNAME`, `SALESFORCE_PASSWORD`, `SALESFORCE_SECURITY_TOKEN`,
`SALESFORCE_DOMAIN`, and `simple-salesforce`; it is off by default. The
consumer key/secret settings are present for future use but are not consumed
by the current provider.

In development mode, local ticketing prunes the oldest `RESOLVED`/`CLOSED`
tickets after creating a ticket when the total exceeds `DEV_TICKET_MAX_COUNT`
(set it to `0` to disable pruning).
`OPEN` and `IN_PROGRESS` tickets are never deleted, so the count can exceed the
cap if there are not enough resolved tickets. This cleanup is disabled when
`DEV_MODE=false` or `TICKET_PROVIDER=salesforce`; Salesforce Cases and their
local mirror rows are not removed by this policy. Ticket deletion from SQLite
may not immediately shrink the database file.

In development mode, each of `audit.log`, `tool_calls.log`, and
`guardrail_events.log` rotates at `LOG_MAX_MB`, keeping up to
`LOG_BACKUP_COUNT` older files per log. With the defaults (80 MB and 3
backups), the three logs use at most about 960 MB (3 logs × 4 files × 80 MB),
excluding small filesystem/rotation overhead. Rotation is disabled outside
development mode; set `LOG_MAX_MB=0` to disable it. `LOG_MAX_MB` is a decimal
megabyte value (1 MB = 1,000,000 bytes).

### Conversation memory & guardrails (Phase 6)

**Memory** (`app/memory/`) is kept separate from RAG and isolated per
conversation, with session-TTL expiry:
- `base.py` — `MemoryStore` interface; `local_store.py` (in-process TTL, the
  zero-dependency default) and `redis_store.py` (used when `REDIS_URL` is set).
  The backend auto-selects and degrades to local if Redis is unreachable.
- `conversation_memory.py` — stores conversation id, customer id, recent
  message turns, current intent, auth state, issue summary, and escalation
  status. No OTP/PII values are kept. Prior turns are fed back so the customer
  doesn't repeat themselves.

**Guardrails** (`app/guardrails/`) are deterministic and wired into the graph:
- `input_guardrails.py` — runs first: rejects empty/over-long input, flags
  prompt injection, abuse, and pasted sensitive PII (card numbers).
- `tool_guardrails.py` — authorizes tool calls (auth required, cross-customer
  block, arg validation) and requires explicit customer confirmation for
  sensitive operations like address changes.
- `output_guardrails.py` — runs last on every reply: strips leaked tool names,
  blocks unsupported refund/discount promises, blocks instruction disclosure,
  and redacts PII.

Graph flow: `input_guard → detect → escalation_check → auth_decision →
(auth | tools→rag | rag) → respond → output_guard`. Escalation for
legal/sensitive triggers now takes precedence over the auth prompt.

### Production RAG pipeline (R-1 .. R-8)

The RAG layer meets a production spec:

- **R-1** multi-format ingestion (Markdown, HTML, PDF, spreadsheet) with
  **token-based, section-aware chunking** (300-500 tokens + overlap).
- **R-2** every chunk carries metadata: policy, version, region, language,
  effective date, `customer_safe`, owner (from doc front-matter).
- **R-3** hybrid retrieval pulls a **top-20 candidate pool**, a local
  **cross-encoder reranks** it, and the **best 4** go to the model.
- **R-4** region + language filter is applied **before** ranking, so a customer
  only sees their region's rules.
- **R-5** answers name the policy **and its effective date**; below the rerank
  threshold the agent says it's unsure and offers a human.
- **R-6** `scripts/ingest_kb.py` rebuilds and **retires superseded versions**;
  `indexer.maybe_reindex()` reindexes within `REINDEX_INTERVAL_SECONDS` of a
  change.
- **R-7** customer-safe and **staff-only** documents live in **separate
  indexes** (`knowledge_base/` vs `knowledge_base_staff/`); internal/fraud docs
  are never returned to customers.
- **R-8** `scripts/eval_rag.py` reports **recall@4** and **faithfulness** on a
  golden question set (targets 90% / 95%); enforced by `tests/test_rag_advanced.py`.

Modules live in `app/rag/`: `ingestion.py`, `chunking.py`, `embeddings.py`,
`reranker.py`, `vector_store.py`, `bm25_store.py`, `retriever.py`, `indexer.py`,
`evaluation.py`.

Earlier phase note — policy/FAQ questions are answered from the knowledge base
via the hybrid retriever, never from the system prompt.

- `app/rag/ingestion.py` + `chunking.py` — load the markdown docs and split
  them into overlapping chunks (`CHUNK_SIZE`/`CHUNK_OVERLAP`), tagging each with
  its source policy name for citations.
- `app/rag/embeddings.py` — local `sentence-transformers` model (no API key).
- `app/rag/vector_store.py` — ChromaDB persistent vector index.
- `app/rag/bm25_store.py` — BM25 keyword index for exact-term matches.
- `app/rag/retriever.py` — fuses the two with a configurable weight
  (`HYBRID_VECTOR_WEIGHT`) and returns passages + de-duplicated source names.

Build the indexes before first use:

```powershell
python scripts/ingest_kb.py
```

Grounding rules enforced: policy answers identify the source policy; questions
the agent can't classify as a policy topic (intent `OTHER`) are never dressed up
as grounded answers — the agent admits uncertainty and offers human support.
With an LLM configured, retrieved passages are passed as grounding so the reply
is composed from them; the offline fallback cites the top passage directly.

### Customer Support Agent (Phase 4)

One agent, built as a LangGraph workflow (`app/agents/graph.py`):

```
detect (intent + language) -> escalation_check -> auth_decision -> route
  route == auth   -> respond (ask for OTP, no account data)
  route == tools  -> MCP tools -> respond (verified account / store data)
  route == direct -> respond (policy / other)
```

- `app/services/llm_service.py` — the only place the app talks to the LLM.
  Supports **four interchangeable providers** (OpenAI, Groq, Anthropic, OpenRouter).
  OpenAI, Groq, and OpenRouter use OpenAI-compatible API; Anthropic uses its native SDK.
  Pick with `LLM_PROVIDER` (`auto` selects the first provider that has a key:
  groq → openai → anthropic → openrouter). This is selection priority only;
  errors from the selected provider do not trigger retries against later providers.
  Ollama is explicit-only and excluded from `auto`. Per-provider base URLs are resolved in code, so `*_BASE_URL`
  env vars stay blank unless you need a proxy/Azure override. Keys come from
  env and are never logged. If no provider key is set, the agent uses
  deterministic heuristics so it still runs fully offline.
- `app/agents/prompts.py` — the spec §12 system prompt verbatim, plus focused
  detection/response prompts. Policy text is never embedded here (it comes from
  RAG in Phase 5).
- `app/agents/nodes.py` — detection, auth decision, escalation triggers, MCP
  tool calls, and response generation. The authentication and escalation
  decisions are deterministic Python, not left to the LLM.
- `app/agents/schemas.py` — structured output: `reply`, `language`, `intent`,
  `confidence`, `escalate`, `escalation_reason`, `authentication_required`,
  `source_references`.

The `/api/chat` route reads verified-session state from the backend session
manager (never from the client) and passes it to the agent as authoritative
context.

### Authentication (Phase 2)

With `DEV_MODE=true` (default), authentication uses a **mock OTP provider**
with a fixed development OTP (`DEV_OTP`, default `123456`); no SMS is sent.
Twilio Verify provider code and blank `TWILIO_*` placeholders are present, but
the current `app/config.py` does not expose those settings to the provider.
Therefore do not disable dev mode expecting SMS verification to work until that
configuration wiring is implemented and tested.

- `app/auth/otp_provider.py` — `OTPProvider` abstraction, `MockOTPProvider`,
  and a Twilio Verify implementation.
- `app/auth/verification_service.py` — enforces, in backend code only:
  5-minute expiry, max 3 attempts, single-use, and (session, customer) binding.
  The LLM never decides OTP validity.
- `app/auth/session_manager.py` — verified-session store, isolated per browser
  session so one customer's verified state can't leak to another.
- `app/auth/auth_service.py` — identifies the customer by order/customer
  reference and orchestrates the challenge lifecycle. No PII is returned before
  verification (only a masked destination hint).

REST endpoints: `POST /api/auth/initiate`, `POST /api/auth/verify`,
`GET /api/auth/status`. The dev OTP is surfaced to the UI **only** when
`DEV_MODE=true`.

### MCP servers & tools (four domain servers)

The business tools are split across **four domain MCP servers**, each a
separate stdio process fronting a (simulated) enterprise system. This mirrors a
real deployment where each backend system is wrapped by its own MCP adapter
exposing only a small, scoped toolset. The LLM only reaches business data
through these tools — there is no raw database or SQL surface.

| MCP server | Real-world system | Tools | Access tier |
|---|---|---|---|
| **crm-mcp** | Salesforce CRM | `send_otp` | write (low) |
| | | `verify_customer` | write (low) |
| | | `get_customer_profile` | read |
| **oms-mcp** | SAP / order mgmt | `get_order` | read |
| | | `get_shipment_status` | read |
| | | `request_address_change` | **write (guarded)** |
| **helpdesk-mcp** | ServiceNow / Zendesk | `create_ticket` | write (low) |
| | | `escalate_to_human` | write (low) |
| **store-mcp** | Store system | `find_store` | read |
| | | `get_store_hours` | read |

Account-scoped tools (`get_order`, `get_shipment_status`,
`get_customer_profile`, `request_address_change`) require a verified session
for the exact customer; `request_address_change` is additionally a **guarded
write** — only allowed before dispatch and gated by explicit customer
confirmation.

- `mcp_server/domains/registry.py` — single source of truth: maps each domain
  server to its tools, with access-tier metadata.
- `mcp_server/domains/{crm,oms,helpdesk,store}_server.py` — the four server
  entrypoints (shared runner in `domain_server.py`).
- `mcp_server/tools/authorization.py` — authorization validated against the
  shared, DB-persisted OTP challenge records, so it holds across process
  boundaries. A session verified for customer A cannot read customer B's data.
- `app/mcp/client.py` — synchronous multi-server facade the agent uses. Routes
  each tool call to its owning domain server over stdio; set `MCP_INPROCESS=1`
  to call the registry directly (faster for tests). Both paths enforce the same
  authorization.

Run a domain server standalone (for debugging):

```powershell
python scripts/run_mcp_server.py crm      # or: oms | helpdesk | store
```

- [ ] Phase 3 — MCP server + tools
- [ ] Phase 4 — OpenRouter + LangGraph agent + intent detection
- [ ] Phase 5 — RAG (ChromaDB + BM25 hybrid) policy-grounded answers
- [ ] Phase 6 — Conversation memory + guardrails + conditional routing
- [ ] Phase 7 — Human support dashboard + escalation handoff
- [ ] Phase 8 — Tracing/audit logs + golden tests + regression suite

## Acceptance criteria (spec §20)

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Customer can open the web app and start a conversation | ✅ | `/` serves the chat UI; `/api/chat` runs the agent |
| 2 | Agent identifies customer intent | ✅ | `detect` node + `test_workflow.py`, golden rule-2 cases |
| 3 | Policy questions answered using RAG | ✅ | `app/rag/*`; `test_rag.py`, golden rule-3 cases |
| 4 | Order queries trigger OTP verification | ✅ | account intents set `authentication_required`; golden rule-1 |
| 5 | Development OTP 123456 works | ✅ | `test_auth.py` valid-OTP cases; live verify → 200 |
| 6 | Invalid OTPs are rejected | ✅ | `test_auth.py` invalid/expiry/attempts; live invalid → 400 |
| 7 | Order/shipment retrieved via MCP tools | ✅ | `mcp_server/tools/*`; `test_mcp_tools.py`, `test_mcp_transport.py` |
| 8 | Unverified customers cannot access private data | ✅ | tool authorization + cross-customer block; `test_mcp_tools.py` |
| 9 | Prompt injection does not bypass backend restrictions | ✅ | golden rule-10, `test_guardrails.py` |
| 10 | Agent can create support tickets | ✅ | `create_support_ticket` / `escalate_to_human`; `test_dashboard.py` |
| 11 | Human dashboard shows escalated tickets + history | ✅ | `/dashboard`; `test_dashboard.py` |
| 12 | Agent can hand off sensitive/unresolved cases | ✅ | escalation node + auto-ticket; golden rules 6/7, `test_dashboard.py` |
| 13 | Conversation memory works across messages | ✅ | `app/memory/*`; `test_memory.py` |
| 14 | Audit logs record events without exposing secrets | ✅ | `app/observability/tracing.py`; `test_tracing.py` redaction |
| 15 | pytest regression suite passes | ✅ | `pytest -m regression` → 39 passed; full suite 163 passed |
| 16 | App starts locally on Windows 11 with documented commands | ✅ | Setup section above; verified on Python 3.12 |

### Honest notes / scope

- **Tests mock the LLM** (free, fast, deterministic). All four providers
  (OpenAI, Groq, OpenRouter, Ollama) were additionally verified with live
  smoke tests.
- **Salesforce** ticket integration is wired and unit-tested with mocks; it has
  not been validated against a live org (needs full OAuth credentials). Local
  SQLite is the default, so the demo is self-contained.
- The **dashboard and admin views have no authentication of their own** (demo
  scope); in production they would sit behind staff/admin auth.
- Language detection and the most fluent replies rely on a configured LLM; with
  no key, the agent falls back to deterministic, still-safe behaviour.

## Guardrails & compliance

### Guardrail coverage (enforced in code)

**Input** — prompt-injection/jailbreak patterns (incl. pasted email/web text)
are detected, logged, and ignored while the backend restrictions still hold;
abuse/threats trigger a single calm reply then escalation on repeat; pasted
card numbers, Aadhaar, OTPs or passwords are detected, never stored, and the
customer is told not to share them.

**Identity / Tool** — any order/account action without a verified session is
blocked and prompts for OTP; tool arguments that don't match the verified
customer or exceed rate limits are refused and logged.

**Output** — unmasked PII is redacted; unsupported refund / discount /
compensation promises and unverified delivery-date commitments are replaced
with a safe handoff; policy claims with no retrieved source become "I'm not
sure" + a human offer; medical/legal/financial advice is declined.

**Escalate to a human** on: explicit request; unresolved after two attempts;
lawyer / consumer court / injury / safety / fraud / chargeback; low detection
confidence; a backend tool failing twice; or out-of-scope requests (refund
approval, payment change).

Guardrail events are written to `logs/guardrail_events.log`; per-tool calls to
`logs/tool_calls.log`; workflow traces to `logs/audit.log` — all with secrets
and PII redacted.

### Security & compliance status

| # | Requirement | Status in this project |
|---|---|---|
| G-1 | Encrypt in transit & at rest; secrets in a vault | **Deployment requirement.** The demo runs locally over HTTP with SQLite and reads secrets from `.env`. For production: terminate TLS, encrypt the DB/volume at rest, and load secrets from a vault (e.g. AWS Secrets Manager / Vault) instead of `.env`. |
| G-2 | DPDP Act + GDPR (consent, purpose limits, deletion) | **Deployment requirement.** Not implemented in the demo. Production needs a consent banner, purpose-limited data use, and a deletion/export workflow for customer data. |
| G-3 | Never see/store full card numbers (PCI scope with provider) | **Met.** No payment flow exists; card numbers are detected and masked at input and output and are never stored. |
| G-4 | Audit log of conversations, tool calls, guardrail triggers, handoffs; 12-month retention + role-based access | **Partially met.** All four log streams exist (`logs/`). Production must add a 12-month retention policy and role-based access to the logs (out of scope for a local demo). |
| G-5 | Tell customers it's an AI; let them reach a human anytime | **Met.** The chat header shows an "AI" tag and the greeting discloses it's an AI and that a human is available at any time; escalation to a human works on request. |

These are called out honestly: the application-level guardrails and G-3/G-5 are
implemented and tested; G-1, G-2, and the retention/RBAC part of G-4 are
infrastructure/governance concerns to be satisfied at deployment, not faked in
application code.

## Memory model (M-1 .. M-6)

Three memory tiers, deliberately separated:

| Tier | Stores | Where | Retention |
|---|---|---|---|
| **Working (per session)** | rolling summary + structured facts (intent, active order, language, tool-result status, last ≤10 raw turns) | Redis (or local fallback) | expires after `SESSION_TTL_SECONDS` idle (30 min) |
| **Episodic (per customer)** | one-paragraph summary + outcome of each past issue | relational DB (SQLite / Postgres) | 12 months, then purged |
| **Preference (per customer)** | preferred language, contact channel | relational DB | until the customer deletes it |
| **Team knowledge** | approved edge-case answers (support leads) | RAG index (`knowledge_base_team/`) | monthly review |

Rules enforced:
- **M-1** — working memory persists a **summary + structured facts, not long raw
  chat text**; summaries never include card/OTP/phone/email values.
- **M-2** — verification status is **never** written to working memory; it lives
  only in the backend session and is re-derived each turn.
- **M-3** — episodic/preference memory is **keyed by customer id**; one
  customer's lookup can never return another's (tested).
- **M-4** — customers can **see / correct / delete** their memory via
  `GET /api/memory`, `PUT /api/memory/preferences`, `DELETE /api/memory`
  (verified session only); completes immediately (≤30-day SLA).
- **M-5** — memory is **context only**; a fresh tool result or retrieved policy
  **overrides** a remembered fact (enforced in code + stated in the prompt).
- **M-6** — sessions are **summarized after 10 turns** to keep context short.

Modules: `app/memory/` (`conversation_memory`, `summarizer`, stores),
`app/models/customer_memory.py`, `app/services/customer_memory_service.py`,
`app/routes/memory_routes.py`. Run `python scripts/purge_memory.py` to apply the
12-month retention (schedule it daily in production).

### Deployment note
Working memory uses Redis when `REDIS_URL` is set (local fallback otherwise),
and episodic/preference memory uses the DB in `DATABASE_URL` (SQLite in the
demo, Postgres in production). The 12-month purge should run on a scheduler.
