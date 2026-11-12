"""Application settings loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path to backend/.env so settings load regardless of the process CWD.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    use_mock_data: bool = True

    # LLM
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""

    # MCP
    mcp_server_command: str = ""
    mcp_server_args: str = ""

    # Which Jira transport to prefer: "auto" (rest then mcp), "mcp", or "rest".
    jira_source: str = "auto"

    # Jira
    jira_url: str = ""
    jira_cloud_id: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "DEMO"
    jira_jql: str = ""

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def mcp_args_list(self) -> list[str]:
        return [a for a in self.mcp_server_args.split(" ") if a]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def mcp_enabled(self) -> bool:
        return not self.use_mock_data and bool(self.mcp_server_command)

    @property
    def prefer_mcp(self) -> bool:
        """True when MCP should be tried before the direct REST client."""
        return self.jira_source.strip().lower() == "mcp"

    @property
    def prefer_rest_only(self) -> bool:
        return self.jira_source.strip().lower() == "rest"

    @property
    def jira_direct_enabled(self) -> bool:
        """True when we can call the Jira REST API directly (no MCP server)."""
        has_creds = bool(self.jira_email and self.jira_api_token)
        has_target = bool(self.jira_cloud_id or self.jira_site_base_url)
        return not self.use_mock_data and has_creds and has_target

    @property
    def jira_site_base_url(self) -> str:
        """Return a usable Jira site base URL, or empty if not a real site.

        Ignores the Atlassian account portal (home.atlassian.com), which is not
        a Jira REST endpoint.
        """
        url = self.jira_url.strip().rstrip("/")
        if not url:
            return ""
        if "home.atlassian.com" in url or "id.atlassian.com" in url:
            return ""
        return url

    @property
    def jira_api_base(self) -> str:
        """Base URL for Jira REST API v3.

        Prefers the cloudId gateway (works regardless of the site's vanity name),
        falling back to a direct site URL.
        """
        if self.jira_cloud_id:
            return f"https://api.atlassian.com/ex/jira/{self.jira_cloud_id}/rest/api/3"
        if self.jira_site_base_url:
            return f"{self.jira_site_base_url}/rest/api/3"
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
