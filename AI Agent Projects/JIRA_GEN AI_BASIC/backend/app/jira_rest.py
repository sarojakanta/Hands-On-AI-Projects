"""Direct Jira Cloud REST API client (v3).

Used when ``USE_MOCK_DATA=false`` and Jira credentials are present. Authenticates
with email + API token (Basic auth) and can target either the cloudId gateway
(https://api.atlassian.com/ex/jira/<cloudId>/...) or a direct site URL.
"""
from __future__ import annotations

import base64
import logging

import httpx

from .config import Settings
from .models import JiraIssue, TestCase

logger = logging.getLogger("jira_genai.jira_rest")


class JiraRestClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = settings.jira_api_base
        token = f"{settings.jira_email}:{settings.jira_api_token}".encode()
        self._auth_header = "Basic " + base64.b64encode(token).decode()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def verify(self) -> dict:
        """Confirm auth works. Returns the current user info."""
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{self.base}/myself", headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def get_issues(self) -> list[JiraIssue]:
        """Fetch issues via the enhanced search endpoint (/search/jql).

        The legacy /search endpoint was removed by Atlassian (returns 410 Gone).
        The new endpoint paginates with ``nextPageToken`` instead of ``startAt``.
        """
        jql = self.settings.jira_jql.strip()
        if not jql:
            key = self.settings.jira_project_key.strip()
            jql = f"project = {key} ORDER BY created DESC" if key else "ORDER BY created DESC"

        fields = "summary,description,status,priority,issuetype,assignee,reporter,creator,created,customfield_10016"
        issues: list[JiraIssue] = []
        next_token: str | None = None

        async with httpx.AsyncClient(timeout=30) as client:
            for _ in range(20):  # safety cap: up to 20 pages
                params: dict[str, object] = {"jql": jql, "maxResults": 100, "fields": fields}
                if next_token:
                    params["nextPageToken"] = next_token

                resp = await client.get(
                    f"{self.base}/search/jql", headers=self._headers(), params=params
                )
                resp.raise_for_status()
                data = resp.json()

                for row in data.get("issues", []):
                    issues.append(self._parse_issue(row))

                next_token = data.get("nextPageToken")
                if data.get("isLast", True) or not next_token:
                    break

        return issues

    async def get_test_cases(self) -> list[TestCase]:
        """Jira core has no native test-case entity.

        Test management lives in add-ons (Xray, Zephyr) with their own APIs. Without
        one configured we return an empty list so the UI shows real coverage (0%)
        rather than mixing in mock data.
        """
        return []

    # ---- Parsing --------------------------------------------------------

    @staticmethod
    def _parse_issue(row: dict) -> JiraIssue:
        f = row.get("fields", {}) or {}
        created = f.get("created")
        created = created[:10] if isinstance(created, str) else None
        creator = (JiraRestClient._nested(f, "creator", "displayName")
                   or JiraRestClient._nested(f, "reporter", "displayName")
                   or "-")
        severity = (JiraRestClient._nested(f, "customfield_10014", "value")
                    or JiraRestClient._nested(f, "severity", "value")
                    or "-")
        return JiraIssue(
            key=row.get("key", "UNKNOWN"),
            summary=f.get("summary", "") or "",
            description=JiraRestClient._render_description(f.get("description")),
            status=JiraRestClient._nested(f, "status", "name", default="To Do"),
            priority=JiraRestClient._nested(f, "priority", "name", default="Medium"),
            severity=severity or "-",
            issueType=JiraRestClient._nested(f, "issuetype", "name", default="Story"),
            assignee=JiraRestClient._nested(f, "assignee", "displayName", default="Unassigned"),
            creator=creator or "-",
            created=created,
            storyPoints=f.get("customfield_10016"),
        )

    @staticmethod
    def _render_description(desc: object) -> str:
        """Jira v3 descriptions use Atlassian Document Format (ADF). Extract text."""
        if isinstance(desc, str):
            return desc
        if not isinstance(desc, dict):
            return ""
        parts: list[str] = []

        def walk(node: object) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text" and isinstance(node.get("text"), str):
                    parts.append(node["text"])
                for child in node.get("content", []) or []:
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(desc)
        return " ".join(parts).strip()

    @staticmethod
    def _nested(obj: dict, *path: str, default: str = "") -> str:
        cur: object = obj
        for p in path:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return default
        return cur if isinstance(cur, str) and cur else default
