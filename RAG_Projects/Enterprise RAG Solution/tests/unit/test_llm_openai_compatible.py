"""Tests for the OpenAI-compatible provider family: generic, Groq and OpenAI. All HTTP is mocked."""

from __future__ import annotations

import logging

import httpx
import pytest
from pydantic import SecretStr

from app.llm.errors import (
    AuthenticationError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
    StreamInterruptedError,
)
from app.llm.groq import GroqProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.openai_provider import OpenAIProvider
from app.llm.retry import RetryPolicy
from tests.helpers import (
    MESSAGES,
    PARAMS,
    SleepRecorder,
    collect,
    json_response,
    mock_client,
    scripted,
    sse_response,
)

pytestmark = pytest.mark.anyio

KEY = "sk-test-secret-key-1234"
BASE = "https://api.example.com/v1"

COMPLETION = {
    "id": "c1",
    "model": "gpt-x",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
}


def build(handler, cls=OpenAICompatibleProvider, *, key=KEY, max_retries=2, **kwargs):
    client, calls = mock_client(handler)
    sleeper = SleepRecorder()
    common = {
        "base_url": BASE,
        "api_key": SecretStr(key) if key is not None else None,
        "default_model": "m1",
        "client": client,
        "sleep": sleeper,
        "retry_policy": RetryPolicy(max_retries=max_retries, jitter=0.0),
    }
    if cls is OpenAICompatibleProvider:
        common["name"] = "custom"
    provider = cls(**{**common, **kwargs})
    return provider, calls, sleeper


def chunks(*payloads):
    return sse_response([(None, p) for p in payloads] + [(None, "[DONE]")])


def delta(text, **extra):
    return {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}], **extra}


# --------------------------------------------------------------------------- non-streaming


async def test_generate_returns_text_usage_and_sends_a_correct_request():
    provider, calls, _ = build(scripted(json_response(COMPLETION)))

    result = await provider.generate(MESSAGES, PARAMS)

    assert result.text == "Hello!"
    assert result.provider == "custom" and result.model == "gpt-x"
    assert (result.usage.prompt_tokens, result.usage.completion_tokens, result.usage.total_tokens) == (11, 3, 14)
    assert result.finish_reason == "stop" and result.attempts == 1 and result.latency_ms >= 0

    assert calls.path() == "/v1/chat/completions"
    assert calls.requests[0].method == "POST"
    assert calls.header("authorization") == f"Bearer {KEY}"
    body = calls.body()
    assert body["model"] == "m1" and body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": "Hello"}]
    assert body["temperature"] == 0.3 and body["max_tokens"] == 50
    assert "max_completion_tokens" not in body


async def test_openai_uses_max_completion_tokens_and_groq_uses_max_tokens():
    openai, calls, _ = build(scripted(json_response(COMPLETION)), OpenAIProvider)
    await openai.generate(MESSAGES, PARAMS)
    assert calls.body()["max_completion_tokens"] == 50 and "max_tokens" not in calls.body()
    assert openai.name == "openai"

    groq, calls, _ = build(scripted(json_response(COMPLETION)), GroqProvider)
    await groq.generate(MESSAGES, PARAMS)
    assert calls.body()["max_tokens"] == 50
    assert groq.name == "groq"


async def test_missing_usage_and_null_content_are_tolerated():
    data = {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}
    provider, _, _ = build(scripted(json_response(data)))
    result = await provider.generate(MESSAGES, PARAMS)
    assert result.text == "" and result.usage is None and result.finish_reason == "length"


async def test_response_without_choices_is_an_invalid_response():
    provider, _, _ = build(scripted(json_response({"choices": []})))
    with pytest.raises(Exception) as info:
        await provider.generate(MESSAGES, PARAMS)
    assert info.value.code == "provider_invalid_response"


