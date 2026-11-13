"""Repository layer: all persistence access goes through these classes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models as m

T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Repository(Generic[T]):
    model: type[T]

    def __init__(self, session: Session):
        self.session = session

    def add(self, obj: T) -> T:
        self.session.add(obj)
        self.session.flush()
        return obj

    def get(self, id_: Any) -> T | None:
        return self.session.get(self.model, id_)

    def list(self, limit: int = 50, offset: int = 0, **filters) -> list[T]:
        stmt = select(self.model)
        for key, value in filters.items():
            if value is not None:
                stmt = stmt.where(getattr(self.model, key) == value)
        order = getattr(self.model, "started_at", None) or getattr(self.model, "timestamp", None)
        if order is None:
            order = getattr(self.model, "requested_at", None) or getattr(self.model, "created_at", None)
        if order is not None:
            stmt = stmt.order_by(order.desc())
        return list(self.session.scalars(stmt.limit(limit).offset(offset)))

    def count(self, **filters) -> int:
        stmt = select(func.count()).select_from(self.model)
        for key, value in filters.items():
            if value is not None:
                stmt = stmt.where(getattr(self.model, key) == value)
        return int(self.session.scalar(stmt) or 0)


class UserRepository(Repository[m.User]):
    model = m.User

    def by_username(self, username: str) -> m.User | None:
        return self.session.scalar(select(m.User).where(m.User.username == username))


class AgentConfigRepository(Repository[m.AgentConfig]):
    model = m.AgentConfig

    def upsert(self, name: str, **fields) -> m.AgentConfig:
        row = self.get(name)
        if row is None:
            row = self.add(m.AgentConfig(name=name, **fields))
        else:
            for k, v in fields.items():
                setattr(row, k, v)
            self.session.flush()
        return row


class WorkflowExecutionRepository(Repository[m.WorkflowExecution]):
    model = m.WorkflowExecution

    def find_active(self, workflow: str, record_number: str) -> m.WorkflowExecution | None:
        """Used for duplicate-processing protection."""
        stmt = (
            select(m.WorkflowExecution)
            .where(
                m.WorkflowExecution.workflow == workflow,
                m.WorkflowExecution.record_number == record_number,
                m.WorkflowExecution.status.in_(["pending", "running", "awaiting_approval"]),
            )
            .order_by(m.WorkflowExecution.started_at.desc())
        )
        return self.session.scalars(stmt).first()

    def add_agent_execution(self, execution_id: str, **fields) -> m.AgentExecution:
        row = m.AgentExecution(workflow_execution_id=execution_id, **fields)
        self.session.add(row)
        self.session.flush()
        return row

    def status_counts(self) -> dict[str, int]:
        rows = self.session.execute(
            select(m.WorkflowExecution.status, func.count()).group_by(m.WorkflowExecution.status)
        ).all()
        return {status: int(count) for status, count in rows}


class ReportRepository(Repository[m.InvestigationReport]):
    model = m.InvestigationReport

    def latest_for_execution(self, execution_id: str) -> m.InvestigationReport | None:
        stmt = (
            select(m.InvestigationReport)
            .where(m.InvestigationReport.workflow_execution_id == execution_id)
            .order_by(m.InvestigationReport.created_at.desc())
        )
        return self.session.scalars(stmt).first()


class ApprovalRepository(Repository[m.ApprovalRequest]):
    model = m.ApprovalRequest

    def pending(self, limit: int = 100) -> list[m.ApprovalRequest]:
        return self.list(limit=limit, status="pending")


class AuditRepository(Repository[m.AuditEvent]):
    model = m.AuditEvent

    def record(self, actor: str, action: str, entity_type: str = "", entity_id: str = "", **details) -> m.AuditEvent:
        return self.add(
            m.AuditEvent(actor=actor, action=action, entity_type=entity_type, entity_id=str(entity_id), details=details)
        )

    def search(self, action: str | None = None, entity_id: str | None = None, limit: int = 100, offset: int = 0):
        return self.list(limit=limit, offset=offset, action=action, entity_id=entity_id)


class LLMUsageRepository(Repository[m.LLMUsage]):
    model = m.LLMUsage

    def summary(self) -> dict:
        row = self.session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(m.LLMUsage.prompt_tokens), 0),
                func.coalesce(func.sum(m.LLMUsage.completion_tokens), 0),
                func.coalesce(func.sum(m.LLMUsage.estimated_cost), 0.0),
                func.coalesce(func.avg(m.LLMUsage.latency_ms), 0.0),
            )
        ).one()
        failures = self.session.scalar(select(func.count()).select_from(m.LLMUsage).where(m.LLMUsage.success.is_(False)))
        return {
            "calls": int(row[0]),
            "prompt_tokens": int(row[1]),
            "completion_tokens": int(row[2]),
            "estimated_cost": float(row[3]),
            "avg_latency_ms": float(row[4]),
            "failures": int(failures or 0),
        }


class KnowledgeDocumentRepository(Repository[m.KnowledgeDocument]):
    model = m.KnowledgeDocument

    def by_source(self, source: str) -> m.KnowledgeDocument | None:
        return self.session.scalar(select(m.KnowledgeDocument).where(m.KnowledgeDocument.source == source))


class ServiceNowCallLogRepository(Repository[m.ServiceNowCallLog]):
    model = m.ServiceNowCallLog
