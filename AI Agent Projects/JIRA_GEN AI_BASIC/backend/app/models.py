"""Pydantic models shared across the API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class JiraIssue(BaseModel):
    key: str
    summary: str
    description: str = ""
    status: str
    priority: str
    severity: str = "-"
    issue_type: str = Field(default="Story", alias="issueType")
    assignee: str = "Unassigned"
    creator: str = "-"
    created: str | None = None  # ISO creation date
    story_points: float | None = Field(default=None, alias="storyPoints")
    sprint: str | None = None

    model_config = {"populate_by_name": True}


class TestCase(BaseModel):
    id: str
    issue_key: str = Field(alias="issueKey")
    title: str
    steps: list[str] = []
    expected_result: str = Field(default="", alias="expectedResult")
    priority: str = "Medium"
    status: str = "Draft"  # Draft | Passed | Failed | Blocked | Not Run

    model_config = {"populate_by_name": True}


class GenerateTestCasesRequest(BaseModel):
    issue_key: str = Field(alias="issueKey")
    count: int = 4

    model_config = {"populate_by_name": True}


class SummarizeRequest(BaseModel):
    issue_key: str = Field(alias="issueKey")

    model_config = {"populate_by_name": True}


class GenAIResponse(BaseModel):
    source: str  # "llm" | "template"
    content: str | None = None
    test_cases: list[TestCase] | None = Field(default=None, alias="testCases")

    model_config = {"populate_by_name": True}


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    source: str  # "llm" | "rules"
    answer: str
    used_issue_count: int = Field(default=0, alias="usedIssueCount")
    issues: list[JiraIssue] = Field(default_factory=list)  # rows to render as a table

    model_config = {"populate_by_name": True}
