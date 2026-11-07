"""Tests for the provider registry and LLM service: switching, validation, endpoint override and fallback."""

from __future__ import annotations

import pytest

from app.core.config import CustomProviderConfig, Settings
from app.core.errors import ValidationFailedError
from app.llm.errors import (
    AuthenticationError,
    InvalidRequestError,
    ModelNotFoundError,
    ModelNotSelectedError,
    ProviderNotConfiguredError,
    ProviderNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
    StreamInterruptedError,
)
from app.llm.registry import OLLAMA_MIN_TIMEOUT_SECONDS, ProviderRegistry
from app.llm.service import (
    LLMConfigUpdate,
    LLMRuntimeConfig,
    LLMService,
    ProviderValidationError,
    StreamStart,
    build_llm_service,
)
from tests.helpers import MESSAGES, FakeProvider, collect

pytestmark = pytest.mark.anyio


def make_service(*providers, **config) -> LLMService:
    config.setdefault("provider", providers[0].name)
    return LLMService(ProviderRegistry(list(providers)), LLMRuntimeConfig(**config))


def unavailable(name="primary"):
    return ProviderUnavailableError("down", provider=name)


# --------------------------------------------------------------------------- registry


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_registry_is_built_from_settings_with_custom_providers(monkeypatch):
    monkeypatch.setenv("TOGETHER_API_KEY", "tg-secret-12345678")
    settings = _settings(
        groq_api_key="gsk_realvalue12345",
        openai_api_key="",   # a blank value in .env must count as "not configured"
        anthropic_api_key="",   # pinned so a real key in the OS environment cannot leak into the test
        llm_timeout_seconds=30,
        openai_compatible_providers=[
            CustomProviderConfig(name="together", base_url="https://api.together.xyz/v1", api_key_env="TOGETHER_API_KEY"),
            CustomProviderConfig(name="lmstudio", base_url="http://localhost:1234/v1"),
        ],
    )
    registry = ProviderRegistry.from_settings(settings)
    info = {p["name"]: p for p in registry.list_info()}

    assert registry.names() == ["groq", "openai", "anthropic", "ollama", "together", "lmstudio"]
    assert info["groq"]["configured"] is True
    assert info["openai"]["configured"] is False and info["anthropic"]["configured"] is False
    assert info["ollama"]["configured"] is True and info["ollama"]["requires_api_key"] is False
    assert info["together"]["configured"] is True and info["lmstudio"]["configured"] is True
    assert registry.get("ollama").timeout == OLLAMA_MIN_TIMEOUT_SECONDS   # cold model loads are slow
    assert registry.get("groq").timeout == 30


def test_provider_listing_never_exposes_credentials(monkeypatch):
    monkeypatch.setenv("TOGETHER_API_KEY", "tg-secret-12345678")
    settings = _settings(
        groq_api_key="gsk_realvalue12345",
        openai_compatible_providers=[
            CustomProviderConfig(name="together", base_url="https://api.together.xyz/v1", api_key_env="TOGETHER_API_KEY")
        ],
    )
    dump = str(ProviderRegistry.from_settings(settings).list_info())
    assert "gsk_realvalue12345" not in dump and "tg-secret-12345678" not in dump
    assert "api_key" not in dump.replace("requires_api_key", "")


def test_unknown_provider_error_lists_alternatives():
    registry = ProviderRegistry([FakeProvider("a"), FakeProvider("b")])
    with pytest.raises(ProviderNotFoundError) as info:
        registry.get("zzz")
    assert "a, b" in info.value.message
    assert registry.get(" A ").name == "a"   # lookups are case/whitespace tolerant


def test_registering_a_custom_provider_needs_no_other_changes():
    registry = ProviderRegistry([FakeProvider("a")])
    registry.register(FakeProvider("extra"))
    assert registry.names() == ["a", "extra"]
    with pytest.raises(ValueError):
        registry.register(FakeProvider("extra"))


def test_endpoint_override_rules():
    local = FakeProvider("local", allows_override=True)
    cloud = FakeProvider("cloud", allows_override=False)   # holds credentials -> must not be redirected
    registry = ProviderRegistry([local, cloud])

    previous = registry.set_base_url("local", "http://127.0.0.1:9999/")
    assert previous == "http://local.test" and local.base_url == "http://127.0.0.1:9999"

    registry.set_base_url("local", None)
    assert local.base_url == "http://local.test"

    with pytest.raises(ValidationFailedError) as blocked:
        registry.set_base_url("cloud", "https://evil.example.com")
    assert blocked.value.code == "base_url_override_not_allowed" and cloud.base_url == "http://cloud.test"

    with pytest.raises(ValidationFailedError) as bad_scheme:
        registry.set_base_url("local", "ftp://x")
    assert bad_scheme.value.code == "invalid_base_url"


