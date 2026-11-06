"""Test script for all LLM providers.

Tests:
1. Auto mode priority (groq -> openai -> anthropic -> openrouter)
2. Direct provider selection (openai, groq, anthropic, openrouter)
3. All major LLMs through OpenRouter gateway

Usage:
    # Test auto mode (needs at least one provider key configured)
    python scripts/test_llm_providers.py

    # Test specific provider
    python scripts/test_llm_providers.py --provider openai

    # Test all LLMs through OpenRouter
    python scripts/test_llm_providers.py --test-openrouter
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.services.llm_service import LLMService, LLMNotConfiguredError
from app.config import config


def test_basic_chat(service: LLMService, test_name: str) -> bool:
    """Test basic chat completion."""
    print(f"\n{'='*60}")
    print(f"TEST: {test_name}")
    print(f"{'='*60}")
    print(f"Provider: {service.provider}")
    print(f"Model: {service.model}")
    print(f"Configured: {service.configured}")
    
    if not service.configured:
        print("[X] SKIP - Provider not configured (no API key)")
        return False
    
    try:
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Be concise."},
            {"role": "user", "content": "Say 'Hello, World!' and nothing else."}
        ]
        
        print("\nSending test message...")
        result = service.chat(messages, temperature=0.0, max_tokens=50)
        
        print(f"\n[OK] SUCCESS")
        print(f"Response: {result.text}")
        print(f"Latency: {result.latency_ms}ms")
        print(f"Actual Provider: {result.provider}")
        print(f"Actual Model: {result.model}")
        return True
        
    except LLMNotConfiguredError as e:
        print(f"\n[X] FAILED - Not configured: {e}")
        return False
    except Exception as e:
        print(f"\n[X] FAILED - Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_structured_output(service: LLMService, test_name: str) -> bool:
    """Test structured JSON output."""
    print(f"\n{'='*60}")
    print(f"TEST: {test_name} (Structured Output)")
    print(f"{'='*60}")
    
    if not service.configured:
        print("[X] SKIP - Provider not configured")
        return False
    
    try:
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Respond only with valid JSON."},
            {"role": "user", "content": 'Return a JSON object with fields: {"greeting": "Hello", "status": "success"}'}
        ]
        
        print("Sending structured query...")
        parsed, result = service.structured(messages, temperature=0.0, max_tokens=100)
        
        print(f"\n[OK] SUCCESS")
        print(f"Parsed JSON: {parsed}")
        print(f"Raw Response: {result.text}")
        print(f"Latency: {result.latency_ms}ms")
        return True
        
    except Exception as e:
        print(f"\n[X] FAILED - Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_auto_mode():
    """Test auto mode provider priority."""
    print("\n" + "="*70)
    print("TESTING AUTO MODE (Priority: groq -> openai -> anthropic -> openrouter)")
    print("="*70)
    
    service = LLMService(preference="auto")
    
    print(f"\nAuto mode selected: {service.provider}")
    print(f"Model: {service.model}")
    
    if not service.configured:
        print("\n[!]  No providers configured. Set at least one API key:")
        print("  - GROQ_API_KEY")
        print("  - OPENAI_API_KEY")
        print("  - ANTHROPIC_API_KEY")
        print("  - OPENROUTER_API_KEY")
        return False
    
    return test_basic_chat(service, "Auto Mode")


def test_provider(provider_name: str):
    """Test a specific provider."""
    service = LLMService(preference=provider_name)
    
    success = test_basic_chat(service, f"Provider: {provider_name.upper()}")
    
    if success:
        # Also test structured output
        test_structured_output(service, f"Provider: {provider_name.upper()}")
    
    return success


def test_openrouter_models():
    """Test multiple LLM models through OpenRouter gateway."""
    print("\n" + "="*70)
    print("TESTING MULTIPLE LLMS THROUGH OPENROUTER")
    print("="*70)
    
    if not config.OPENROUTER_API_KEY:
        print("\n[!]  OPENROUTER_API_KEY not configured. Skipping OpenRouter tests.")
        print("Set OPENROUTER_API_KEY in your .env file to test.")
        return
    
    # Save original config
    original_provider = os.environ.get("LLM_PROVIDER")
    original_model = os.environ.get("OPENROUTER_MODEL")
    
    # Models to test through OpenRouter
    test_models = [
        ("openai/gpt-4o-mini", "OpenAI GPT-4o Mini"),
        ("openai/gpt-3.5-turbo", "OpenAI GPT-3.5 Turbo"),
        ("anthropic/claude-3-5-sonnet", "Anthropic Claude 3.5 Sonnet"),
        ("anthropic/claude-3-haiku", "Anthropic Claude 3 Haiku"),
        ("google/gemini-pro-1.5", "Google Gemini Pro 1.5"),
        ("meta-llama/llama-3.1-8b-instruct", "Meta Llama 3.1 8B"),
        ("mistralai/mistral-7b-instruct", "Mistral 7B Instruct"),
    ]
    
    results = []
    
    for model_id, model_name in test_models:
        print(f"\n{'-'*60}")
        print(f"Testing: {model_name}")
        print(f"Model ID: {model_id}")
        print(f"{'-'*60}")
        
        # Update environment for this test
        os.environ["LLM_PROVIDER"] = "openrouter"
        os.environ["OPENROUTER_MODEL"] = model_id
        
        # Create fresh service instance
        service = LLMService(preference="openrouter")
        service.refresh()  # Reload config
        
        try:
            messages = [
                {"role": "system", "content": "You are a helpful assistant. Be very concise."},
                {"role": "user", "content": "Say only 'Test passed' and nothing else."}
            ]
            
            result = service.chat(messages, temperature=0.0, max_tokens=50)
            
            print(f"[OK] SUCCESS")
            print(f"   Response: {result.text}")
            print(f"   Latency: {result.latency_ms}ms")
            
            results.append({
                "model": model_name,
                "status": "[OK] PASSED",
                "latency": result.latency_ms,
                "response": result.text[:50]
            })
            
        except Exception as e:
            print(f"[X] FAILED: {e}")
            results.append({
                "model": model_name,
                "status": "[X] FAILED",
                "error": str(e)[:100]
            })
    
    # Restore original config
    if original_provider:
        os.environ["LLM_PROVIDER"] = original_provider
    if original_model:
        os.environ["OPENROUTER_MODEL"] = original_model
    
    # Print summary
    print("\n" + "="*70)
    print("OPENROUTER TEST SUMMARY")
    print("="*70)
    for r in results:
        print(f"\n{r['model']}: {r['status']}")
        if "latency" in r:
            print(f"  Latency: {r['latency']}ms")
            print(f"  Response: {r['response']}")
        if "error" in r:
            print(f"  Error: {r['error']}")


def main():
    parser = argparse.ArgumentParser(description="Test LLM providers")
    parser.add_argument(
        "--provider",
        choices=["auto", "openai", "groq", "anthropic", "openrouter", "ollama"],
        help="Test specific provider"
    )
    parser.add_argument(
        "--test-openrouter",
        action="store_true",
        help="Test multiple LLMs through OpenRouter"
    )
    parser.add_argument(
        "--test-all",
        action="store_true",
        help="Test all configured providers"
    )
    
    args = parser.parse_args()
    
    print("\n" + "LLM PROVIDER TEST SUITE")
    print("="*70)
    print(f"Config loaded from: {project_root / '.env'}")
    print(f"DEV_MODE: {config.DEV_MODE}")
    print(f"LLM_PROVIDER setting: {config.LLM_PROVIDER}")
    
    if args.test_openrouter:
        test_openrouter_models()
        return
    
    if args.test_all:
        providers = ["auto", "openai", "groq", "anthropic", "openrouter"]
        for provider in providers:
            test_provider(provider)
        return
    
    if args.provider:
        test_provider(args.provider)
        return
    
    # Default: test auto mode
    test_auto_mode()


if __name__ == "__main__":
    main()
