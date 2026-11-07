"""Tests for the Anthropic Claude provider (Messages API). All HTTP is mocked."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.llm.anthropic import AnthropicProvider
from app.llm.base import ChatMessage, GenerationParams
from app.llm.errors import (
    AuthenticationError,
    InvalidRequestError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
    StreamInterruptedError,
)
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

KEY = "sk-ant-test-key-12345678"

MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-test",
    "content": [
        {"type": "text", "text": "Hello "},
        {"type": "text", "text": "there"},
        {"type": "tool_use", "id": "t1", "name": "n", "input": {}},
    ],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 12, "output_tokens": 5},
}


def build(handler, *, key=KEY, max_retries=2):
    client, calls = mock_client(handler)
    sleeper = SleepRecorder()
    provider = AnthropicProvider(
        api_key=SecretStr(key) if key is not None else None,
        default_model="claude-test",
        client=client,
        sleep=sleeper,
        retry_policy=RetryPolicy(max_retries=max_retries, jitter=0.0),
    )
    return provider, calls, sleeper


def event(kind, **payload):
    return (kind, {"type": kind, **payload})


# --------------------------------------------------------------------------- non-streaming


async def test_generate_parses_text_blocks_usage_and_stop_reason():
    provider, calls, _ = build(scripted(json_response(MESSAGE)))

    result = await provider.generate(MESSAGES, PARAMS)

    assert result.text == "Hello there"   # non-text blocks are ignored
    assert result.provider == "anthropic" and result.model == "claude-test"
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (12, 5)
    assert result.finish_reason == "stop"

    assert calls.path() == "/v1/messages"
    assert calls.header("x-api-key") == KEY
    assert calls.header("anthropic-version") == "2023-06-01"
    assert calls.header("authorization") is None


async def test_system_messages_move_to_the_top_level_and_temperature_is_clamped():
    provider, calls, _ = build(scripted(json_response(MESSAGE)))
    messages = [
        ChatMessage("system", "Be brief."),
        ChatMessage("user", "Hi"),
        ChatMessage("assistant", "Hello"),
        ChatMessage("user", "Again"),
    ]

    await provider.generate(messages, GenerationParams(model="claude-test", temperature=1.7, max_tokens=64))

    body = calls.body()
    assert body["system"] == "Be brief."
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["temperature"] == 1.0   # Anthropic accepts 0..1 only
    assert body["max_tokens"] == 64 and body["stream"] is False


async def test_request_without_a_user_message_is_rejected_locally():
    provider, calls, _ = build(scripted(json_response(MESSAGE)))
    with pytest.raises(InvalidRequestError):
        await provider.generate([ChatMessage("system", "only system")], PARAMS)
    assert len(calls) == 0


async def test_not_configured_without_a_key():
    provider, calls, _ = build(scripted(json_response(MESSAGE)), key=None)
    with pytest.raises(ProviderNotConfiguredError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 0


# --------------------------------------------------------------------------- errors


async def test_invalid_key_is_not_retried():
    body = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
    provider, calls, _ = build(scripted(json_response(body, 401)))
    with pytest.raises(AuthenticationError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 1


async def test_low_credit_balance_is_quota_not_a_bad_request():
    body = {"type": "error", "error": {"type": "invalid_request_error",
                                       "message": "Your credit balance is too low to access the Anthropic API."}}
    provider, calls, _ = build(scripted(json_response(body, 400)))
    with pytest.raises(QuotaExceededError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 1


async def test_rate_limit_waits_for_retry_after_then_succeeds():
    limited = json_response(
        {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}, 429, {"retry-after": "3"}
    )
    provider, calls, sleeper = build(scripted(limited, json_response(MESSAGE)))
    result = await provider.generate(MESSAGES, PARAMS)
    assert result.text == "Hello there" and sleeper.delays == [3.0] and len(calls) == 2


async def test_overloaded_529_is_retried_then_reported_unavailable():
    overloaded = json_response({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}, 529)
    provider, calls, _ = build(scripted(overloaded))
    with pytest.raises(ProviderUnavailableError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 3


async def test_spend_cap_429_without_retry_after_still_fails_after_bounded_retries():
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "limit"}}
    provider, calls, sleeper = build(scripted(json_response(body, 429)))
    with pytest.raises(RateLimitError):
        await provider.generate(MESSAGES, PARAMS)
    assert len(calls) == 3 and sleeper.delays == [0.5, 1.0]


# --------------------------------------------------------------------------- models


async def test_list_models_follows_pagination_and_keeps_api_order():
    page1 = json_response({"data": [{"id": "claude-new", "display_name": "New"}], "has_more": True, "last_id": "claude-new"})
    page2 = json_response({"data": [{"id": "claude-old"}], "has_more": False, "last_id": "claude-old"})
    provider, calls, _ = build(scripted(page1, page2))

    models = await provider.list_models()

    assert [m.id for m in models] == ["claude-new", "claude-old"]
    assert models[0].display_name == "New"
    assert "after_id" not in calls.url(0) and "after_id=claude-new" in calls.url(1)


async def test_connection_test_uses_the_model_list():
    provider, _, _ = build(scripted(json_response({"data": [{"id": "claude-test"}], "has_more": False})))
    status = await provider.test_connection()
    assert status.ok and status.model_available is True

    provider, _, _ = build(scripted(json_response({"data": [{"id": "other"}], "has_more": False})))
    status = await provider.test_connection("claude-retired")
    assert not status.ok and status.error_code == "model_not_found"


# --------------------------------------------------------------------------- streaming


def stream_events(*middle):
    return [
        event("message_start", message={"id": "m", "usage": {"input_tokens": 25, "output_tokens": 1}}),
        event("content_block_start", index=0, content_block={"type": "text", "text": ""}),
        ("ping", {"type": "ping"}),
        *middle,
        event("content_block_stop", index=0),
        event("message_delta", delta={"stop_reason": "end_turn"}, usage={"output_tokens": 15}),
        event("message_stop"),
    ]


def text_delta(text):
    return event("content_block_delta", index=0, delta={"type": "text_delta", "text": text})


async def test_stream_assembles_text_usage_and_ignores_unknown_events():
    response = sse_response(
        stream_events(
            text_delta("Hel"),
            event("content_block_delta", index=0, delta={"type": "thinking_delta", "thinking": "hmm"}),
            ("some_future_event", {"type": "some_future_event", "x": 1}),
            text_delta("lo"),
        )
    )
    provider, calls, _ = build(scripted(response))

    events = await collect(provider.stream(MESSAGES, PARAMS))

    assert [e.delta for e in events if e.delta] == ["Hel", "lo"]
    final = events[-1]
    assert final.done and final.finish_reason == "stop"
    assert (final.usage.prompt_tokens, final.usage.completion_tokens) == (25, 15)
    assert calls.body()["stream"] is True


async def test_stream_error_event_before_output_is_retried():
    overloaded = sse_response([("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})])
    provider, calls, _ = build(scripted(overloaded, sse_response(stream_events(text_delta("ok")))))
    events = await collect(provider.stream(MESSAGES, PARAMS))
    assert [e.delta for e in events if e.delta] == ["ok"] and len(calls) == 2


async def test_stream_error_event_after_output_interrupts_the_stream():
    broken = sse_response(
        [
            event("message_start", message={"usage": {"input_tokens": 1}}),
            text_delta("partial"),
            ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
        ]
    )
    provider, calls, _ = build(scripted(broken))
    seen = []
    with pytest.raises(StreamInterruptedError):
        async for e in provider.stream(MESSAGES, PARAMS):
            seen.append(e.delta)
    assert seen == ["partial"] and len(calls) == 1


async def test_stream_http_error_at_open_is_mapped():
    body = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
    provider, calls, _ = build(scripted(json_response(body, 401)))
    with pytest.raises(AuthenticationError):
        await collect(provider.stream(MESSAGES, PARAMS))
    assert len(calls) == 1
