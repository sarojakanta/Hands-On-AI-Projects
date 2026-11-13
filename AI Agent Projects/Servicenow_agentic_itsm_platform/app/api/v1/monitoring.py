from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import Services, get_services, require_role
from app.api.schemas import Page
from app.api.v1.agents import agent_stats
from app.api.v1.investigations import execution_dict
from app.config import settings
from app.db import models as m
from app.db.base import session_scope
from app.db.repositories import AuditRepository, LLMUsageRepository, WorkflowExecutionRepository
from app.llm.manager import provider_status
from app.security.rbac import Principal

router = APIRouter(tags=["monitoring"])


@router.get("/dashboard/summary", summary="Executive dashboard metrics")
def summary(svc: Services = Depends(get_services), _: Principal = Depends(require_role("viewer"))):
    tickets: dict[str, int | None] = {}
    for workflow in ("incident", "problem", "change", "request"):
        try:
            items, total = svc.servicenow.search_records(workflow, "", 1, 0)
            tickets[workflow] = total if total is not None else len(items)
        except Exception:  # noqa: BLE001 - dashboard must render even if ServiceNow is down
            tickets[workflow] = None
    with session_scope() as s:
        repo = WorkflowExecutionRepository(s)
        counts = repo.status_counts()
        recent = [execution_dict(r) for r in repo.list(limit=10)]
        problems = [execution_dict(r) for r in s.scalars(
            select(m.WorkflowExecution).where(m.WorkflowExecution.status.in_(["failed", "partial"]))
            .order_by(m.WorkflowExecution.started_at.desc()).limit(10))]
        durations = [(r.finished_at - r.started_at).total_seconds() for r in s.scalars(
            select(m.WorkflowExecution).where(m.WorkflowExecution.finished_at.is_not(None)).limit(200))]
        pending = s.scalar(select(func.count()).select_from(m.ApprovalRequest)
                           .where(m.ApprovalRequest.status == "pending")) or 0
        usage = LLMUsageRepository(s).summary()
    total = sum(counts.values())
    done = counts.get("completed", 0) + counts.get("awaiting_approval", 0)
    return {
        "tickets": tickets, "executions": {"total": total, "by_status": counts,
                                           "success_rate": round(done / total, 3) if total else None,
                                           "avg_duration_seconds": round(sum(durations) / len(durations), 2)
                                           if durations else None},
        "pending_approvals": int(pending), "llm": usage,
        "agents": {name: {"runs": st["runs"], "failed": st["failed"], "avg_duration_ms": st["avg_duration_ms"]}
                   for name, st in agent_stats().items()},
        "recent_executions": recent, "recent_problems": problems,
        "servicenow_mode": svc.servicenow.mode,
    }


@router.get("/audit", response_model=Page[dict], summary="Audit log (approver)")
def audit(action: str | None = None, entity_id: str | None = None, actor: str | None = None,
          limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
          _: Principal = Depends(require_role("approver"))):
    with session_scope() as s:
        repo = AuditRepository(s)
        rows = repo.list(limit=limit, offset=offset, action=action, entity_id=entity_id, actor=actor)
        total = repo.count(action=action, entity_id=entity_id, actor=actor)
        return Page(items=[{"id": r.id, "timestamp": r.timestamp.isoformat(), "actor": r.actor, "action": r.action,
                            "entity_type": r.entity_type, "entity_id": r.entity_id, "details": r.details}
                           for r in rows], total=total, limit=limit, offset=offset)


@router.get("/servicenow/calls", summary="ServiceNow API call log (approver)")
def servicenow_calls(limit: int = Query(50, ge=1, le=500), only_failures: bool = False,
                     _: Principal = Depends(require_role("approver"))):
    with session_scope() as s:
        stmt = select(m.ServiceNowCallLog).order_by(m.ServiceNowCallLog.timestamp.desc()).limit(limit)
        if only_failures:
            stmt = stmt.where(m.ServiceNowCallLog.success.is_(False))
        rows = s.scalars(stmt).all()
        total = s.scalar(select(func.count()).select_from(m.ServiceNowCallLog)) or 0
        failed = s.scalar(select(func.count()).select_from(m.ServiceNowCallLog)
                          .where(m.ServiceNowCallLog.success.is_(False))) or 0
        return {"total_calls": int(total), "failed_calls": int(failed),
                "items": [{"timestamp": r.timestamp.isoformat(), "method": r.method, "table": r.table,
                           "status_code": r.status_code, "duration_ms": r.duration_ms, "attempts": r.attempts,
                           "success": r.success, "error": r.error, "mode": r.mode} for r in rows]}


@router.get("/system/config", summary="Effective configuration with all secrets masked (admin)")
def system_config(_: Principal = Depends(require_role("admin"))):
    return settings.safe_dict()


@router.get("/system/status", summary="Component status")
def system_status(svc: Services = Depends(get_services), _: Principal = Depends(require_role("viewer"))):
    components: dict[str, dict] = {}
    try:
        with session_scope() as s:
            s.execute(select(1))
        components["database"] = {"ok": True, "backend": settings.database_url.split(":", 1)[0].split("+")[0]}
    except Exception as exc:  # noqa: BLE001
        components["database"] = {"ok": False, "error": type(exc).__name__}
    try:
        components["knowledge"] = {"ok": True, **svc.knowledge.stats(), "embedding_provider": settings.embedding_provider}
    except Exception as exc:  # noqa: BLE001
        components["knowledge"] = {"ok": False, "error": type(exc).__name__}
    components["servicenow"] = {"ok": True, "mode": svc.servicenow.mode, "auth": settings.servicenow_auth,
                                "writes_enabled": svc.servicenow.mock or settings.servicenow_write_enabled}
    components["llm"] = {"ok": True, "default_provider": settings.llm_provider, "providers": provider_status()}
    return {"environment": settings.app_env, "components": components}
