# Data Models and Schemas
from typing import Dict, List, Optional, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime


class AlertPayload(BaseModel):
    """Alertmanager webhook payload"""
    receiver: str
    status: str
    alerts: List[Dict[str, Any]]
    groupLabels: Dict[str, str]
    commonLabels: Dict[str, str]
    commonAnnotations: Dict[str, str]
    externalURL: str
    version: str
    groupKey: str


class AgentState(BaseModel):
    """LangGraph agent state"""
    # Alert context
    alert_name: str
    alert_severity: Literal["critical", "warning", "info"]
    affected_machine: str
    alert_timestamp: str
    alert_labels: Dict[str, str]
    
    # Supervisor state
    selected_agents: List[str] = Field(default_factory=list)
    current_agent: Optional[str] = None
    
    # Agent outputs
    linux_health: Optional[Dict] = None
    nginx_status: Optional[Dict] = None
    app_health: Optional[Dict] = None
    api_metrics: Optional[Dict] = None
    database_health: Optional[Dict] = None
    middleware_status: Optional[Dict] = None
    log_analysis: Optional[Dict] = None
    
    # RCA results
    root_cause_analysis: Optional[Dict] = None
    incident_report: Optional[Dict] = None
    
    # Metadata
    execution_start: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    execution_steps: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class AgentOutput(BaseModel):
    """Standard agent output format"""
    agent_name: str
    status: Literal["healthy", "degraded", "critical", "unknown"]
    metrics: Dict[str, Any] = Field(default_factory=dict)
    anomalies: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "high"
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class RCAOutput(BaseModel):
    """Root Cause Analysis output"""
    incident_id: str
    root_cause: str
    confidence: float
    confidence_basis: str
    evidence: List[Dict[str, Any]]
    timeline: List[Dict[str, str]]
    affected_components: List[str]
    probable_causes: List[Dict[str, Any]]
    recommendations: List[Dict[str, str]]


class IncidentReport(BaseModel):
    """Final incident report"""
    incident_id: str
    created_at: str
    severity: Literal["high", "medium", "low"]
    status: Literal["identified", "in_progress", "resolved"]
    affected_system: str
    summary: str
    root_cause_analysis: RCAOutput
    evidence_summary: Dict[str, Any]
    impact: Dict[str, Any]
    recommendations: List[str]
    similar_incidents: List[Dict[str, Any]] = Field(default_factory=list)
    notification_sent: bool = False
    requires_human_approval: bool = True
    auto_remediation_enabled: bool = False