def test_custom_provider_with_a_key_cannot_be_redirected(monkeypatch):
    monkeypatch.setenv("TOGETHER_API_KEY", "tg-secret-12345678")
    settings = _settings(
        openai_compatible_providers=[
            CustomProviderConfig(name="together", base_url="https://api.together.xyz/v1", api_key_env="TOGETHER_API_KEY"),
            CustomProviderConfig(name="lmstudio", base_url="http://localhost:1234/v1"),
        ]
    )
    registry = ProviderRegistry.from_settings(settings)
    assert registry.get("together").allows_base_url_override is False
    assert registry.get("lmstudio").allows_base_url_override is True
    assert registry.get("ollama").allows_base_url_override is True


def test_service_defaults_come_from_settings():
    service = build_llm_service(
        _settings(
            llm_default_provider="groq",
            llm_default_model="llama-x",
            llm_temperature=0.7,
            llm_max_tokens=256,
            llm_fallback_enabled=True,
            llm_fallback_provider="ollama",
        )
    )
    cfg = service.config
    assert (cfg.provider, cfg.model, cfg.temperature, cfg.max_tokens) == ("groq", "llama-x", 0.7, 256)
    assert cfg.fallback_enabled and cfg.fallback_provider == "ollama"


# --------------------------------------------------------------------------- generation and switching


async def test_generate_uses_the_active_provider_and_its_default_model():
    primary, other = FakeProvider("primary"), FakeProvider("other")
    service = make_service(primary, other)
    result = await service.generate(MESSAGES)
    assert result.text == "primary:fm" and not result.used_fallback
    assert other.calls == []


async def test_configured_model_and_sampling_settings_are_applied():
    primary = FakeProvider("primary")
    service = make_service(primary, model="chosen", temperature=0.9, max_tokens=77)
    result = await service.generate(MESSAGES)
    assert result.text == "primary:chosen"
    assert (primary.calls[0].temperature, primary.calls[0].max_tokens) == (0.9, 77)

    await service.generate(MESSAGES, temperature=0.0, max_tokens=5)
    assert (primary.calls[1].temperature, primary.calls[1].max_tokens) == (0.0, 5)


async def test_per_request_provider_override_ignores_the_configured_model():
    primary, other = FakeProvider("primary"), FakeProvider("other", default_model="om")
    service = make_service(primary, other, model="chosen")
    assert (await service.generate(MESSAGES, provider="other")).text == "other:om"
    assert (await service.generate(MESSAGES, provider="other", model="x")).text == "other:x"


async def test_missing_model_and_unknown_provider_are_reported():
    service = make_service(FakeProvider("primary", default_model=""))
    with pytest.raises(ModelNotSelectedError):
        await service.generate(MESSAGES)
    with pytest.raises(ProviderNotFoundError):
        await service.generate(MESSAGES, provider="nope")


async def test_switching_provider_resets_the_model_and_needs_no_reindexing():
    primary, other = FakeProvider("primary"), FakeProvider("other", default_model="om")
    service = make_service(primary, other, model="primary-model")

    config = await service.update_config(LLMConfigUpdate(provider="other"))

    assert (config.provider, config.model) == ("other", "")
    assert (await service.generate(MESSAGES)).text == "other:om"


async def test_switching_provider_with_an_explicit_model_keeps_it():
    service = make_service(FakeProvider("primary"), FakeProvider("other"))
    config = await service.update_config(LLMConfigUpdate(provider="other", model="big"))
    assert (config.provider, config.model) == ("other", "big")


async def test_null_fields_leave_the_configuration_unchanged():
    service = make_service(FakeProvider("primary"), temperature=0.4)
    config = await service.update_config(LLMConfigUpdate(provider=None, temperature=None, max_tokens=99))
    assert config.provider == "primary" and config.temperature == 0.4 and config.max_tokens == 99


async def test_activation_requires_a_working_connection_and_keeps_the_old_config_on_failure():
    primary = FakeProvider("primary")
    broken = FakeProvider("broken", connection_ok=False)
    service = make_service(primary, broken)

    with pytest.raises(ProviderValidationError) as info:
        await service.update_config(LLMConfigUpdate(provider="broken"))

    assert info.value.code == "provider_validation_failed"
    assert info.value.details["provider"] == "broken" and info.value.details["role"] == "primary"
    assert service.config.provider == "primary"


async def test_validation_can_be_skipped_explicitly():
    service = make_service(FakeProvider("primary"), FakeProvider("broken", connection_ok=False))
    config = await service.update_config(LLMConfigUpdate(provider="broken"), validate=False)
    assert config.provider == "broken"


