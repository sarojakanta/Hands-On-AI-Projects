"""Persistence of the active LLM configuration (provider, model, sampling, fallback). Never stores secrets."""

from __future__ import annotations

from typing import Any, Protocol


class ConfigPersistence(Protocol):
    def load(self) -> dict[str, Any] | None: ...
    def save(self, config: dict[str, Any]) -> None: ...
    def clear(self) -> None: ...


class KeyValuePersistence:
    """Stores the configuration under one key of a key/value settings repository (see ``SettingsRepository``)."""

    def __init__(self, repository: Any, key: str = "llm_runtime_config") -> None:
        self._repository = repository
        self._key = key

    def load(self) -> dict[str, Any] | None:
        return self._repository.get(self._key)

    def save(self, config: dict[str, Any]) -> None:
        self._repository.set(self._key, config)

    def clear(self) -> None:
        self._repository.delete(self._key)
