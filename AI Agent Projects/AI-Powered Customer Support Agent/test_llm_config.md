# LLM Provider Testing Guide

## What Has Been Implemented

### 1. **Anthropic Support Added**
- Native Anthropic SDK integration in `app/services/llm_service.py`
- Configuration variables in `app/config.py`
- Auto mode priority: `groq -> openai -> anthropic -> openrouter`

### 2. **Files Modified**
- ✅ `app/config.py` - Added ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_BASE_URL
- ✅ `app/services/llm_service.py` - Added Anthropic provider support with native SDK
- ✅ `requirements.txt` - Added `anthropic==0.39.0` package
- ✅ `scripts/test_llm_providers.py` - Comprehensive test script created

---

## How to Test

### Installation
```bash
# Install the new anthropic package
pip install anthropic==0.39.0
```

---

## Test Configurations

### Option 1: Test AUTO Mode (Recommended First)

**Update your `.env` file with ONE provider:**

```env
# Test with Groq (will be selected first in auto mode)
LLM_PROVIDER=auto
GROQ_API_KEY=your_groq_key_here
GROQ_MODEL=llama-3.1-70b-versatile
```

**Run test:**
```bash
python scripts/test_llm_providers.py
```

Expected output: Should use Groq provider

---

### Option 2: Test Anthropic Direct

**Update your `.env` file:**

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxxx
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

**Run test:**
```bash
python scripts/test_llm_providers.py --provider anthropic
```

---

### Option 3: Test All Providers

**Update your `.env` file with ALL keys:**

```env
LLM_PROVIDER=auto

# OpenAI
OPENAI_API_KEY=sk-xxxxx
OPENAI_MODEL=gpt-4o-mini

# Groq
GROQ_API_KEY=gsk_xxxxx
GROQ_MODEL=llama-3.1-70b-versatile

# Anthropic
ANTHROPIC_API_KEY=sk-ant-xxxxx
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# OpenRouter
OPENROUTER_API_KEY=sk-or-xxxxx
OPENROUTER_MODEL=openai/gpt-4o-mini
```

**Run tests:**
```bash
# Test all providers individually
python scripts/test_llm_providers.py --test-all
```

---

### Option 4: Test Multiple LLMs Through OpenRouter

**This is the easiest way to test ALL models without multiple API keys!**

**Update your `.env` file:**

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-xxxxx
OPENROUTER_MODEL=openai/gpt-4o-mini
```

**Run OpenRouter multi-model test:**
```bash
python scripts/test_llm_providers.py --test-openrouter
```

This will test:
- ✅ OpenAI GPT-4o Mini
- ✅ OpenAI GPT-3.5 Turbo
- ✅ Anthropic Claude 3.5 Sonnet
- ✅ Anthropic Claude 3 Haiku
- ✅ Google Gemini Pro 1.5
- ✅ Meta Llama 3.1 8B
- ✅ Mistral 7B Instruct

All through a single OpenRouter API key!

---

## Quick Test Commands

```bash
# Test auto mode (uses first available provider)
python scripts/test_llm_providers.py

# Test specific provider
python scripts/test_llm_providers.py --provider openai
python scripts/test_llm_providers.py --provider groq
python scripts/test_llm_providers.py --provider anthropic
python scripts/test_llm_providers.py --provider openrouter

# Test all configured providers
python scripts/test_llm_providers.py --test-all

# Test multiple models through OpenRouter (recommended!)
python scripts/test_llm_providers.py --test-openrouter
```

---

## Expected Test Output

### Successful Test:
```
============================================================
TEST: Provider: ANTHROPIC
============================================================
Provider: anthropic
Model: claude-3-5-sonnet-20241022
Configured: True

Sending test message...

✅ SUCCESS
Response: Hello, World!
Latency: 1234ms
Actual Provider: anthropic
Actual Model: claude-3-5-sonnet-20241022
```

### Provider Not Configured:
```
❌ SKIP - Provider not configured (no API key)
```

---

## Testing Through OpenRouter (Easiest Option)

**You can test Anthropic models WITHOUT an Anthropic API key by using OpenRouter:**

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_key
OPENROUTER_MODEL=anthropic/claude-3-5-sonnet
```

Then run the app normally - it will use Claude through OpenRouter!

---

## Verify Integration in the App

After testing with the script, verify it works in the actual app:

```bash
# Start the app
python run.py

# Visit http://localhost:5000
# Chat with the assistant
# Check the logs to see which provider is being used
```

---

## Auto Mode Priority (LLM_PROVIDER=auto)

When `LLM_PROVIDER=auto`, the system checks in this order:

1. **Groq** (if GROQ_API_KEY exists) ← checked first
2. **OpenAI** (if OPENAI_API_KEY exists)
3. **Anthropic** (if ANTHROPIC_API_KEY exists) ← NEW!
4. **OpenRouter** (if OPENROUTER_API_KEY exists) ← checked last

The first provider with a valid API key wins.

---

## Troubleshooting

### anthropic package not found
```bash
pip install anthropic==0.39.0
```

### No provider configured
Make sure at least one API key is set in your `.env` file.

### OpenRouter test fails
- Verify OPENROUTER_API_KEY is correct
- Check if you have credits at https://openrouter.ai/
- Some models may require additional approval

---

## Summary

✅ Anthropic support implemented with native SDK  
✅ Works in auto mode (3rd priority after groq and openai)  
✅ Can be forced with `LLM_PROVIDER=anthropic`  
✅ Can use Anthropic models through OpenRouter too  
✅ Test script covers all scenarios  
✅ Requirements.txt updated with anthropic package  

**Recommended Test Flow:**
1. Run `python scripts/test_llm_providers.py --test-openrouter` (easiest - tests all models with one key)
2. Then test individual providers with `--test-all`
3. Finally test auto mode to verify priority order
