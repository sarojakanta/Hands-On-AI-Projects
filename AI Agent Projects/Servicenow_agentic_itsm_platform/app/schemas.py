from typing import Any, Literal

from pydantic import BaseModel, Field

WorkflowType = Literal["incident", "problem", "change", "request"]
LLMProvider = Literal["mock", "groq", "ollama", "openai_compatible"]

class ITSMRecord(BaseModel):
    sys_id: str
    number: str
    short_description: str
    description: str = ""
    state: str = "New"
    priority: str = "3"
    category: str = "general"
    impact: str = "3"
    urgency: str = "3"
    assignment_group: str = ""
    record_type: WorkflowType = "incident"
    source: str = "mock"
    extra: dict[str, Any] = {}

class InvestigationRequest(BaseModel):
    record_id: str = Field(..., description="ServiceNow record number or sys_id")
    workflow: WorkflowType = "incident"
    llm_provider: LLMProvider | None = None

class InvestigationResult(BaseModel):
    record_id: str
    workflow: WorkflowType
    category: str
    priority: str
    investigation_summary: str
    evidence: list[str]
    probable_root_cause: str
    confidence: float = Field(ge=0, le=1)
    recommendations: list[str]
    requires_human_approval: bool = True
    status: str = "awaiting_approval"
    agent_trace: list[str] = []
    raw: dict[str, Any] = {}
    # --- added by the multi-agent orchestration ---
    execution_id: str = ""
    affected_service: str = ""
    business_impact: str = ""
    root_cause_status: str = "unverified_hypothesis"
    hypotheses: list[dict[str, Any]] = []
    missing_information: list[str] = []
    evidence_items: list[dict[str, Any]] = []
    knowledge_sources: list[dict[str, Any]] = []
    knowledge_conflicts: list[dict[str, Any]] = []
    similar_incidents: list[dict[str, Any]] = []
    corrective_actions: list[str] = []
    preventive_actions: list[str] = []
    change_assessment: dict[str, Any] = {}
    proposed_actions: list[dict[str, Any]] = []
    verification: dict[str, Any] = {}
    security_flags: list[str] = []
    errors: list[str] = []
    skipped_agents: list[str] = []
    duplicate: bool = False
