# Environment Configuration Guide

## LLM Provider Configuration (Updated with Anthropic)

### Provider Priority (LLM_PROVIDER=auto)

When `LLM_PROVIDER=auto`, the system tries providers in this order:
1. **Groq** (if GROQ_API_KEY is set)
2. **OpenAI** (if OPENAI_API_KEY is set)
3. **Anthropic** (if ANTHROPIC_API_KEY is set) ← NEW!
4. **OpenRouter** (if OPENROUTER_API_KEY is set)

The first provider with a valid API key is used.

---

## Configuration Variables

### LLM_PROVIDER
```env
# Options: auto | openai | groq | anthropic | openrouter | ollama
LLM_PROVIDER=auto
```

### OpenAI (Direct)
```env
OPENAI_API_KEY=sk-xxxxx
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=  # Optional: leave blank for default
```

### Groq (Direct)
```env
GROQ_API_KEY=gsk_xxxxx
GROQ_MODEL=llama-3.1-70b-versatile
GROQ_BASE_URL=  # Optional: leave blank for default
```

### Anthropic (Direct - Native SDK) ← NEW!
```env
ANTHROPIC_API_KEY=sk-ant-xxxxx
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_BASE_URL=  # Optional: leave blank for default

# Available models:
#  - claude-3-5-sonnet-20241022 (recommended)
#  - claude-3-opus-20240229
#  - claude-3-haiku-20240307
```

### OpenRouter (Gateway - Access Multiple Models)
```env
OPENROUTER_API_KEY=sk-or-xxxxx
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_BASE_URL=  # Optional: leave blank for default
OPENROUTER_APP_NAME=AI Customer Support Agent
OPENROUTER_SITE_URL=http://localhost:5000

# Popular models through OpenRouter:
#  - openai/gpt-4o-mini
#  - openai/gpt-3.5-turbo
#  - anthropic/claude-3-5-sonnet
#  - anthropic/claude-3-haiku
#  - google/gemini-pro-1.5
#  - meta-llama/llama-3.1-8b-instruct
#  - mistralai/mistral-7b-instruct
```

---

## Quick Start Examples

### Example 1: Use Anthropic Claude Directly
```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

### Example 2: Use Anthropic Through OpenRouter (No separate Anthropic key needed)
```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-your-key-here
OPENROUTER_MODEL=anthropic/claude-3-5-sonnet
```

### Example 3: Auto Mode with Multiple Providers
```env
LLM_PROVIDER=auto

# Set multiple keys - first available wins
GROQ_API_KEY=gsk_xxxxx
OPENAI_API_KEY=sk-xxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxx
OPENROUTER_API_KEY=sk-or-xxxxx
```

### Example 4: Test Multiple Models Through OpenRouter
```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-xxxxx

# Change model anytime:
# OPENROUTER_MODEL=openai/gpt-4o-mini
# OPENROUTER_MODEL=anthropic/claude-3-5-sonnet
# OPENROUTER_MODEL=google/gemini-pro-1.5
OPENROUTER_MODEL=anthropic/claude-3-haiku
```

---

## Testing LLM Providers

### Test Auto Mode
```bash
python scripts/test_llm_providers.py
```

### Test Specific Provider
```bash
python scripts/test_llm_providers.py --provider anthropic
python scripts/test_llm_providers.py --provider openai
python scripts/test_llm_providers.py --provider groq
python scripts/test_llm_providers.py --provider openrouter
```

### Test All Configured Providers
```bash
python scripts/test_llm_providers.py --test-all
```

### Test Multiple LLMs Through OpenRouter
```bash
python scripts/test_llm_providers.py --test-openrouter
```

This tests 7 different models through a single OpenRouter API key:
- OpenAI GPT-4o Mini
- OpenAI GPT-3.5 Turbo
- Anthropic Claude 3.5 Sonnet
- Anthropic Claude 3 Haiku
- Google Gemini Pro 1.5
- Meta Llama 3.1 8B
- Mistral 7B Instruct

---

## Other Configuration Variables

See your existing `.env` file for:
- Flask settings (SECRET_KEY, HOST, PORT, DEV_MODE)
- Database (DATABASE_URL)
- Authentication (DEV_OTP, OTP_TTL_SECONDS, OTP_MAX_ATTEMPTS)
- RAG settings (EMBEDDING_MODEL, CHROMA_DIR, KNOWLEDGE_BASE_DIR)
- Memory (REDIS_URL, SESSION_TTL_SECONDS)
- MCP Server (MCP_SERVER_HOST, MCP_SERVER_PORT)
- Tickets (TICKET_PROVIDER, Salesforce credentials)
- Logging (LOG_DIR, LOG_LEVEL, LOG_MAX_MB)

---

## Notes

### Anthropic Native SDK
- Uses native Anthropic Python SDK (not OpenAI-compatible)
- Automatically handles different message format
- Requires: `pip install anthropic==0.39.0`

### httpx Version
- Requires `httpx==0.27.2` for compatibility
- Already in requirements.txt

### .env File
- Copy your existing `.env.example` to `.env`
- Add the new Anthropic variables shown above
- Never commit `.env` to version control
