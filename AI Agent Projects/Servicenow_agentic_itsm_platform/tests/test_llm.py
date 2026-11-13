from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel, SecretStr

from app.config import settings
from app.db.base import session_scope
from app.db.repositories import LLMUsageRepository
from app.llm import manager as mgr
from app.llm.manager import (
    LLMConfigError,
    LLMInvalidResponseError,
    LLMManager,
    LLMRateLimitError,
    LLMUnavailableError,
    MockProvider,
    OpenAICompatibleProvider,
    ProviderConfig,
    extract_json,
    provider_status,
)


class Answer(BaseModel):
    cause: str
    confidence: float


@pytest.fixture(autouse=True)
def reset_script():
    MockProvider.script = []
    yield
    MockProvider.script = []


def make(provider="mock", **kw):
    kw.setdefault("sleep", lambda s: None)
    return LLMManager(provider, **kw)


def test_mock_returns_empty_and_records_usage_to_db():
    assert make("mock").complete("s", "u", agent="a") == ""
    with session_scope() as s:
        assert LLMUsageRepository(s).summary()["calls"] == 1


def test_complete_json_validates_schema():
    MockProvider.script = ['```json\n{"cause": "cpu", "confidence": 0.7}\n```']
    out = make("mock").complete_json("s", "u", Answer)
    assert out.cause == "cpu" and out.confidence == 0.7


def test_invalid_json_triggers_repair_then_succeeds():
    MockProvider.script = ["not json at all", '{"cause": "x", "confidence": 0.5}']
    assert make("mock").complete_json("s", "u", Answer).cause == "x"


def test_invalid_json_exhausted_raises():
    MockProvider.script = ["nope", '{"cause": 1}']  # second is schema-invalid
    with pytest.raises(LLMInvalidResponseError):
        make("mock").complete_json("s", "u", Answer)


def test_extract_json_handles_prose_wrapper():
    assert extract_json('Here you go: {"a": 1} thanks') == {"a": 1}
    with pytest.raises(LLMInvalidResponseError):
        extract_json("")


def test_retry_then_success(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    MockProvider.script = [LLMUnavailableError("down"), LLMRateLimitError("slow", retry_after=0.0), "ok"]
    manager = make("mock")
    resp = manager.generate("s", "u")
    assert resp.text == "ok" and resp.attempts == 3


def test_retries_exhausted_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    MockProvider.script = [LLMUnavailableError("a"), LLMUnavailableError("b")]
    with pytest.raises(LLMUnavailableError):
        make("mock").generate("s", "u")


def test_non_retriable_error_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_retries", 3)
    MockProvider.script = [LLMConfigError("bad key"), "should not be used"]
    with pytest.raises(LLMConfigError):
        make("mock").generate("s", "u")
    assert MockProvider.script == ["should not be used"]


def test_fallback_provider_used_when_primary_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(""))
    MockProvider.script = ["from fallback"]
    resp = make("groq", fallbacks=["mock"]).generate("s", "u")
    assert resp.text == "from fallback" and resp.fallback_used and resp.provider == "mock"


def test_no_fallback_raises_for_unconfigured_provider(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(""))
    with pytest.raises(LLMConfigError):
        make("groq", fallbacks=[]).generate("s", "u")


def test_unknown_provider_rejected():
    with pytest.raises(LLMConfigError):
        LLMManager("skynet")


def test_cost_estimation(monkeypatch):
    monkeypatch.setattr(settings, "llm_cost_per_1k_input", 1.0)
    monkeypatch.setattr(settings, "llm_cost_per_1k_output", 2.0)
    MockProvider.script = ["one two three"]
    manager = make("mock")
    resp = manager.generate("a b", "c d")
    assert resp.estimated_cost == pytest.approx(4 / 1000 * 1.0 + 3 / 1000 * 2.0)
    assert manager.usage_summary()["calls"] == 1


def test_provider_status_never_exposes_secrets(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_supersecretvalue123"))
    text = str(provider_status())
    assert "supersecret" not in text
    assert any(p["provider"] == "groq" and p["configured"] for p in provider_status())


# ---- OpenAI-compatible adapter with a stubbed SDK client -----------------
def _provider(client):
    p = OpenAICompatibleProvider(ProviderConfig("ollama", "http://x/v1", "k", "m"))
    p._client = client
    return p


def _client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _resp(text="hi"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3), model="m")


def _http(status):
    return httpx.Response(status, request=httpx.Request("POST", "http://x"), headers={"retry-after": "2"})


def test_adapter_success_reports_usage():
    out = _provider(_client(lambda **kw: _resp("hello"))).chat("s", "u", timeout=5, json_mode=False, temperature=0)
    assert (out.text, out.prompt_tokens, out.completion_tokens) == ("hello", 7, 3)


def test_adapter_maps_rate_limit_with_retry_after():
    def boom(**kw):
        raise openai.RateLimitError("limit", response=_http(429), body=None)

    with pytest.raises(LLMRateLimitError) as exc:
        _provider(_client(boom)).chat("s", "u", timeout=5, json_mode=False, temperature=0)
    assert exc.value.retry_after == 2.0


def test_adapter_maps_connection_and_auth_errors():
    def conn(**kw):
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://x"))

    def auth(**kw):
        raise openai.AuthenticationError("no", response=_http(401), body=None)

    with pytest.raises(LLMUnavailableError):
        _provider(_client(conn)).chat("s", "u", timeout=5, json_mode=False, temperature=0)
    with pytest.raises(LLMConfigError):
        _provider(_client(auth)).chat("s", "u", timeout=5, json_mode=False, temperature=0)


def test_adapter_json_mode_falls_back_when_unsupported():
    calls = []

    def create(**kw):
        calls.append("response_format" in kw)
        if "response_format" in kw:
            raise openai.BadRequestError("nope", response=_http(400), body=None)
        return _resp("{}")

    _provider(_client(create)).chat("s", "u", timeout=5, json_mode=True, temperature=0)
    assert calls == [True, False]


def test_error_messages_are_masked():
    def boom(**kw):
        raise openai.APIError("failed with api_key=sk-abcdefghijklmnop1234", request=httpx.Request("POST", "http://x"), body=None)

    with pytest.raises(mgr.LLMError) as exc:
        _provider(_client(boom)).chat("s", "u", timeout=5, json_mode=False, temperature=0)
    assert "abcdefghijklmnop" not in str(exc.value)
