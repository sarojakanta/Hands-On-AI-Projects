"""Tests for persisted LLM configuration, config reset and the vector-store factory."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import ValidationFailedError
from app.db.session import Database
from app.db.settings_repo import SettingsRepository
from app.llm.persistence import KeyValuePersistence
from app.llm.registry import ProviderRegistry
from app.llm.service import LLMConfigUpdate, LLMRuntimeConfig, LLMService, build_llm_service
from app.vectorstore.chroma_store import ChromaVectorStore
from app.vectorstore.factory import create_vector_store
from tests.helpers import FakeProvider

pytestmark = pytest.mark.anyio


class MemoryPersistence:
    def __init__(self, initial=None):
        self.value = initial
        self.fail_saves = False
        self.cleared = 0

    def load(self):
        return self.value

    def save(self, config):
        if self.fail_saves:
            raise RuntimeError("disk full")
        self.value = config

    def clear(self):
        self.value = None
        self.cleared += 1


def make_service(persistence=None):
    registry = ProviderRegistry([FakeProvider("primary"), FakeProvider("other", allows_override=True)])
    defaults = LLMRuntimeConfig(provider="primary", temperature=0.2, max_tokens=1024)
    return LLMService(registry, defaults, persistence, defaults)


# --------------------------------------------------------------------------- persistence


async def test_updates_are_saved_and_reset_restores_the_env_defaults():
    persistence = MemoryPersistence()
    service = make_service(persistence)

    await service.update_config(LLMConfigUpdate(provider="other", model="m", temperature=0.9))

    assert persistence.value["provider"] == "other" and persistence.value["temperature"] == 0.9
    assert "api_key" not in str(persistence.value).lower()          # configuration only, never secrets

    config = service.reset_config()
    assert (config.provider, config.temperature) == ("primary", 0.2)
    assert persistence.value is None and persistence.cleared == 1


async def test_a_failed_validation_saves_nothing():
    persistence = MemoryPersistence()
    service = LLMService(
        ProviderRegistry([FakeProvider("primary"), FakeProvider("broken", connection_ok=False)]),
        LLMRuntimeConfig(provider="primary"),
        persistence,
    )
    with pytest.raises(Exception):
        await service.update_config(LLMConfigUpdate(provider="broken"))
    assert persistence.value is None


async def test_a_failing_save_does_not_break_the_update():
    persistence = MemoryPersistence()
    persistence.fail_saves = True
    service = make_service(persistence)
    config = await service.update_config(LLMConfigUpdate(provider="other", model="m"))
    assert config.provider == "other"


async def test_reset_also_restores_endpoint_overrides():
    service = make_service(MemoryPersistence())
    other = service.registry.get("other")
    await service.update_config(
        LLMConfigUpdate(provider="other", model="m", base_url_overrides={"other": "http://127.0.0.1:9999"})
    )
    assert other.base_url == "http://127.0.0.1:9999"

    service.reset_config()
    assert other.base_url == "http://other.test" and service.config.base_url_overrides == {}


def settings(**values):
    return Settings(_env_file=None, groq_api_key="", openai_api_key="", anthropic_api_key="", **values)


def test_saved_configuration_wins_over_env_defaults_at_start():
    saved = {"provider": "groq", "model": "llama-x", "temperature": 0.7, "max_tokens": 321}
    service = build_llm_service(settings(llm_default_provider="ollama", llm_temperature=0.3), persistence=MemoryPersistence(saved))
    assert (service.config.provider, service.config.model, service.config.temperature) == ("groq", "llama-x", 0.7)

    service.reset_config()      # reset goes back to the .env values, not the saved ones
    assert (service.config.provider, service.config.temperature) == ("ollama", 0.3)


@pytest.mark.parametrize("saved", [None, {}, {"provider": "groq", "temperature": 99}, {"nonsense": True}, ["list"]])
def test_unusable_saved_configuration_is_ignored(saved):
    service = build_llm_service(settings(llm_default_provider="ollama"), persistence=MemoryPersistence(saved))
    assert service.config.provider == "ollama"


def test_saved_endpoint_overrides_are_reapplied_only_where_allowed():
    saved = {"provider": "ollama", "model": "m", "base_url_overrides": {"ollama": "http://127.0.0.1:9", "groq": "http://evil"}}
    service = build_llm_service(settings(), persistence=MemoryPersistence(saved))

    assert service.registry.get("ollama").base_url == "http://127.0.0.1:9"
    assert service.registry.get("groq").base_url == "https://api.groq.com/openai/v1"      # credentials -> never redirected
    assert service.config.base_url_overrides == {"ollama": "http://127.0.0.1:9"}


def test_key_value_persistence_works_with_the_sql_settings_repository(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    db.create_all()
    persistence = KeyValuePersistence(SettingsRepository(db))

    assert persistence.load() is None
    persistence.save({"provider": "groq"})
    assert persistence.load() == {"provider": "groq"}
    persistence.clear()
    assert persistence.load() is None
    db.dispose()


# --------------------------------------------------------------------------- vector store factory


def test_factory_builds_chroma_in_the_configured_directory(tmp_path):
    store = create_vector_store(Settings(_env_file=None, chroma_path=tmp_path / "vec"))
    assert isinstance(store, ChromaVectorStore) and store.name == "chroma"
    assert (tmp_path / "vec").is_dir()


def test_pgvector_needs_a_postgres_url():
    with pytest.raises(ValidationFailedError) as info:
        create_vector_store(Settings(_env_file=None, vector_store="pgvector", database_url=""))
    assert info.value.code == "pgvector_url_required"

    with pytest.raises(ValidationFailedError):     # a SQLite metadata database cannot host pgvector
        create_vector_store(
            Settings(_env_file=None, vector_store="pgvector", database_url="sqlite:///x.db", vector_store_url="")
        )