@pytest.mark.parametrize("key", [None, "", "   "])
async def test_unconfigured_provider_fails_before_any_request(key):
    provider, calls, _ = build(scripted(json_response(COMPLETION)), key=key)
    assert not provider.is_configured
    with pytest.raises(ProviderNotConfiguredError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 0


async def test_keyless_provider_works_without_a_key_and_sends_no_auth_header():
    provider, calls, _ = build(scripted(json_response(COMPLETION)), key=None, requires_api_key=False)
    assert provider.is_configured
    await provider.generate(MESSAGES, PARAMS)
    assert calls.header("authorization") is None


# --------------------------------------------------------------------------- errors and retries


async def test_authentication_failure_is_not_retried():
    provider, calls, sleeper = build(scripted(json_response({"error": {"message": "Invalid API key"}}, 401)))
    with pytest.raises(AuthenticationError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 1 and sleeper.delays == []


async def test_rate_limit_is_retried_after_the_advertised_delay():
    provider, calls, sleeper = build(
        scripted(
            json_response({"error": {"message": "Rate limit reached"}}, 429, {"retry-after": "2"}),
            json_response(COMPLETION),
        )
    )
    result = await provider.generate(MESSAGES, PARAMS)
    assert result.text == "Hello!" and result.attempts == 2
    assert sleeper.delays == [2.0] and len(calls) == 2


async def test_quota_exhaustion_is_not_retried():
    body = {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}
    provider, calls, _ = build(scripted(json_response(body, 429)))
    with pytest.raises(QuotaExceededError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 1


async def test_server_errors_use_exponential_backoff_then_give_up():
    provider, calls, sleeper = build(scripted(json_response({"error": {"message": "oops"}}, 500)))
    with pytest.raises(ProviderUnavailableError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 3 and sleeper.delays == [0.5, 1.0]


async def test_timeouts_and_connection_failures_are_mapped_and_retried():
    provider, calls, _ = build(scripted(httpx.ReadTimeout("slow")))
    with pytest.raises(ProviderTimeoutError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 3

    provider, calls, _ = build(scripted(httpx.ConnectError("refused")), max_retries=0)
    with pytest.raises(ProviderUnavailableError) as info:
        await provider.generate(MESSAGES, PARAMS)
    assert BASE in info.value.message and len(calls) == 1


async def test_unknown_model_is_reported_as_model_not_found():
    body = {"error": {"message": "The model `nope` does not exist", "code": "model_not_found"}}
    provider, calls, _ = build(scripted(json_response(body, 404)))
    with pytest.raises(ModelNotFoundError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 1


async def test_model_that_rejects_temperature_is_retried_without_it_and_remembered():
    rejection = {
        "error": {
            "message": "Unsupported value: 'temperature' does not support 0.3 with this model.",
            "type": "invalid_request_error",
            "param": "temperature",
        }
    }
    provider, calls, _ = build(
        scripted(json_response(rejection, 400), lambda: json_response(COMPLETION)), max_retries=0
    )
    result = await provider.generate(MESSAGES, PARAMS)
    assert result.text == "Hello!"
    assert "temperature" in calls.body(0) and "temperature" not in calls.body(1)

    await provider.generate(MESSAGES, PARAMS)   # remembered: no rejected attempt this time
    assert len(calls) == 3 and "temperature" not in calls.body(2)


async def test_other_bad_requests_are_not_swallowed():
    provider, calls, _ = build(scripted(json_response({"error": {"message": "max_tokens too large"}}, 400)))
    with pytest.raises(InvalidRequestError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 1


# --------------------------------------------------------------------------- models and connection test


async def test_list_models_is_sorted():
    provider, calls, _ = build(scripted(json_response({"data": [{"id": "b"}, {"id": "a"}, {"nope": 1}]})))
    models = await provider.list_models()
    assert [m.id for m in models] == ["a", "b"]
    assert calls.path() == "/v1/models" and calls.requests[0].method == "GET"


async def test_connection_test_checks_credentials_and_model_availability():
    models = json_response({"data": [{"id": "m1"}, {"id": "m2"}]})

    ok, _, _ = build(scripted(models))
    status = await ok.test_connection()
    assert status.ok and status.model_available is True and status.model_count == 2

    missing, _, _ = build(scripted(models))
    status = await missing.test_connection("not-installed")
    assert not status.ok and status.error_code == "model_not_found" and status.model_available is False

    lenient, _, _ = build(scripted(models), strict_model_check=False)
    status = await lenient.test_connection("anything")
    assert status.ok and status.model_available is None   # custom servers may not list every model


async def test_connection_test_reports_failures_instead_of_raising():
    bad_key, _, _ = build(scripted(json_response({"error": {"message": "Invalid API key"}}, 401)))
    status = await bad_key.test_connection()
    assert not status.ok and status.error_code == "provider_auth_failed"

    down, _, _ = build(scripted(httpx.ConnectError("refused")), max_retries=0)
    status = await down.test_connection()
    assert not status.ok and status.error_code == "provider_unavailable"

    unconfigured, calls, _ = build(scripted(json_response({"data": []})), key=None)
    status = await unconfigured.test_connection()
    assert not status.ok and status.error_code == "provider_not_configured" and len(calls) == 0


# --------------------------------------------------------------------------- streaming


async def test_stream_yields_tokens_then_a_final_event_with_usage():
    response = chunks(
        {"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
        delta("Hel"),
        delta("lo"),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    )
    provider, calls, _ = build(scripted(response))

    events = await collect(provider.stream(MESSAGES, PARAMS))

    assert [e.delta for e in events if e.delta] == ["Hel", "lo"]
    assert events[-1].done and events[-1].finish_reason == "stop"
    assert (events[-1].usage.prompt_tokens, events[-1].usage.completion_tokens) == (5, 2)
    assert calls.body()["stream"] is True and "stream_options" not in calls.body()


async def test_openai_requests_usage_in_streams():
    provider, calls, _ = build(scripted(chunks(delta("Hi"))), OpenAIProvider)
    await collect(provider.stream(MESSAGES, PARAMS))
    assert calls.body()["stream_options"] == {"include_usage": True}


async def test_groq_style_usage_is_read_from_x_groq():
    response = chunks(delta("Hi", x_groq={"usage": {"prompt_tokens": 9, "completion_tokens": 4}}))
    provider, _, _ = build(scripted(response), GroqProvider)
    events = await collect(provider.stream(MESSAGES, PARAMS))
    assert events[-1].usage.total_tokens == 13


async def test_stream_error_before_first_token_is_retried():
    failing = chunks({"error": {"message": "overloaded"}})
    provider, calls, sleeper = build(scripted(failing, chunks(delta("ok"))))
    events = await collect(provider.stream(MESSAGES, PARAMS))
    assert [e.delta for e in events if e.delta] == ["ok"]
    assert len(calls) == 2 and len(sleeper.delays) == 1


async def test_stream_failure_after_tokens_is_not_retried():
    broken = chunks(delta("partial"), {"error": {"message": "connection reset"}})
    provider, calls, _ = build(scripted(broken))
    received = []
    with pytest.raises(StreamInterruptedError):
        async for event in provider.stream(MESSAGES, PARAMS):
            received.append(event.delta)
    assert received == ["partial"] and len(calls) == 1


async def test_stream_http_error_at_open_is_mapped_and_retried():
    provider, calls, sleeper = build(
        scripted(json_response({"error": {"message": "slow"}}, 429, {"retry-after": "1"}), chunks(delta("ok")))
    )
    events = await collect(provider.stream(MESSAGES, PARAMS))
    assert events[0].delta == "ok" and sleeper.delays == [1.0] and len(calls) == 2


async def test_stream_http_auth_error_is_not_retried():
    provider, calls, _ = build(scripted(json_response({"error": {"message": "bad key"}}, 401)))
    with pytest.raises(AuthenticationError):
        await collect(provider.stream(MESSAGES, PARAMS))
    assert len(calls) == 1


# --------------------------------------------------------------------------- secrets


async def test_api_key_never_leaks_into_repr_errors_or_logs():
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("app.llm")
    handler = Capture(level=logging.DEBUG)
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        echo = {"error": {"message": f"Incorrect API key provided: {KEY}"}}
        provider, _, _ = build(scripted(json_response(echo, 401)))
        assert KEY not in repr(provider) and KEY not in str(provider.__dict__.get("name"))
        with pytest.raises(AuthenticationError) as info:
            await provider.generate(MESSAGES, PARAMS)
        assert KEY not in info.value.message
        assert KEY not in str(info.value.to_payload())

        ok, _, _ = build(scripted(json_response({"error": {"message": "x"}}, 503), json_response(COMPLETION)))
        await ok.generate(MESSAGES, PARAMS)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert records, "expected the call to be logged"
    assert all(KEY not in line for line in records)
