"""Jira MCP integration layer.

This module invokes a Jira MCP server over the stdio transport to fetch issues and
test cases. When ``USE_MOCK_DATA`` is true (default) or no MCP server is configured,
it transparently falls back to bundled sample data so the app always runs.
"""
from __future__ import annotations

import json
import logging

from .config import get_settings
from .jira_rest import JiraRestClient
from .models import JiraIssue, TestCase
from . import mock_data

logger = logging.getLogger("jira_genai.mcp")


class JiraDataProvider:
    """Fetches Jira data from a direct REST connection, an MCP server, or mock data.

    Resolution order: direct Jira REST (if creds + cloudId/URL) -> MCP server ->
    bundled mock data.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._rest: JiraRestClient | None = None
        if self.settings.jira_direct_enabled:
            self._rest = JiraRestClient(self.settings)

    # ---- Public API -----------------------------------------------------

    async def get_issues(self) -> list[JiraIssue]:
        # When MCP is preferred, try it first.
        if self.settings.prefer_mcp and self.settings.mcp_enabled:
            try:
                return await self._mcp_get_issues()
            except BaseException as exc:  # noqa: BLE001 - surface full detail incl. ExceptionGroup
                logger.warning("MCP get_issues failed, falling back: %r", exc)
                self._log_exception_group(exc)
        if self._rest is not None and not self.settings.prefer_mcp:
            try:
                return await self._rest.get_issues()
            except Exception as exc:
                logger.warning("Jira REST get_issues failed, falling back: %s", exc)
        # REST fallback if MCP was preferred but failed.
        if self._rest is not None:
            try:
                return await self._rest.get_issues()
            except Exception as exc:
                logger.warning("Jira REST get_issues fallback failed: %s", exc)
        # MCP fallback if REST was preferred but unavailable.
        if self.settings.mcp_enabled:
            try:
                return await self._mcp_get_issues()
            except Exception as exc:  # pragma: no cover
                logger.warning("MCP get_issues failed, using mock data: %s", exc)
        return list(mock_data.MOCK_ISSUES)

    async def get_issue(self, key: str) -> JiraIssue | None:
        issues = await self.get_issues()
        return next((i for i in issues if i.key.upper() == key.upper()), None)

    async def get_test_cases(self) -> list[TestCase]:
        if self._rest is not None:
            try:
                return await self._rest.get_test_cases()
            except Exception as exc:
                logger.warning("Jira REST get_test_cases failed, falling back: %s", exc)
            return []
        if self.settings.mcp_enabled:
            try:
                return await self._mcp_get_test_cases()
            except Exception as exc:  # pragma: no cover
                logger.warning("MCP get_test_cases failed, using mock data: %s", exc)
        return list(mock_data.MOCK_TEST_CASES)

    # ---- MCP calls ------------------------------------------------------

    @staticmethod
    def _log_exception_group(exc: BaseException) -> None:
        """Log nested exceptions from an ExceptionGroup (TaskGroup errors)."""
        excs = getattr(exc, "exceptions", None)
        if excs:
            for sub in excs:
                logger.warning("  MCP sub-error: %r", sub)
                JiraDataProvider._log_exception_group(sub)

    async def _call_mcp_tool(self, tool_name: str, arguments: dict) -> object:
        """Open a short-lived MCP stdio session and call a single tool.

        Kept dependency-optional: the ``mcp`` package is only imported when a live
        server is actually configured, so the app runs without it in mock mode.
        """
        from mcp import ClientSession, StdioServerParameters  # type: ignore
        from mcp.client.stdio import stdio_client  # type: ignore

        params = StdioServerParameters(
            command=self.settings.mcp_server_command,
            args=self.settings.mcp_args_list,
            env=self._jira_env(),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments)
                return self._extract_content(result)

    def _jira_env(self) -> dict[str, str]:
        """Environment passed to the mcp-atlassian server process.

        mcp-atlassian expects JIRA_URL, JIRA_USERNAME (the account email), and
        JIRA_API_TOKEN for Jira Cloud basic auth.
        """
        import os

        env: dict[str, str] = dict(os.environ)  # inherit PATH etc. so the child can run
        if self.settings.jira_url:
            env["JIRA_URL"] = self.settings.jira_url
        if self.settings.jira_email:
            env["JIRA_USERNAME"] = self.settings.jira_email
            env["JIRA_EMAIL"] = self.settings.jira_email
        if self.settings.jira_api_token:
            env["JIRA_API_TOKEN"] = self.settings.jira_api_token
        return env

    @staticmethod
    def _extract_content(result: object) -> object:
        """Pull JSON/text payload out of an MCP CallToolResult."""
        content = getattr(result, "content", None)
        if not content:
            return None
        first = content[0]
        text = getattr(first, "text", None)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    async def _mcp_get_issues(self) -> list[JiraIssue]:
        """Search issues via the mcp-atlassian ``jira_search`` tool."""
        jql = self.settings.jira_jql.strip()
        if not jql:
            key = self.settings.jira_project_key.strip()
            jql = f"project = {key} ORDER BY created DESC" if key else "ORDER BY created DESC"
        data = await self._call_mcp_tool(
            "jira_search",
            {
                "jql": jql,
                "limit": 100,
                "fields": "summary,description,status,priority,issuetype,assignee,reporter,creator,created,customfield_10016",
            },
        )
        return self._parse_issues(data)

    async def _mcp_get_test_cases(self) -> list[TestCase]:
        # mcp-atlassian has no test-case entity; return none so coverage is honest.
        return []

    # ---- Parsing (defensive: MCP servers vary in shape) -----------------

    @staticmethod
    def _parse_issues(data: object) -> list[JiraIssue]:
        """Parse issues from mcp-atlassian output.

        mcp-atlassian flattens issues to top-level keys with nested objects like
        ``status.name``, ``issue_type.name``, ``assignee.display_name``. This also
        tolerates the raw Jira ``fields`` shape as a fallback.
        """
        rows = JiraDataProvider._as_rows(data, keys=("issues", "results", "items"))
        issues: list[JiraIssue] = []
        for row in rows:
            fields = row.get("fields", {}) if isinstance(row.get("fields"), dict) else {}

            key = row.get("key") or fields.get("key") or "UNKNOWN"
            summary = row.get("summary") or fields.get("summary") or ""

            # status: flattened row.status.name OR fields.status.name
            status = (JiraDataProvider._nested(row, "status", "name")
                      or JiraDataProvider._nested(fields, "status", "name")
                      or "To Do")
            priority = (JiraDataProvider._nested(row, "priority", "name")
                        or JiraDataProvider._nested(fields, "priority", "name")
                        or "Medium")
            issue_type = (JiraDataProvider._nested(row, "issue_type", "name")
                          or JiraDataProvider._nested(row, "issuetype", "name")
                          or JiraDataProvider._nested(fields, "issuetype", "name")
                          or "Story")
            assignee = (JiraDataProvider._nested(row, "assignee", "display_name")
                        or JiraDataProvider._nested(row, "assignee", "displayName")
                        or JiraDataProvider._nested(fields, "assignee", "displayName")
                        or "Unassigned")

            # creator: prefer 'creator', fall back to 'reporter'
            creator = (JiraDataProvider._nested(row, "creator", "display_name")
                       or JiraDataProvider._nested(row, "creator", "displayName")
                       or JiraDataProvider._nested(row, "reporter", "display_name")
                       or JiraDataProvider._nested(row, "reporter", "displayName")
                       or JiraDataProvider._nested(fields, "creator", "displayName")
                       or JiraDataProvider._nested(fields, "reporter", "displayName")
                       or "-")

            # created date: flattened string or under fields
            created = (row.get("created")
                       or JiraDataProvider._nested(row, "timetracking", "created")
                       or fields.get("created")
                       or None)
            created = JiraDataProvider._clean_date(created)

            # severity: often a custom field; check common shapes
            severity = (JiraDataProvider._nested(row, "severity", "value")
                        or JiraDataProvider._nested(row, "severity", "name")
                        or (row.get("severity") if isinstance(row.get("severity"), str) else "")
                        or JiraDataProvider._nested(fields, "customfield_10014", "value")
                        or "-")

            desc = row.get("description") or fields.get("description") or ""
            if isinstance(desc, dict):
                desc = ""  # ADF object; skip in MCP path

            points = row.get("story_points") or fields.get("customfield_10016")

            issues.append(JiraIssue(
                key=key, summary=summary, description=desc if isinstance(desc, str) else "",
                status=status, priority=priority, severity=severity or "-",
                issueType=issue_type, assignee=assignee, creator=creator or "-",
                created=created, storyPoints=points,
            ))
        return issues

    @staticmethod
    def _clean_date(value: object) -> str | None:
        """Normalize a Jira datetime to YYYY-MM-DD when possible."""
        if not value or not isinstance(value, str):
            return None
        # Jira returns e.g. '2026-09-20T14:33:00.000+0000' -> take the date part.
        return value[:10]

    @staticmethod
    def _parse_test_cases(data: object) -> list[TestCase]:
        rows = JiraDataProvider._as_rows(data, keys=("testCases", "results", "items"))
        cases: list[TestCase] = []
        for i, row in enumerate(rows, start=1):
            cases.append(TestCase(
                id=str(row.get("id", f"TC-{i}")),
                issueKey=row.get("issueKey", row.get("issue_key", "")),
                title=row.get("title", row.get("summary", "Untitled")),
                steps=row.get("steps", []) or [],
                expectedResult=row.get("expectedResult", row.get("expected_result", "")),
                priority=row.get("priority", "Medium"),
                status=row.get("status", "Draft"),
            ))
        return cases

    @staticmethod
    def _as_rows(data: object, keys: tuple[str, ...]) -> list[dict]:
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            for k in keys:
                if isinstance(data.get(k), list):
                    return [r for r in data[k] if isinstance(r, dict)]
        return []

    @staticmethod
    def _nested(obj: dict, *path: str, default: str = "") -> str:
        cur: object = obj
        for p in path:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return default
        return cur if isinstance(cur, str) and cur else default


provider = JiraDataProvider()
