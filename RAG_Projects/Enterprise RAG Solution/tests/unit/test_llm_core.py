"""Unit tests for HTTP error mapping, retry helpers and stream parsers."""

from __future__ import annotations

import httpx
import pytest

from app.llm.errors import (
    AuthenticationError,
    InvalidRequestError,
    InvalidResponseError,
    LLMError,
    ModelNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
    StreamInterruptedError,
)
from app.llm.http_errors import extract_error, map_http_error, map_transport_error, parse_retry_after
from app.llm.retry import RetryPolicy, stream_with_retries, with_retries
from app.llm.sse import SSEEvent, iter_ndjson, iter_sse, parse_json_object
from tests.helpers import SleepRecorder, agen, collect

pytestmark = pytest.mark.anyio


def _map(status, body="", headers=None):
    return map_http_error(provider="p", model="m", status=status, headers=headers or {}, body=body)


# --------------------------------------------------------------------------- error mapping


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, AuthenticationError),
        (403, AuthenticationError),
        (402, QuotaExceededError),
        (429, RateLimitError),
        (408, ProviderTimeoutError),
        (504, ProviderTimeoutError),
        (500, ProviderUnavailableError),
        (502, ProviderUnavailableError),
        (503, ProviderUnavailableError),
        (529, ProviderUnavailableError),
        (400, InvalidRequestError),
        (413, InvalidRequestError),
        (422, InvalidRequestError),
    ],
)
def test_status_codes_map_to_error_types(status, expected):
    assert type(_map(status, '{"error":{"message":"boom"}}')) is expected


def test_retryable_flags():
    assert _map(429).retryable and _map(500).retryable and _map(504).retryable
    assert not _map(401).retryable and not _map(400).retryable and not _map(402).retryable


def test_rate_limit_carries_retry_after_header():
    err = _map(429, '{"error":{"message":"slow down"}}', {"retry-after": "7"})
    assert isinstance(err, RateLimitError)
    assert err.retry_after == 7.0
    assert err.headers["Retry-After"] == "7"


@pytest.mark.parametrize(
    "status, body",
    [
        (429, '{"error":{"message":"You exceeded your current quota","code":"insufficient_quota"}}'),
        (400, '{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low"}}'),
        (400, '{"type":"error","error":{"type":"invalid_request_error","message":"You have reached your spend limit"}}'),
        (402, '{"type":"error","error":{"type":"billing_error","message":"payment"}}'),
    ],
)
def test_quota_and_billing_failures_are_not_treated_as_rate_limits(status, body):
    err = _map(status, body)
    assert isinstance(err, QuotaExceededError)
    assert not err.retryable


def test_404_distinguishes_missing_model_from_wrong_endpoint():
    assert isinstance(_map(404, '{"error":{"message":"The model `x` does not exist"}}'), ModelNotFoundError)
    assert isinstance(_map(404, '{"error":"model \'x\' not found"}'), ModelNotFoundError)
    wrong_path = _map(404, '{"error":{"message":"Not Found"}}')
    assert isinstance(wrong_path, InvalidRequestError)
    assert "base URL" in wrong_path.message


def test_error_message_is_redacted_and_truncated():
    err = _map(401, '{"error":{"message":"Incorrect API key provided: sk-abcdefghijklmnop1234"}}')
    assert "abcdefghijklmnop1234" not in err.message
    long = _map(500, '{"error":{"message":"%s"}}' % ("x" * 2000))
    assert len(long.message) <= 300


@pytest.mark.parametrize(
    "body, expected",
    [
        ('{"error":{"message":"m","code":"c"}}', ("m", "c")),
        ('{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}', ("Overloaded", "overloaded_error")),
        ('{"error":"model not found"}', ("model not found", "")),
        ("plain text failure", ("plain text failure", "")),
        ("", ("", "")),
        (None, ("", "")),
    ],
)
def test_extract_error_understands_all_body_shapes(body, expected):
    assert extract_error(body) == expected


