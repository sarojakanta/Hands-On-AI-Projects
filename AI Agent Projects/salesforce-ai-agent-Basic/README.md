# Saroj SF Gen AI Agent

An agentic AI assistant for Salesforce. Ask questions about your CRM in plain
English and get answers, tables, and live dashboards — powered by an LLM that
reasons and calls Salesforce tools on your behalf.

---

## What it does

- **Conversational querying** — ask about Cases, Accounts, Opportunities,
  Contacts, Leads, Tasks (and any object via SOQL) in natural language.
- **Agentic tool-calling** — the LLM decides which tools to run, chains them,
  and answers from live Salesforce data.
- **Smart filters** — status, priority, open/closed, owner, creator ("raised by me"),
  text search, grouping and counts.
- **Live dashboard** — KPIs, pipeline, cases by priority, accounts by industry,
  leads by status, plus auto-generated recommendations.
- **Streaming chat UI** — responses stream token-by-token with live tool badges.
- **SOQL inspector** — the exact query runs in the right-side panel (not shown in chat).
- **Multi-provider LLM** — switch between OpenAI, Groq, Claude (and others) via one env var.
- **Fallback mode** — works without any LLM key using rule-based querying.

---

## Setup

1. Create and activate a virtual environment, then install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Authenticate the Salesforce CLI (`sf`):

   ```bash
   sf org login web --set-default
   ```

3. Copy the env template and fill in your values:

   ```bash
   copy .env.example .env
   ```

4. Run the web app:

   ```bash
   python app.py
   ```

   Open http://localhost:5000

   Or use the CLI:

   ```bash
   python agent.py "show me all open cases"
   ```

---

## Configuration (`.env`)

Only three LLM settings matter. The base URL is auto-filled from the provider.

```bash
# LLM — pick one provider
LLM_PROVIDER=groq            # openai | groq | claude | together | ollama | gemini | custom
LLM_API_KEY=gsk_...          # key for that provider (blank = fallback mode)
LLM_MODEL=openai/gpt-oss-120b

# Salesforce
SF_USERNAME=you@example.com

# MCP mode (optional)
MCP_MODE=none                # none | local | remote
```

| Provider | LLM_PROVIDER | Example model |
|----------|-------------|---------------|
| OpenAI   | `openai`    | `gpt-4o-mini` |
| Groq     | `groq`      | `openai/gpt-oss-120b` |
| Claude   | `claude`    | `claude-sonnet-4-20250514` |

To run **without an LLM**, leave `LLM_API_KEY` blank — the agent uses rule-based
fallback (basic listing still works).

---

## Example prompts

- Show me all open cases
- Show all cases with status as New
- Show me all cases created by me
- List all open high-priority cases
- List all accounts in the Technology industry
- How many open cases do we have per account?

---

## Project structure

| File | Role |
|------|------|
| `app.py` | Flask web server + REST/streaming API endpoints |
| `agent.py` | Agentic loop, LLM provider config, tools, conversation memory |
| `salesforce_client.py` | Salesforce data layer (sf CLI or REST) |
| `mcp_server.py` | Optional MCP server exposing the same tools |
| `templates/index.html` | Chat + Dashboard UI |
| `static/style.css` | Styling |

See `ARCHITECTURE.md` for the design and component diagram.

---

## Notes

- The Salesforce CLI must be installed and on your PATH for `MCP_MODE=none`/`local`.
- Some advanced queries (date ranges, "no activity" sub-queries) depend on model
  quality and Salesforce SOQL limits.
- Groq free tier has rate limits; rapid repeated queries may be throttled.
