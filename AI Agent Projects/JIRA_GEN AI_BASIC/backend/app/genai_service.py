"""GenAI service: test case generation and issue summarization.

Uses an OpenAI-compatible LLM when ``OPENAI_API_KEY`` is set. Otherwise it falls
back to a deterministic template engine so the feature works out of the box.
"""
from __future__ import annotations

import json
import logging

from collections import Counter

from .config import get_settings
from .models import AskResponse, GenAIResponse, JiraIssue, TestCase

logger = logging.getLogger("jira_genai.genai")


class GenAIService:
    def __init__(self) -> None:
        self.settings = get_settings()

    # ---- Test case generation ------------------------------------------

    async def generate_test_cases(self, issue: JiraIssue, count: int = 4) -> GenAIResponse:
        if self.settings.llm_enabled:
            try:
                cases = await self._llm_generate_test_cases(issue, count)
                return GenAIResponse(source="llm", testCases=cases)
            except Exception as exc:  # pragma: no cover
                logger.warning("LLM test case generation failed, using template: %s", exc)
        return GenAIResponse(source="template", testCases=self._template_test_cases(issue, count))

    async def summarize_issue(self, issue: JiraIssue) -> GenAIResponse:
        if self.settings.llm_enabled:
            try:
                content = await self._llm_summarize(issue)
                return GenAIResponse(source="llm", content=content)
            except Exception as exc:  # pragma: no cover
                logger.warning("LLM summarization failed, using template: %s", exc)
        return GenAIResponse(source="template", content=self._template_summary(issue))

    # ---- Question wizard (Ask) -----------------------------------------

    async def ask(self, question: str, issues: list[JiraIssue]) -> AskResponse:
        """Answer a natural-language question about the project.

        Uses the LLM with the real Jira issues as context when a key is set,
        otherwise a rule-based engine that handles common questions.
        """
        question = (question or "").strip()
        if not question:
            return AskResponse(source="rules", answer="Please ask a question about your project.",
                               usedIssueCount=len(issues))

        # Select which issues to show in the results table, based on the question.
        table_issues = self._select_issues_for_table(question, issues)

        if self.settings.llm_enabled:
            try:
                answer = await self._llm_ask(question, issues)
                return AskResponse(source="llm", answer=answer,
                                   usedIssueCount=len(issues), issues=table_issues)
            except Exception as exc:  # pragma: no cover
                logger.warning("LLM ask failed, using rules: %s", exc)
        return AskResponse(source="rules", answer=self._rules_ask(question, issues),
                           usedIssueCount=len(issues), issues=table_issues)

    @staticmethod
    def _select_issues_for_table(question: str, issues: list[JiraIssue]) -> list[JiraIssue]:
        """Pick the issues to display as a table, filtered by intent in the question."""
        q = question.lower()
        result = issues

        # Type filters
        if "bug" in q:
            result = [i for i in result if i.issue_type.lower() == "bug"]
        elif "story" in q or "stories" in q:
            result = [i for i in result if i.issue_type.lower() == "story"]
        elif "task" in q:
            result = [i for i in result if i.issue_type.lower() == "task"]

        # Status filters
        if "in progress" in q:
            result = [i for i in result if "progress" in i.status.lower()]
        elif any(w in q for w in ("done", "closed", "complete", "resolved")):
            result = [i for i in result if i.status.lower() in ("done", "closed", "resolved")]
        elif any(w in q for w in ("open", "to do", "todo", "backlog")):
            result = [i for i in result if i.status.lower() in ("to do", "open", "backlog", "idea", "in progress")]

        # Priority filters
        for p in ("critical", "high", "medium", "low"):
            if p in q:
                result = [i for i in result if i.priority.lower() == p]
                break

        # If a filter emptied the list, fall back to all issues so the table isn't blank.
        return result if result else issues

    async def _llm_ask(self, question: str, issues: list[JiraIssue]) -> str:
        client = self._client()
        context = self._issues_as_context(issues)
        prompt = (
            "You are a helpful Jira project assistant. Answer the user's question using ONLY the "
            "issue data provided. Be concise and specific, cite issue keys where relevant, and if "
            "the data doesn't contain the answer, say so.\n\n"
            f"ISSUE DATA (JSON lines):\n{context}\n\n"
            f"QUESTION: {question}"
        )
        resp = await client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()

    @staticmethod
    def _issues_as_context(issues: list[JiraIssue]) -> str:
        lines = []
        for i in issues:
            lines.append(
                f"{i.key} | type={i.issue_type} | status={i.status} | priority={i.priority} "
                f"| assignee={i.assignee} | points={i.story_points or '-'} | {i.summary}"
            )
        return "\n".join(lines) if lines else "(no issues)"

    def _rules_ask(self, question: str, issues: list[JiraIssue]) -> str:
        """Deterministic answers for common questions without an LLM."""
        q = question.lower()
        total = len(issues)
        if total == 0:
            return "There are no issues available to answer from."

        status_counts = Counter(i.status for i in issues)
        type_counts = Counter(i.issue_type for i in issues)
        priority_counts = Counter(i.priority for i in issues)
        assignee_counts = Counter(i.assignee for i in issues)

        def fmt(counter: Counter) -> str:
            return ", ".join(f"{k}: {v}" for k, v in counter.most_common())

        # Bugs
        if "bug" in q:
            bugs = [i for i in issues if i.issue_type.lower() == "bug"]
            open_bugs = [i for i in bugs if i.status.lower() not in ("done", "closed", "resolved")]
            if not bugs:
                return "There are no issues of type Bug in the current data."
            listing = "; ".join(f"{b.key} ({b.status})" for b in bugs[:10])
            return (f"There are {len(bugs)} bug(s), {len(open_bugs)} still open. {listing}")

        # Counts / how many / total
        if any(w in q for w in ("how many", "count", "number of", "total")):
            if "progress" in q:
                n = sum(v for k, v in status_counts.items() if "progress" in k.lower())
                return f"{n} issue(s) are In Progress."
            if "done" in q or "complete" in q or "closed" in q:
                n = sum(v for k, v in status_counts.items() if k.lower() in ("done", "closed", "resolved"))
                return f"{n} issue(s) are Done/Closed."
            if "to do" in q or "todo" in q or "open" in q:
                n = sum(v for k, v in status_counts.items() if k.lower() in ("to do", "open", "backlog", "idea"))
                return f"{n} issue(s) are open / to do."
            return f"There are {total} issue(s) total. By status — {fmt(status_counts)}."

        # Status breakdown
        if "status" in q:
            return f"Status breakdown ({total} issues): {fmt(status_counts)}."

        # Priority
        if "priorit" in q:
            return f"Priority breakdown: {fmt(priority_counts)}."

        # Type
        if "type" in q or "kind" in q:
            return f"Issue types: {fmt(type_counts)}."

        # Assignee / who is working on what
        if any(w in q for w in ("assignee", "who is", "who's", "assigned", "working on")):
            return f"Work by assignee: {fmt(assignee_counts)}."

        # What to test first / risk
        if any(w in q for w in ("test first", "what should i test", "risk", "priority order")):
            ranked = sorted(
                issues,
                key=lambda i: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(i.priority.lower(), 4),
            )
            top = ranked[:5]
            listing = "\n".join(f"- {i.key} [{i.priority}] {i.summary}" for i in top)
            return "Suggested testing order (highest priority first):\n" + listing

        # Summary / overview
        if any(w in q for w in ("summar", "overview", "status of the project", "how is the project")):
            return (
                f"Project overview: {total} issues. "
                f"Status — {fmt(status_counts)}. "
                f"Types — {fmt(type_counts)}. "
                f"Priorities — {fmt(priority_counts)}."
            )

        # List issues
        if any(w in q for w in ("list", "show", "what are the issues", "all issues")):
            listing = "\n".join(f"- {i.key} [{i.status}] {i.summary}" for i in issues[:15])
            return f"Here are the issues ({total} total):\n{listing}"

        # Fallback: give an overview and hint.
        return (
            f"I can answer from {total} Jira issue(s). "
            f"Quick overview — status: {fmt(status_counts)}; types: {fmt(type_counts)}. "
            "Try asking about counts, statuses, priorities, bugs, assignees, or what to test first. "
            "Tip: set OPENAI_API_KEY in .env for free-form AI answers."
        )

    # ---- LLM calls ------------------------------------------------------

    def _client(self):
        from openai import AsyncOpenAI  # imported lazily

        kwargs = {"api_key": self.settings.openai_api_key}
        if self.settings.openai_base_url:
            kwargs["base_url"] = self.settings.openai_base_url
        return AsyncOpenAI(**kwargs)

    async def _llm_generate_test_cases(self, issue: JiraIssue, count: int) -> list[TestCase]:
        client = self._client()
        prompt = (
            f"You are a senior QA engineer. Generate {count} thorough test cases for this Jira issue. "
            "Include positive, negative, and edge cases. Return STRICT JSON with a top-level key "
            '"testCases", each having: title, steps (array of strings), expectedResult, priority '
            "(Low|Medium|High|Critical).\n\n"
            f"Issue {issue.key} [{issue.issue_type}, priority {issue.priority}]\n"
            f"Summary: {issue.summary}\nDescription: {issue.description}"
        )
        resp = await client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.4,
        )
        payload = json.loads(resp.choices[0].message.content or "{}")
        rows = payload.get("testCases", []) if isinstance(payload, dict) else []
        cases: list[TestCase] = []
        for i, row in enumerate(rows, start=1):
            cases.append(TestCase(
                id=f"{issue.key}-AI-{i}",
                issueKey=issue.key,
                title=row.get("title", f"Generated case {i}"),
                steps=row.get("steps", []) or [],
                expectedResult=row.get("expectedResult", ""),
                priority=row.get("priority", issue.priority),
                status="Draft",
            ))
        return cases or self._template_test_cases(issue, count)

    async def _llm_summarize(self, issue: JiraIssue) -> str:
        client = self._client()
        prompt = (
            "Summarize this Jira issue in 2-3 sentences for a status report, then list up to 3 risks "
            "as bullet points.\n\n"
            f"Issue {issue.key}: {issue.summary}\n{issue.description}"
        )
        resp = await client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return (resp.choices[0].message.content or "").strip()

    # ---- Template fallback ---------------------------------------------

    @staticmethod
    def _template_test_cases(issue: JiraIssue, count: int) -> list[TestCase]:
        feature = issue.summary.rstrip(".")
        blueprints = [
            ("Happy path: {f} works with valid input",
             ["Set up valid preconditions", f"Perform the main action for '{feature}'", "Submit / confirm"],
             "The feature behaves as expected and a success state is shown.", "High"),
            ("Negative: {f} rejects invalid input",
             ["Provide invalid or malformed input", "Attempt the main action", "Observe the response"],
             "A clear validation error is shown and no changes are persisted.", "High"),
            ("Edge case: {f} handles empty / boundary values",
             ["Provide empty or boundary values", "Attempt the action"],
             "The system handles the boundary gracefully without crashing.", "Medium"),
            ("Permissions: {f} respects access control",
             ["Log in as an unauthorized user", f"Attempt to use '{feature}'"],
             "Access is denied with an appropriate message.", "Medium"),
            ("Resilience: {f} recovers from a backend error",
             ["Simulate a backend/network failure", "Retry the action"],
             "A friendly error is shown and the user can retry successfully.", "Low"),
        ]
        cases: list[TestCase] = []
        for i in range(min(count, len(blueprints))):
            title_t, steps, expected, prio = blueprints[i]
            cases.append(TestCase(
                id=f"{issue.key}-AI-{i + 1}",
                issueKey=issue.key,
                title=title_t.format(f=feature),
                steps=steps,
                expectedResult=expected,
                priority=prio,
                status="Draft",
            ))
        return cases

    @staticmethod
    def _template_summary(issue: JiraIssue) -> str:
        return (
            f"**{issue.key} — {issue.summary}**\n\n"
            f"This {issue.issue_type.lower()} is currently *{issue.status}* with *{issue.priority}* priority, "
            f"assigned to {issue.assignee}. {issue.description}\n\n"
            "**Potential risks:**\n"
            f"- Scope may expand if acceptance criteria for '{issue.summary}' are not fully defined.\n"
            "- Dependencies on related issues could delay delivery.\n"
            "- Insufficient test coverage may allow regressions to reach production."
        )


genai_service = GenAIService()
