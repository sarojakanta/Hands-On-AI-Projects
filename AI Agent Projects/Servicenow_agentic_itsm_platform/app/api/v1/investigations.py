import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy import func, select

from app.api.deps import Services, get_services, require_role
from app.api.schemas import InvestigationCreate, Page
from app.db import models as m
from app.db.base import session_scope
from app.schemas import InvestigationResult
from app.security.rbac import Principal
from app.security.sanitize import mask_secrets
from app.services.reports import render_markdown

router = APIRouter(prefix="/investigations", tags=["investigations"])
logger = logging.getLogger(__name__)


def execution_dict(ex: m.WorkflowExecution) -> dict:
    duration = None
    if ex.finished_at and ex.started_at:
        a, b = ex.started_at, ex.finished_at
        duration = round((b - a).total_seconds(), 2)
    return {"id": ex.id, "workflow": ex.workflow, "record_number": ex.record_number, "status": ex.status,
            "llm_provider": ex.llm_provider, "requested_by": ex.requested_by, "step_count": ex.step_count,
            "error": ex.error, "started_at": ex.started_at.isoformat() if ex.started_at else None,
            "finished_at": ex.finished_at.isoformat() if ex.finished_at else None, "duration_seconds": duration}


def _background(svc: Services, run_id: str, job: dict) -> None:
    try:
        svc.investigations.run_job(run_id, job)
    except Exception as exc:  # noqa: BLE001 - status/error already persisted by the service
        logger.error("Background investigation %s failed: %s", run_id, mask_secrets(str(exc)))


@router.post("", status_code=201, summary="Start an investigation",
             description="Runs the multi-agent workflow. With `wait=false` it returns 202 immediately and the run "
                         "can be followed via the timeline endpoint. Re-submitting a record that is awaiting "
                         "approval returns the existing run (HTTP 200) unless `force` is set.")
def create_investigation(body: InvestigationCreate, response: Response, background: BackgroundTasks,
                         wait: bool = Query(True), svc: Services = Depends(get_services),
                         principal: Principal = Depends(require_role("analyst"))):
    run_id, job, existing = svc.investigations.start(
        body.record_id, body.workflow, body.llm_provider, principal.username, body.diagnostics or None, body.force)
    if existing is not None:
        response.status_code = 200
        return {"run_id": run_id, "status": existing.status, "result": existing}
    if not wait:
        background.add_task(_background, svc, run_id, job)
        response.status_code = 202
        return {"run_id": run_id, "status": "running", "result": None}
    result = svc.investigations.run_job(run_id, job)
    return {"run_id": run_id, "status": result.status, "result": result}


@router.get("", response_model=Page[dict], summary="List workflow executions")
def list_investigations(status: str | None = None, workflow: str | None = None, record_number: str | None = None,
                        limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
                        _: Principal = Depends(require_role("viewer"))):
    with session_scope() as s:
        filters = [getattr(m.WorkflowExecution, k) == v for k, v in
                   (("status", status), ("workflow", workflow), ("record_number", record_number)) if v]
        rows = s.scalars(select(m.WorkflowExecution).where(*filters).order_by(m.WorkflowExecution.started_at.desc())
                         .limit(limit).offset(offset)).all()
        total = s.scalar(select(func.count()).select_from(m.WorkflowExecution).where(*filters))
        return Page(items=[execution_dict(r) for r in rows], total=int(total or 0), limit=limit, offset=offset)


@router.get("/{run_id}", summary="Execution status and result")
def get_investigation(run_id: str, svc: Services = Depends(get_services),
                      _: Principal = Depends(require_role("viewer"))):
    with session_scope() as s:
        ex = s.get(m.WorkflowExecution, run_id)
        if ex is None:
            raise KeyError("Investigation run not found")
        execution = execution_dict(ex)
    try:
        result: InvestigationResult | None = svc.investigations.get_result(run_id)
    except KeyError:
        result = None  # still running (or failed before a report existed)
    return {"execution": execution, "result": result}


@router.get("/{run_id}/timeline", summary="Per-agent execution timeline, handoffs and supervisor decisions")
def timeline(run_id: str, _: Principal = Depends(require_role("viewer"))):
    with session_scope() as s:
        ex = s.get(m.WorkflowExecution, run_id)
        if ex is None:
            raise KeyError("Investigation run not found")
        state = ex.state or {}
        steps = [{"sequence": a.sequence, "agent": a.agent_name, "attempt": a.attempt, "status": a.status,
                  "input": a.input_summary, "output": a.output_summary, "error": a.error,
                  "duration_ms": a.duration_ms, "started_at": a.started_at.isoformat()}
                 for a in ex.agent_executions]
        return {"execution": execution_dict(ex), "steps": steps, "handoffs": state.get("handoffs", []),
                "supervisor_log": state.get("supervisor_log", []), "skipped": state.get("skipped", []),
                "reruns": state.get("rerun", []), "errors": state.get("errors", []),
                "stop_reason": state.get("stop_reason", "")}


@router.post("/{run_id}/resume", summary="Resume a failed or partial run")
def resume(run_id: str, svc: Services = Depends(get_services), principal: Principal = Depends(require_role("analyst"))):
    result = svc.investigations.resume(run_id, principal.username)
    return {"run_id": run_id, "status": result.status, "result": result}


@router.get("/{run_id}/report", summary="Download the investigation report (md or json)")
def report(run_id: str, format: str = Query("md", pattern="^(md|json)$"), svc: Services = Depends(get_services),
           _: Principal = Depends(require_role("viewer"))):
    result = svc.investigations.get_result(run_id)
    with session_scope() as s:
        ex = execution_dict(s.get(m.WorkflowExecution, run_id))
    if format == "json":
        content, media, ext = json.dumps(result.model_dump(), indent=2, default=str), "application/json", "json"
    else:
        content, media, ext = render_markdown(result, ex), "text/markdown", "md"
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{result.record_id}-report.{ext}"'})
