"""Central configuration loaded from environment variables / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .providers import DEFAULT_PROVIDER, PROVIDERS

# Load variables from a local .env file if present. Existing environment
# variables always take precedence over the file.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the troubleshooting assistant.

    Only Tavily and the choice of default LLM provider live here. Per-provider
    keys / models / base URLs are resolved on demand from ``providers.py`` so a
    user can switch providers at runtime from the UI.
    """

    tavily_api_key: str | None
    default_provider: str

    @property
    def tavily_ready(self) -> bool:
        return bool(self.tavily_api_key)


def _resolve_default_provider() -> str:
    """Pick the startup provider from LLM_PROVIDER, defaulting safely."""
    requested = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    return requested if requested in PROVIDERS else DEFAULT_PROVIDER


def load_settings() -> Settings:
    """Read configuration from the environment and return a Settings object."""

    return Settings(
        tavily_api_key=os.getenv("TAVILY_API_KEY") or None,
        default_provider=_resolve_default_provider(),
    )
