"""FastAPI application: Jira GenAI solution API."""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .genai_service import genai_service
from .mcp_client import provider
from .metrics import build_metrics
from .mock_data import MOCK_BURNDOWN
from .models import (
    AskRequest,
    AskResponse,
    GenAIResponse,
    GenerateTestCasesRequest,
    SummarizeRequest,
)

logging.basicConfig(level=logging.INFO)
settings = get_settings()

app = FastAPI(title="Jira GenAI Solution API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mockData": settings.use_mock_data,
        "llmEnabled": settings.llm_enabled,
        "mcpEnabled": settings.mcp_enabled,
        "jiraDirectEnabled": settings.jira_direct_enabled,
        "jiraApiBase": settings.jira_api_base,
    }


@app.get("/api/jira/verify")
async def jira_verify() -> dict:
    """Check that the configured Jira credentials actually authenticate."""
    if not settings.jira_direct_enabled:
        return {
            "connected": False,
            "reason": "Direct Jira not enabled. Set USE_MOCK_DATA=false and provide "
                      "JIRA_EMAIL, JIRA_API_TOKEN and JIRA_CLOUD_ID (or a real JIRA_URL).",
        }
    from .jira_rest import JiraRestClient
    client = JiraRestClient(settings)
    try:
        me = await client.verify()
        return {
            "connected": True,
            "account": me.get("emailAddress") or me.get("displayName"),
            "apiBase": settings.jira_api_base,
        }
    except Exception as exc:
        return {"connected": False, "reason": str(exc), "apiBase": settings.jira_api_base}


@app.get("/api/issues")
async def list_issues():
    issues = await provider.get_issues()
    return [i.model_dump(by_alias=True) for i in issues]


@app.get("/api/issues/{key}")
async def get_issue(key: str):
    issue = await provider.get_issue(key)
    if not issue:
        raise HTTPException(status_code=404, detail=f"Issue {key} not found")
    return issue.model_dump(by_alias=True)


@app.get("/api/test-cases")
async def list_test_cases():
    cases = await provider.get_test_cases()
    return [c.model_dump(by_alias=True) for c in cases]


@app.get("/api/metrics")
async def get_metrics():
    issues = await provider.get_issues()
    cases = await provider.get_test_cases()
    metrics = build_metrics(issues, cases)
    metrics["burndown"] = MOCK_BURNDOWN
    return metrics


@app.post("/api/genai/test-cases", response_model=GenAIResponse)
async def generate_test_cases(req: GenerateTestCasesRequest):
    issue = await provider.get_issue(req.issue_key)
    if not issue:
        raise HTTPException(status_code=404, detail=f"Issue {req.issue_key} not found")
    return await genai_service.generate_test_cases(issue, req.count)


@app.post("/api/genai/summarize", response_model=GenAIResponse)
async def summarize_issue(req: SummarizeRequest):
    issue = await provider.get_issue(req.issue_key)
    if not issue:
        raise HTTPException(status_code=404, detail=f"Issue {req.issue_key} not found")
    return await genai_service.summarize_issue(issue)


@app.post("/api/genai/ask", response_model=AskResponse)
async def ask_question(req: AskRequest):
    """Answer a natural-language question about the project using live Jira data."""
    issues = await provider.get_issues()
    return await genai_service.ask(req.question, issues)