async def test_activation_requires_a_model_when_the_provider_has_no_default():
    service = make_service(FakeProvider("primary"), FakeProvider("bare", default_model=""))
    with pytest.raises(ProviderValidationError) as info:
        await service.update_config(LLMConfigUpdate(provider="bare"))
    assert info.value.code == "model_not_selected"
    assert (await service.update_config(LLMConfigUpdate(provider="bare", model="m"))).model == "m"


async def test_unknown_provider_cannot_be_activated():
    service = make_service(FakeProvider("primary"))
    with pytest.raises(ProviderNotFoundError):
        await service.update_config(LLMConfigUpdate(provider="nope"))
    assert service.config.provider == "primary"


async def test_enabling_fallback_requires_a_distinct_valid_provider():
    service = make_service(FakeProvider("primary"), FakeProvider("backup"), FakeProvider("down", connection_ok=False))

    with pytest.raises(ValidationFailedError) as missing:
        await service.update_config(LLMConfigUpdate(fallback_enabled=True))
    assert missing.value.code == "fallback_provider_required"

    with pytest.raises(ValidationFailedError) as same:
        await service.update_config(LLMConfigUpdate(fallback_enabled=True, fallback_provider="primary"))
    assert same.value.code == "fallback_same_as_primary"

    with pytest.raises(ProviderValidationError) as unreachable:
        await service.update_config(LLMConfigUpdate(fallback_enabled=True, fallback_provider="down"))
    assert unreachable.value.details["role"] == "fallback"
    assert not service.config.fallback_enabled

    config = await service.update_config(LLMConfigUpdate(fallback_enabled=True, fallback_provider="backup"))
    assert config.fallback_enabled and config.fallback_provider == "backup"


async def test_endpoint_override_is_applied_and_can_be_reset():
    local = FakeProvider("local", allows_override=True)
    service = make_service(FakeProvider("primary"), local)

    config = await service.update_config(
        LLMConfigUpdate(provider="local", base_url_overrides={"local": "http://127.0.0.1:9999/"})
    )
    assert local.base_url == "http://127.0.0.1:9999"
    assert config.base_url_overrides == {"local": "http://127.0.0.1:9999"}

    config = await service.update_config(LLMConfigUpdate(base_url_overrides={"local": None}))
    assert local.base_url == "http://local.test" and config.base_url_overrides == {}


async def test_failed_activation_rolls_the_endpoint_override_back():
    local = FakeProvider("local", allows_override=True, connection_ok=False)
    service = make_service(FakeProvider("primary"), local)

    with pytest.raises(ProviderValidationError):
        await service.update_config(
            LLMConfigUpdate(provider="local", base_url_overrides={"local": "http://127.0.0.1:9999"})
        )

    assert local.base_url == "http://local.test"
    assert service.config.base_url_overrides == {} and service.config.provider == "primary"


async def test_endpoint_override_is_refused_for_providers_that_hold_credentials():
    cloud = FakeProvider("cloud", allows_override=False)
    service = make_service(FakeProvider("primary"), cloud)
    with pytest.raises(ValidationFailedError) as info:
        await service.update_config(LLMConfigUpdate(base_url_overrides={"cloud": "https://evil.example.com"}))
    assert info.value.code == "base_url_override_not_allowed"
    assert cloud.base_url == "http://cloud.test"


async def test_connection_test_goes_through_the_service():
    service = make_service(FakeProvider("primary"), FakeProvider("broken", connection_ok=False))
    assert (await service.test_provider("primary")).ok
    assert not (await service.test_provider("broken")).ok
    with pytest.raises(ProviderNotFoundError):
        await service.test_provider("nope")


# --------------------------------------------------------------------------- fallback (generate)


def fallback_service(primary_error, **config):
    primary = FakeProvider("primary", error=primary_error)
    backup = FakeProvider("backup", default_model="bm")
    service = make_service(
        primary, backup, FakeProvider("third"), fallback_enabled=True, fallback_provider="backup", **config
    )
    return service, primary, backup


async def test_fallback_is_used_and_clearly_reported():
    service, primary, backup = fallback_service(unavailable())

    result = await service.generate(MESSAGES)

    assert result.text == "backup:bm" and result.provider == "backup"
    assert result.used_fallback is True
    assert result.fallback_from == "primary" and result.fallback_reason == "provider_unavailable"
    assert len(primary.calls) == 1 and len(backup.calls) == 1


async def test_configured_fallback_model_is_used():
    service, _, backup = fallback_service(unavailable(), fallback_model="special")
    assert (await service.generate(MESSAGES)).text == "backup:special"


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailableError("x"),
        ProviderTimeoutError("x"),
        RateLimitError("x"),
        QuotaExceededError("x"),
        AuthenticationError("x"),
        ProviderNotConfiguredError("x"),
    ],
)
async def test_fallback_triggers_for_outages_limits_and_credential_problems(error):
    service, _, backup = fallback_service(error)
    assert (await service.generate(MESSAGES)).used_fallback is True
    assert len(backup.calls) == 1


