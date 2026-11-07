"""HTTP-level tests for /api/v1/llm/*. Providers are fakes; no model, network or API key is involved."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_llm_service
from app.api.routes.llm import router
from app.core.errors import register_error_handlers
from app.llm.errors import (
    AuthenticationError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
    StreamInterruptedError,
)
from app.llm.registry import ProviderRegistry
from app.llm.service import LLMRuntimeConfig, LLMService
from tests.helpers import FakeProvider


def make_api(primary: FakeProvider | None = None, backup: FakeProvider | None = None, **config) -> SimpleNamespace:
    primary = primary or FakeProvider("primary")
    backup = backup or FakeProvider("backup", default_model="bm")
    config.setdefault("provider", "primary")
    service = LLMService(ProviderRegistry([primary, backup]), LLMRuntimeConfig(**config))

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_llm_service] = lambda: service
    return SimpleNamespace(client=TestClient(app), service=service, primary=primary, backup=backup)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


# --------------------------------------------------------------------------- providers


def test_providers_endpoint_lists_status_and_active_config_without_secrets():
    api = make_api()
    body = api.client.get("/api/v1/llm/providers").json()

    assert [p["name"] for p in body["providers"]] == ["primary", "backup"]
    assert body["active"]["provider"] == "primary"
    assert {"configured", "base_url", "default_model", "requires_api_key"} <= set(body["providers"][0])
    assert "key" not in " ".join(body["providers"][0]).replace("requires_api_key", "")


def test_models_endpoint_and_unknown_provider_envelope():
    api = make_api()
    ok = api.client.get("/api/v1/llm/providers/primary/models")
    assert ok.status_code == 200 and ok.json()["models"][0]["id"] == "fm"

    missing = api.client.get("/api/v1/llm/providers/nope/models")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "provider_not_found"


def test_connection_test_returns_200_with_ok_false_when_the_provider_is_down():
    api = make_api(primary=FakeProvider("primary", connection_ok=False))
    result = api.client.post("/api/v1/llm/providers/primary/test")
    assert result.status_code == 200
    assert result.json()["ok"] is False and result.json()["error_code"] == "provider_unavailable"

    healthy = api.client.post("/api/v1/llm/providers/backup/test", json={"model": "bm"})
    assert healthy.status_code == 200 and healthy.json()["ok"] is True


# --------------------------------------------------------------------------- configuration


def test_switching_the_active_provider_through_the_api():
    api = make_api(model="old-model")
    response = api.client.put("/api/v1/llm/config", json={"provider": "backup"})

    assert response.status_code == 200
    assert response.json()["provider"] == "backup" and response.json()["model"] == ""
    assert api.client.get("/api/v1/llm/config").json()["provider"] == "backup"
    assert api.client.post("/api/v1/llm/complete", json={"prompt": "hi"}).json()["provider"] == "backup"


def test_activation_failure_is_a_structured_422_and_keeps_the_old_provider():
    api = make_api(backup=FakeProvider("backup", connection_ok=False))
    response = api.client.put("/api/v1/llm/config", json={"provider": "backup"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "provider_validation_failed"
    assert api.client.get("/api/v1/llm/config").json()["provider"] == "primary"

    forced = api.client.put("/api/v1/llm/config?validate=false", json={"provider": "backup"})
    assert forced.status_code == 200 and forced.json()["provider"] == "backup"


def test_invalid_config_values_are_rejected():
    api = make_api()
    assert api.client.put("/api/v1/llm/config", json={"temperature": 5}).status_code == 422
    assert api.client.put("/api/v1/llm/config", json={"max_tokens": 0}).status_code == 422
    assert api.client.put("/api/v1/llm/config", json={"provider": "nope"}).status_code == 404


# --------------------------------------------------------------------------- complete (non-streaming)


def test_complete_with_a_prompt_returns_text_and_provenance():
    api = make_api()
    response = api.client.post("/api/v1/llm/complete", json={"prompt": "Hello", "system": "Be brief"})

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "primary:fm" and body["provider"] == "primary" and body["model"] == "fm"
    assert body["used_fallback"] is False and body["fallback_from"] is None
    assert [m.role for m in api.primary.last_messages] == ["system", "user"]


def test_per_request_provider_and_model_override():
    api = make_api()
    body = api.client.post(
        "/api/v1/llm/complete", json={"prompt": "hi", "provider": "backup", "model": "zz", "temperature": 0.0}
    ).json()
    assert body["text"] == "backup:zz"
    assert api.backup.calls[0].temperature == 0.0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"prompt": "a", "messages": [{"role": "user", "content": "b"}]},
        {"messages": [{"role": "user", "content": "b"}], "system": "s"},
        {"prompt": "x" * 8001},
        {"prompt": ""},
        {"messages": []},
        {"messages": [{"role": "robot", "content": "b"}]},
        {"prompt": "hi", "temperature": 9},
        {"prompt": "hi", "max_tokens": 0},
    ],
)
def test_invalid_complete_requests_are_rejected_with_422(payload):
    api = make_api()
    assert api.client.post("/api/v1/llm/complete", json=payload).status_code == 422
    assert api.primary.calls == []


@pytest.mark.parametrize(
    "error, status, code",
    [
        (RateLimitError("slow", provider="primary", retry_after=7.0), 429, "provider_rate_limited"),
        (QuotaExceededError("out of credit", provider="primary"), 429, "provider_quota_exceeded"),
        (ProviderUnavailableError("down", provider="primary"), 503, "provider_unavailable"),
        (AuthenticationError("bad key", provider="primary"), 502, "provider_auth_failed"),
    ],
)
def test_provider_failures_map_to_clear_http_errors(error, status, code):
    api = make_api(primary=FakeProvider("primary", error=error))
    response = api.client.post("/api/v1/llm/complete", json={"prompt": "hi"})

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["details"]["provider"] == "primary"
    if isinstance(error, RateLimitError):
        assert response.headers["retry-after"] == "7"


def test_fallback_is_visible_in_the_response():
    api = make_api(
        primary=FakeProvider("primary", error=ProviderUnavailableError("down", provider="primary")),
        fallback_enabled=True,
        fallback_provider="backup",
    )
    body = api.client.post("/api/v1/llm/complete", json={"prompt": "hi"}).json()

    assert body["provider"] == "backup" and body["text"] == "backup:bm"
    assert body["used_fallback"] is True
    assert body["fallback_from"] == "primary" and body["fallback_reason"] == "provider_unavailable"


# --------------------------------------------------------------------------- complete (streaming)


def test_streaming_emits_start_tokens_and_done():
    api = make_api()
    response = api.client.post("/api/v1/llm/complete", json={"prompt": "hi", "stream": True})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)

    assert [name for name, _ in events] == ["start", "token", "token", "done"]
    assert events[0][1]["provider"] == "primary" and events[0][1]["used_fallback"] is False
    assert "".join(data["delta"] for name, data in events if name == "token") == "Hello"
    assert events[-1][1]["finish_reason"] == "stop"


def test_streaming_announces_the_fallback_provider():
    api = make_api(
        primary=FakeProvider("primary", error=ProviderUnavailableError("down", provider="primary")),
        fallback_enabled=True,
        fallback_provider="backup",
    )
    events = parse_sse(api.client.post("/api/v1/llm/complete", json={"prompt": "hi", "stream": True}).text)

    start = events[0][1]
    assert events[0][0] == "start"
    assert (start["provider"], start["used_fallback"], start["fallback_from"]) == ("backup", True, "primary")
    assert events[-1][0] == "done"


def test_failure_before_the_first_token_is_a_real_http_error_not_a_200_stream():
    api = make_api(primary=FakeProvider("primary", error=ProviderUnavailableError("down", provider="primary")))
    response = api.client.post("/api/v1/llm/complete", json={"prompt": "hi", "stream": True})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"


def test_failure_mid_stream_ends_with_an_error_event():
    api = make_api(
        primary=FakeProvider("primary", error_after_tokens=StreamInterruptedError("dropped", provider="primary")),
        fallback_enabled=True,
        fallback_provider="backup",
    )
    response = api.client.post("/api/v1/llm/complete", json={"prompt": "hi", "stream": True})
    events = parse_sse(response.text)

    assert response.status_code == 200
    assert [name for name, _ in events] == ["start", "token", "token", "error"]
    assert events[-1][1]["code"] == "stream_interrupted"
    assert api.backup.calls == []   # no splicing a second answer onto partial text
