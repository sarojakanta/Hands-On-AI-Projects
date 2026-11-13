"""SQLAlchemy models. Portable across SQLite (dev/test) and PostgreSQL (prod)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    # roles: viewer | analyst | approver | admin  (comma separated for simplicity)
    roles: Mapped[str] = mapped_column(String(255), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=60.0)
    llm_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    __table_args__ = (Index("ix_wf_record", "workflow", "record_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workflow: Mapped[str] = mapped_column(String(20))
    record_number: Mapped[str] = mapped_column(String(50))
    record_sys_id: Mapped[str] = mapped_column(String(64), default="")
    # pending | running | awaiting_approval | completed | failed | partial | rejected
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    llm_provider: Mapped[str] = mapped_column(String(50), default="mock")
    requested_by: Mapped[str] = mapped_column(String(100), default="system")
    state: Mapped[dict] = mapped_column(JSON, default=dict)  # serialisable shared state snapshot
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent_executions: Mapped[list["AgentExecution"]] = relationship(
        back_populates="workflow_execution", cascade="all, delete-orphan", order_by="AgentExecution.sequence"
    )


class AgentExecution(Base):
    __tablename__ = "agent_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workflow_execution_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_executions.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    agent_name: Mapped[str] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20))  # success | failed | skipped
    input_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    output_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    workflow_execution: Mapped[WorkflowExecution] = relationship(back_populates="agent_executions")


class InvestigationReport(Base):
    __tablename__ = "investigation_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workflow_execution_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_executions.id", ondelete="CASCADE"), index=True
    )
    record_number: Mapped[str] = mapped_column(String(50), index=True)
    workflow: Mapped[str] = mapped_column(String(20))
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workflow_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_executions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # incident_update | priority_change | assignment_change | change_approval |
    # remediation | incident_closure
    action_type: Mapped[str] = mapped_column(String(40))
    record_type: Mapped[str] = mapped_column(String(20), default="incident")
    record_number: Mapped[str] = mapped_column(String(50), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # exactly what will be written
    # pending | approved | rejected | executed | failed | expired
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_by: Mapped[str] = mapped_column(String(100), default="system")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    execution_result: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    actor: Mapped[str] = mapped_column(String(100), default="system")
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    workflow_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agent_name: Mapped[str] = mapped_column(String(100), default="")
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(500), default="")
    file_type: Mapped[str] = mapped_column(String(10), default="md")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    doc_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ServiceNowCallLog(Base):
    __tablename__ = "servicenow_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    method: Mapped[str] = mapped_column(String(10))
    table: Mapped[str] = mapped_column(String(64), default="")
    path: Mapped[str] = mapped_column(String(255), default="")
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(10), default="mock")