def test_parse_retry_after():
    assert parse_retry_after({"retry-after": "2"}) == 2.0
    assert parse_retry_after({"retry-after-ms": "1500"}) == 1.5
    assert parse_retry_after({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert parse_retry_after({}) is None


def test_transport_errors():
    kwargs = {"provider": "p", "model": "m", "base_url": "http://x"}
    assert isinstance(map_transport_error(exc=httpx.ReadTimeout("t"), **kwargs), ProviderTimeoutError)
    assert isinstance(map_transport_error(exc=httpx.ConnectError("refused"), **kwargs), ProviderUnavailableError)
    assert isinstance(map_transport_error(exc=httpx.RemoteProtocolError("x"), **kwargs), ProviderUnavailableError)
    assert isinstance(map_transport_error(exc=httpx.DecodingError("x"), **kwargs), ProviderUnavailableError)


# --------------------------------------------------------------------------- retry


def _flaky(failures, result="ok"):
    """Operation that raises the given errors in order, then returns ``result``."""
    state = {"calls": 0}

    async def operation():
        state["calls"] += 1
        if state["calls"] <= len(failures):
            raise failures[state["calls"] - 1]
        return result

    operation.state = state
    return operation


async def test_with_retries_recovers_and_uses_exponential_backoff():
    sleeper = SleepRecorder()
    op = _flaky([ProviderUnavailableError("a"), ProviderUnavailableError("b")])
    result, attempts = await with_retries(op, RetryPolicy(max_retries=2, jitter=0.0), sleep=sleeper)
    assert (result, attempts) == ("ok", 3)
    assert sleeper.delays == [0.5, 1.0]


async def test_with_retries_does_not_retry_permanent_errors():
    sleeper = SleepRecorder()
    op = _flaky([AuthenticationError("bad key")])
    with pytest.raises(AuthenticationError):
        await with_retries(op, RetryPolicy(max_retries=3), sleep=sleeper)
    assert op.state["calls"] == 1 and sleeper.delays == []


async def test_with_retries_gives_up_after_max_retries():
    op = _flaky([ProviderUnavailableError("x")] * 5)
    with pytest.raises(ProviderUnavailableError) as info:
        await with_retries(op, RetryPolicy(max_retries=1, jitter=0.0), sleep=SleepRecorder())
    assert op.state["calls"] == 2
    assert info.value.attempts == 2


async def test_retry_after_is_honoured():
    sleeper = SleepRecorder()
    op = _flaky([RateLimitError("slow", retry_after=3.0)])
    await with_retries(op, RetryPolicy(jitter=0.0), sleep=sleeper)
    assert sleeper.delays == [3.0]


async def test_retry_after_longer_than_limit_fails_fast_so_fallback_can_take_over():
    sleeper = SleepRecorder()
    op = _flaky([RateLimitError("slow", retry_after=120.0)])
    with pytest.raises(RateLimitError):
        await with_retries(op, RetryPolicy(max_retry_after=20.0), sleep=sleeper)
    assert op.state["calls"] == 1 and sleeper.delays == []


async def test_on_retry_hook_is_called():
    seen = []
    op = _flaky([ProviderTimeoutError("t")])
    await with_retries(
        op, RetryPolicy(jitter=0.0), sleep=SleepRecorder(), on_retry=lambda n, exc, delay: seen.append((n, exc.code, delay))
    )
    assert seen == [(1, "provider_timeout", 0.5)]


def _stream_factory(script):
    """``script`` is a list of per-attempt behaviours: list of items, optionally ending in an Exception."""
    state = {"attempt": 0}

    async def factory():
        steps = script[min(state["attempt"], len(script) - 1)]
        state["attempt"] += 1
        for step in steps:
            if isinstance(step, Exception):
                raise step
            yield step

    factory.state = state
    return factory


async def test_stream_retries_when_failure_happens_before_first_item():
    factory = _stream_factory([[ProviderUnavailableError("down")], ["a", "b"]])
    items = await collect(stream_with_retries(factory, RetryPolicy(jitter=0.0), sleep=SleepRecorder()))
    assert items == ["a", "b"] and factory.state["attempt"] == 2


async def test_stream_failure_after_output_is_not_retried_and_is_reported_as_interrupted():
    factory = _stream_factory([["a", ProviderUnavailableError("dropped")]])
    received = []
    with pytest.raises(StreamInterruptedError):
        async for item in stream_with_retries(factory, RetryPolicy(), sleep=SleepRecorder()):
            received.append(item)
    assert received == ["a"] and factory.state["attempt"] == 1


async def test_stream_permanent_error_is_raised_as_is():
    factory = _stream_factory([[AuthenticationError("bad key")]])
    with pytest.raises(AuthenticationError):
        await collect(stream_with_retries(factory, RetryPolicy(), sleep=SleepRecorder()))


# --------------------------------------------------------------------------- parsers


async def test_iter_sse_parses_events():
    lines = ["event: a", "data: 1", "", "data: 2", "data: 3", "", ": a comment", "id: 9", "data:tail"]
    events = await collect(iter_sse(agen(lines)))
    assert events == [SSEEvent("a", "1"), SSEEvent(None, "2\n3"), SSEEvent(None, "tail")]


async def test_iter_sse_handles_carriage_returns_and_empty_stream():
    assert await collect(iter_sse(agen(["data: x\r", "\r"]))) == [SSEEvent(None, "x")]
    assert await collect(iter_sse(agen([]))) == []


async def test_iter_ndjson_skips_blank_lines_and_rejects_garbage():
    assert await collect(iter_ndjson(agen(['{"a":1}', "", '{"b":2}']), provider="p")) == [{"a": 1}, {"b": 2}]
    with pytest.raises(InvalidResponseError):
        await collect(iter_ndjson(agen(["{not json"]), provider="p"))


def test_parse_json_object_requires_an_object():
    assert parse_json_object('{"a":1}', provider="p") == {"a": 1}
    for bad in ("[1,2]", "not json", '"str"'):
        with pytest.raises(InvalidResponseError):
            parse_json_object(bad, provider="p")


def test_llm_errors_are_app_errors_with_safe_payloads():
    err = RateLimitError("slow down", provider="groq", model="m", retry_after=2.2)
    payload = err.to_payload("req-1")["error"]
    assert payload["code"] == "provider_rate_limited"
    assert payload["details"]["provider"] == "groq"
    assert payload["details"]["retry_after_seconds"] == 2.2
    assert err.headers == {"Retry-After": "3"}
    assert isinstance(err, LLMError) and err.status_code == 429
