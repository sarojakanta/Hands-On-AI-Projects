"""Tests for the Ollama provider (native API). All HTTP is mocked."""

from __future__ import annotations

import httpx
import pytest

from app.llm.errors import (
    ModelNotFoundError,
    ProviderUnavailableError,
    StreamInterruptedError,
)
from app.llm.ollama import OllamaProvider
from app.llm.retry import RetryPolicy
from tests.helpers import (
    MESSAGES,
    PARAMS,
    SleepRecorder,
    collect,
    json_response,
    mock_client,
    ndjson_response,
    scripted,
)

pytestmark = pytest.mark.anyio

TAGS = {
    "models": [
        {"name": "qwen2:0.5b", "size": 352_000_000},
        {"name": "llama3.1:latest", "size": 4_920_753_328},
    ]
}

CHAT = {
    "model": "llama3.1:latest",
    "message": {"role": "assistant", "content": "Hi there"},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 7,
    "eval_count": 2,
}


def build(handler, *, max_retries=2, **kwargs):
    client, calls = mock_client(handler)
    sleeper = SleepRecorder()
    provider = OllamaProvider(
        base_url="http://ollama.test:11434",
        default_model="llama3.1:latest",
        client=client,
        sleep=sleeper,
        retry_policy=RetryPolicy(max_retries=max_retries, jitter=0.0),
        **kwargs,
    )
    return provider, calls, sleeper


async def test_needs_no_api_key_and_sends_none():
    provider, calls, _ = build(scripted(json_response(CHAT)))
    assert provider.is_configured and not provider.requires_api_key and provider.allows_base_url_override
    await provider.generate(MESSAGES, PARAMS)
    assert calls.header("authorization") is None


async def test_generate_maps_response_and_sends_native_payload():
    provider, calls, _ = build(scripted(json_response(CHAT)), keep_alive="10m")

    result = await provider.generate(MESSAGES, PARAMS)

    assert result.text == "Hi there" and result.provider == "ollama"
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (7, 2)
    assert result.finish_reason == "stop"
    assert calls.path() == "/api/chat"
    body = calls.body()
    assert body["stream"] is False   # Ollama streams by default, so it must always be explicit
    assert body["model"] == "m1"
    assert body["options"] == {"temperature": 0.3, "num_predict": 50}
    assert body["keep_alive"] == "10m"


async def test_list_models_reports_installed_models():
    provider, calls, _ = build(scripted(json_response(TAGS)))
    models = await provider.list_models()
    assert [m.id for m in models] == ["llama3.1:latest", "qwen2:0.5b"]
    assert models[0].size_bytes == 4_920_753_328
    assert calls.path() == "/api/tags"


def test_model_names_match_with_or_without_latest_tag():
    provider, _, _ = build(scripted(json_response(TAGS)))
    installed = {"llama3.1:latest", "qwen2:0.5b"}
    assert provider.model_matches("llama3.1", installed)
    assert provider.model_matches("llama3.1:latest", installed)
    assert provider.model_matches("qwen2:0.5b", installed)
    assert not provider.model_matches("llama3.1:70b", installed)
    assert not provider.model_matches("mistral", installed)


async def test_missing_model_gives_an_actionable_error():
    provider, calls, _ = build(scripted(json_response({"error": "model 'nope' not found"}, 404)))
    with pytest.raises(ModelNotFoundError) as info:
        await provider.generate(MESSAGES, PARAMS.__class__(model="nope", temperature=0.1, max_tokens=5))
    assert "ollama pull nope" in info.value.message
    assert len(calls) == 1   # not retried


async def test_unreachable_server_is_reported_as_unavailable_and_retried():
    provider, calls, sleeper = build(scripted(httpx.ConnectError("refused")))
    with pytest.raises(ProviderUnavailableError) as info:
        await provider.generate(MESSAGES, PARAMS)
    assert "ollama.test:11434" in info.value.message
    assert len(calls) == 3 and sleeper.delays == [0.5, 1.0]


async def test_connection_test_distinguishes_missing_model_from_unreachable_server():
    ready, _, _ = build(scripted(json_response(TAGS)))
    status = await ready.test_connection()   # default model llama3.1:latest
    assert status.ok and status.model_available is True and status.model_count == 2

    by_short_name, _, _ = build(scripted(json_response(TAGS)))
    assert (await by_short_name.test_connection("llama3.1")).ok

    missing, _, _ = build(scripted(json_response(TAGS)))
    status = await missing.test_connection("mistral")
    assert not status.ok and status.error_code == "model_not_found"

    down, _, _ = build(scripted(httpx.ConnectError("refused")), max_retries=0)
    status = await down.test_connection()
    assert not status.ok and status.error_code == "provider_unavailable"


async def test_stream_yields_tokens_and_final_usage():
    response = ndjson_response(
        [
            {"message": {"role": "assistant", "content": "Hel"}, "done": False},
            {"message": {"role": "assistant", "content": "lo"}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
             "prompt_eval_count": 4, "eval_count": 2},
        ]
    )
    provider, calls, _ = build(scripted(response))

    events = await collect(provider.stream(MESSAGES, PARAMS))

    assert [e.delta for e in events if e.delta] == ["Hel", "lo"]
    assert events[-1].done and events[-1].finish_reason == "stop"
    assert (events[-1].usage.prompt_tokens, events[-1].usage.completion_tokens) == (4, 2)
    assert calls.body()["stream"] is True


async def test_stream_error_object_before_output_is_retried():
    failing = ndjson_response([{"error": "llama runner process has terminated"}])
    ok = ndjson_response([{"message": {"content": "fine"}, "done": False}, {"done": True, "done_reason": "stop"}])
    provider, calls, _ = build(scripted(failing, ok))
    events = await collect(provider.stream(MESSAGES, PARAMS))
    assert events[0].delta == "fine" and len(calls) == 2


async def test_stream_error_object_after_output_interrupts_the_stream():
    broken = ndjson_response([{"message": {"content": "part"}, "done": False}, {"error": "out of memory"}])
    provider, calls, _ = build(scripted(broken))
    seen = []
    with pytest.raises(StreamInterruptedError):
        async for event in provider.stream(MESSAGES, PARAMS):
            seen.append(event.delta)
    assert seen == ["part"] and len(calls) == 1