@pytest.mark.parametrize(
    "error",
    [InvalidRequestError("x"), ModelNotFoundError("x"), ModelNotSelectedError("x")],
)
async def test_fallback_is_not_used_for_caller_errors(error):
    service, _, backup = fallback_service(error)
    with pytest.raises(type(error)):
        await service.generate(MESSAGES)
    assert backup.calls == []


async def test_no_fallback_when_disabled():
    primary = FakeProvider("primary", error=unavailable())
    backup = FakeProvider("backup")
    service = make_service(primary, backup, fallback_enabled=False, fallback_provider="backup")
    with pytest.raises(ProviderUnavailableError):
        await service.generate(MESSAGES)
    assert backup.calls == []


async def test_request_can_opt_out_of_or_into_fallback():
    service, _, backup = fallback_service(unavailable())
    with pytest.raises(ProviderUnavailableError):
        await service.generate(MESSAGES, allow_fallback=False)
    assert backup.calls == []

    primary = FakeProvider("primary")
    broken = FakeProvider("third", error=unavailable("third"))
    backup = FakeProvider("backup", default_model="bm")
    service = make_service(primary, broken, backup, fallback_enabled=False, fallback_provider="backup")
    assert (await service.generate(MESSAGES, provider="third", allow_fallback=True)).used_fallback is True


async def test_explicitly_chosen_provider_is_never_silently_replaced():
    service, _, backup = fallback_service(None)
    service.registry.get("third").error = unavailable("third")
    with pytest.raises(ProviderUnavailableError):
        await service.generate(MESSAGES, provider="third")
    assert backup.calls == []


async def test_when_the_fallback_also_fails_both_failures_are_visible():
    primary = FakeProvider("primary", error=unavailable())
    backup = FakeProvider("backup", error=RateLimitError("slow", provider="backup", retry_after=4.0))
    service = make_service(primary, backup, fallback_enabled=True, fallback_provider="backup")

    with pytest.raises(RateLimitError) as info:
        await service.generate(MESSAGES)

    assert info.value.details["primary_failure"] == {"provider": "primary", "code": "provider_unavailable"}


async def test_fallback_without_a_usable_model_surfaces_the_original_error():
    primary = FakeProvider("primary", error=unavailable())
    backup = FakeProvider("backup", default_model="")
    service = make_service(primary, backup, fallback_enabled=True, fallback_provider="backup")
    with pytest.raises(ProviderUnavailableError):
        await service.generate(MESSAGES)
    assert backup.calls == []


# --------------------------------------------------------------------------- streaming


async def test_stream_starts_with_the_answering_provider_then_tokens_then_done():
    service = make_service(FakeProvider("primary"))
    items = await collect(service.stream(MESSAGES))

    assert items[0] == StreamStart(provider="primary", model="fm")
    assert [i.delta for i in items[1:] if i.delta] == ["Hel", "lo"]
    assert items[-1].done


async def test_stream_falls_back_before_the_first_token_and_says_so():
    service, _, backup = fallback_service(unavailable())
    items = await collect(service.stream(MESSAGES))

    start = items[0]
    assert (start.provider, start.used_fallback, start.fallback_from, start.fallback_reason) == (
        "backup", True, "primary", "provider_unavailable",
    )
    assert [i.delta for i in items[1:] if i.delta] == ["Hel", "lo"]
    assert len(backup.calls) == 1


async def test_stream_never_falls_back_after_output_has_started():
    primary = FakeProvider("primary", error_after_tokens=StreamInterruptedError("dropped", provider="primary"))
    backup = FakeProvider("backup")
    service = make_service(primary, backup, fallback_enabled=True, fallback_provider="backup")

    received = []
    with pytest.raises(StreamInterruptedError):
        async for item in service.stream(MESSAGES):
            received.append(item)

    assert [i.delta for i in received if getattr(i, "delta", "")] == ["Hel", "lo"]
    assert backup.calls == []


async def test_stream_error_without_fallback_is_raised_before_any_item():
    service = make_service(FakeProvider("primary", error=unavailable()))
    with pytest.raises(ProviderUnavailableError):
        await collect(service.stream(MESSAGES))


async def test_stream_reports_both_failures_when_the_fallback_fails_too():
    primary = FakeProvider("primary", error=unavailable())
    backup = FakeProvider("backup", error=ProviderTimeoutError("t", provider="backup"))
    service = make_service(primary, backup, fallback_enabled=True, fallback_provider="backup")
    with pytest.raises(ProviderTimeoutError) as info:
        await collect(service.stream(MESSAGES))
    assert info.value.details["primary_failure"]["provider"] == "primary"
