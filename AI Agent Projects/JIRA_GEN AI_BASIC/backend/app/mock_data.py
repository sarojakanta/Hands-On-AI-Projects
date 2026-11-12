"""Realistic sample data so the app works without a live Jira/MCP connection."""
from __future__ import annotations

from .models import JiraIssue, TestCase

MOCK_ISSUES: list[JiraIssue] = [
    JiraIssue(key="DEMO-101", summary="User login with email and password",
              description="As a user I want to log in with my email and password so that I can access my account securely.",
              status="Done", priority="High", issueType="Story", assignee="Alice", storyPoints=5, sprint="Sprint 12"),
    JiraIssue(key="DEMO-102", summary="Password reset via email link",
              description="As a user I want to reset my password through an email link so I can regain access if I forget it.",
              status="In Progress", priority="High", issueType="Story", assignee="Bob", storyPoints=3, sprint="Sprint 12"),
    JiraIssue(key="DEMO-103", summary="Dashboard loads slowly on large datasets",
              description="The analytics dashboard takes over 8 seconds to load when a project has more than 1000 issues.",
              status="In Progress", priority="Critical", issueType="Bug", assignee="Carol", storyPoints=8, sprint="Sprint 12"),
    JiraIssue(key="DEMO-104", summary="Export report to PDF",
              description="As a project manager I want to export the metrics report to PDF so I can share it with stakeholders.",
              status="To Do", priority="Medium", issueType="Story", assignee="Dan", storyPoints=5, sprint="Sprint 13"),
    JiraIssue(key="DEMO-105", summary="Add two-factor authentication",
              description="As a security-conscious user I want 2FA so my account has an extra layer of protection.",
              status="To Do", priority="High", issueType="Story", assignee="Alice", storyPoints=8, sprint="Sprint 13"),
    JiraIssue(key="DEMO-106", summary="Search filters not persisting",
              description="Selected search filters reset after navigating away from the issues page.",
              status="Done", priority="Low", issueType="Bug", assignee="Bob", storyPoints=2, sprint="Sprint 11"),
    JiraIssue(key="DEMO-107", summary="Onboarding tour for new users",
              description="As a new user I want a guided tour so I understand the main features quickly.",
              status="In Review", priority="Medium", issueType="Story", assignee="Carol", storyPoints=3, sprint="Sprint 12"),
    JiraIssue(key="DEMO-108", summary="API rate limiting for public endpoints",
              description="As an operator I want rate limiting so the public API is protected from abuse.",
              status="To Do", priority="Medium", issueType="Task", assignee="Dan", storyPoints=5, sprint="Sprint 13"),
]

MOCK_TEST_CASES: list[TestCase] = [
    TestCase(id="TC-1", issueKey="DEMO-101", title="Valid login succeeds",
             steps=["Navigate to login page", "Enter valid email", "Enter valid password", "Click Sign In"],
             expectedResult="User is redirected to the dashboard.", priority="High", status="Passed"),
    TestCase(id="TC-2", issueKey="DEMO-101", title="Invalid password is rejected",
             steps=["Navigate to login page", "Enter valid email", "Enter wrong password", "Click Sign In"],
             expectedResult="An 'invalid credentials' error is shown.", priority="High", status="Passed"),
    TestCase(id="TC-3", issueKey="DEMO-101", title="Empty fields blocked",
             steps=["Navigate to login page", "Leave fields empty", "Click Sign In"],
             expectedResult="Validation messages appear for both fields.", priority="Medium", status="Failed"),
    TestCase(id="TC-4", issueKey="DEMO-102", title="Reset link sent to registered email",
             steps=["Open forgot password", "Enter registered email", "Submit"],
             expectedResult="Confirmation shown and email is sent.", priority="High", status="Not Run"),
    TestCase(id="TC-5", issueKey="DEMO-103", title="Dashboard loads under 3s with 1000 issues",
             steps=["Open a project with 1000+ issues", "Load dashboard", "Measure load time"],
             expectedResult="Dashboard renders within 3 seconds.", priority="Critical", status="Failed"),
    TestCase(id="TC-6", issueKey="DEMO-106", title="Filters persist across navigation",
             steps=["Apply filters on issues page", "Navigate away", "Return to issues page"],
             expectedResult="Previously applied filters are still active.", priority="Low", status="Passed"),
    TestCase(id="TC-7", issueKey="DEMO-107", title="Tour can be skipped",
             steps=["Log in as a new user", "Start onboarding tour", "Click Skip"],
             expectedResult="Tour closes and does not reappear.", priority="Medium", status="Draft"),
]

# Sprint burndown sample: ideal vs actual remaining points per day.
MOCK_BURNDOWN = [
    {"day": "Day 1", "ideal": 30, "remaining": 30},
    {"day": "Day 2", "ideal": 27, "remaining": 29},
    {"day": "Day 3", "ideal": 24, "remaining": 27},
    {"day": "Day 4", "ideal": 21, "remaining": 25},
    {"day": "Day 5", "ideal": 18, "remaining": 20},
    {"day": "Day 6", "ideal": 15, "remaining": 18},
    {"day": "Day 7", "ideal": 12, "remaining": 13},
    {"day": "Day 8", "ideal": 9, "remaining": 10},
    {"day": "Day 9", "ideal": 6, "remaining": 6},
    {"day": "Day 10", "ideal": 0, "remaining": 3},
]
