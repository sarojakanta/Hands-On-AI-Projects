# Architecture — Saroj SF Gen AI Agent

A Flask web app where an LLM agent answers natural-language questions about
Salesforce by calling tools against live CRM data.

---

## Component diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                           BROWSER (UI)                                 │
│   templates/index.html  +  static/style.css                           │
│                                                                        │
│   ┌─────────────┐   ┌──────────────────────┐   ┌──────────────────┐   │
│   │  Sidebar    │   │   Chat (center)       │   │  Right panel     │   │
│   │  - nav      │   │   - streaming bubbles │   │  - Last Query    │   │
│   │  - quick    │   │   - record tables     │   │    (SOQL)        │   │
│   │    queries  │   │   - tool badges       │   │  - Live gauges   │   │
│   │  - health   │   │                       │   │  - Tips          │   │
│   └─────────────┘   └──────────────────────┘   └──────────────────┘   │
│   Dashboard view: KPIs, charts, recommendations                        │
└───────────────┬────────────────────────────────────────────────────────┘
                │  HTTP / SSE (EventSource)
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      app.py  (Flask server)                            │
│   /                 chat + dashboard UI                                │
│   /api/stream       streaming chat (Server-Sent Events)                │
│   /api/chat         synchronous chat                                   │
│   /api/dashboard    live CRM metrics + recommendations                 │
│   /api/suggestions  quick-query prompts                                │
│   /api/health       provider / connectivity status                     │
│   /api/share        shareable text summary                             │
└───────────────┬────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      agent.py  (SalesforceAgent)                       │
│                                                                        │
│   Agentic loop:                                                        │
│     user prompt → LLM → tool calls → results → LLM → ... → answer      │
│                                                                        │
│   ┌────────────────┐   ┌────────────────┐   ┌─────────────────────┐    │
│   │ LLM provider   │   │ Tool dispatcher│   │ Conversation memory │    │
│   │ resolver       │   │ (12 tools)     │   │ (per session)       │    │
│   │ openai/groq/.. │   │                │   │                     │    │
│   └───────┬────────┘   └───────┬────────┘   └─────────────────────┘    │
│           │                    │                                       │
│           ▼                    │          Fallback (no key):           │
│   ┌────────────────┐           │          rule-based querying          │
│   │  LLM API       │           │                                       │
│   │ (OpenAI-compat)│           │                                       │
│   └────────────────┘           ▼                                       │
└────────────────────────────────┼──────────────────────────────────────┘
                                  │  (MCP_MODE routes tool calls)
          ┌───────────────────────┼───────────────────────┐
          ▼                       ▼                       ▼
   MCP_MODE=none           MCP_MODE=local          MCP_MODE=remote
   SalesforceTools         LocalMCPClient          RemoteMCPClient
   (direct)                → mcp_server.py          → HTTP MCP server
                             (stdio)                  (cloud)
          │                       │                       │
          └───────────────────────┼───────────────────────┘
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  salesforce_client.py  (data layer)                    │
│   CLI backend  (sf subprocess)   |   REST backend (simple-salesforce)  │
│   query · create · update · delete · schema · aggregates               │
└───────────────┬────────────────────────────────────────────────────────┘
                │
                ▼
        ┌───────────────┐
        │  Salesforce   │
        │     Org       │
        └───────────────┘
```

---

## Components

| Component | Responsibility |
|-----------|----------------|
| **UI** (`index.html`, `style.css`) | Chat + Dashboard, streaming display, SOQL inspector, gauges |
| **Flask server** (`app.py`) | HTTP + SSE endpoints, dashboard metrics, health, share |
| **Agent** (`agent.py`) | Agentic tool-calling loop, LLM provider resolution, memory, fallback |
| **Tool dispatcher** | 12 tools: query_soql, get_cases/accounts/opportunities/contacts/leads/tasks, aggregate_soql, get_object_schema, create/update/delete_record |
| **Salesforce client** (`salesforce_client.py`) | Executes queries/DML via sf CLI or REST; "me" resolution, schema cache |
| **MCP server** (`mcp_server.py`) | Optional — exposes the same tools over stdio/HTTP |

---

## Request flow (streaming chat)

```
1. User types a question in the chat.
2. Browser opens an SSE connection to /api/stream.
3. agent.py sends the prompt + tool specs to the LLM.
4. LLM responds with tool calls (e.g. get_cases{is_closed:false}).
5. Tool dispatcher runs them via salesforce_client → Salesforce org.
6. Results feed back to the LLM; loop repeats until a final answer.
7. Answer streams to the UI; records render as a table; SOQL shows in the right panel.
```

---

## Key design choices

- **Agentic, not hardcoded** — the LLM chooses tools and chains them, rather than
  fixed keyword routing.
- **Provider-agnostic LLM** — one `LLM_PROVIDER` env var switches between any
  OpenAI-compatible endpoint; no code change.
- **Pluggable execution** — `MCP_MODE` switches tool execution between direct,
  local MCP (stdio), and remote MCP (HTTP) without touching agent logic.
- **Dual Salesforce backend** — sf CLI for local dev, REST (simple-salesforce)
  for server/cloud deployments.
- **Graceful fallback** — works without an LLM key using rule-based querying.
