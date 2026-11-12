"""Aggregate Jira issues and test cases into chart-ready metrics."""
from __future__ import annotations

from collections import Counter

from .models import JiraIssue, TestCase


def build_metrics(issues: list[JiraIssue], test_cases: list[TestCase]) -> dict:
    status_counts = Counter(i.status for i in issues)
    priority_counts = Counter(i.priority for i in issues)
    type_counts = Counter(i.issue_type for i in issues)
    tc_status_counts = Counter(tc.status for tc in test_cases)

    issues_with_tests = {tc.issue_key for tc in test_cases}
    covered = sum(1 for i in issues if i.key in issues_with_tests)
    coverage_pct = round((covered / len(issues) * 100), 1) if issues else 0.0

    points_by_sprint: dict[str, float] = {}
    for i in issues:
        if i.sprint and i.story_points:
            points_by_sprint[i.sprint] = points_by_sprint.get(i.sprint, 0) + i.story_points

    return {
        "totals": {
            "issues": len(issues),
            "testCases": len(test_cases),
            "coveragePct": coverage_pct,
            "openBugs": sum(1 for i in issues if i.issue_type == "Bug" and i.status != "Done"),
        },
        "statusDistribution": [{"name": k, "value": v} for k, v in status_counts.items()],
        "priorityDistribution": [{"name": k, "value": v} for k, v in priority_counts.items()],
        "typeDistribution": [{"name": k, "value": v} for k, v in type_counts.items()],
        "testCaseStatus": [{"name": k, "value": v} for k, v in tc_status_counts.items()],
        "coverage": [
            {"name": "Covered", "value": covered},
            {"name": "Not covered", "value": len(issues) - covered},
        ],
        "pointsBySprint": [{"name": k, "value": v} for k, v in points_by_sprint.items()],
    }
