from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.agents import agent_catalog
from app.api.deps import require_role
from app.api.errors import ApiError
from app.api.schemas import AgentUpdate
from app.db import models as m
from app.db.base import session_scope
from app.db.repositories import AuditRepository
from app.security.rbac import Principal
from app.services.agent_configs import load_agent_configs

router = APIRouter(prefix="/agents", tags=["agents"])


def agent_stats() -> dict[str, dict]:
    with session_scope() as s:
        rows = s.execute(select(m.AgentExecution.agent_name, m.AgentExecution.status, func.count(),
                                func.avg(m.AgentExecution.duration_ms), func.max(m.AgentExecution.started_at))
                         .group_by(m.AgentExecution.agent_name, m.AgentExecution.status)).all()
    stats: dict[str, dict] = {}
    for name, status, count, avg_ms, last in rows:
        st = stats.setdefault(name, {"runs": 0, "success": 0, "failed": 0, "skipped": 0, "avg_duration_ms": 0.0,
                                     "last_run": None, "_ms": 0.0, "_n": 0})
        st[status] = st.get(status, 0) + int(count)
        if status != "skipped":
            st["_ms"] += float(avg_ms or 0) * int(count)
            st["_n"] += int(count)
        if last and (st["last_run"] is None or last.isoformat() > st["last_run"]):
            st["last_run"] = last.isoformat()
    for st in stats.values():
        st["runs"] = st["success"] + st["failed"]
        st["avg_duration_ms"] = round(st.pop("_ms") / st["_n"], 2) if st["_n"] else 0.0
        st.pop("_n")
    return stats


@router.get("", summary="Agents, their configuration and run statistics")
def list_agents(_: Principal = Depends(require_role("viewer"))):
    configs, stats = load_agent_configs(), agent_stats()
    empty = {"runs": 0, "success": 0, "failed": 0, "skipped": 0, "avg_duration_ms": 0.0, "last_run": None}
    out = []
    for item in agent_catalog():
        cfg = configs.get(item["name"], {})
        out.append({**item, "enabled": cfg.get("enabled", True), "max_retries": cfg.get("max_retries"),
                    "timeout_seconds": cfg.get("timeout_seconds"), "llm_provider": cfg.get("llm_provider"),
                    "llm_model": cfg.get("llm_model"), "stats": stats.get(item["name"], empty)})
    return {"items": out}


@router.patch("/{name}", summary="Update an agent's configuration (admin)")
def update_agent(name: str, body: AgentUpdate, admin: Principal = Depends(require_role("admin"))):
    catalog = {a["name"]: a for a in agent_catalog()}
    if name not in catalog:
        raise KeyError(f"Unknown agent: {name}")
    if name == "supervisor" and body.enabled is False:
        raise ApiError(400, "agent_required", "The supervisor can not be disabled")
    if catalog[name]["critical"] and body.enabled is False:
        raise ApiError(400, "agent_required", f"'{name}' is critical and can not be disabled")
    load_agent_configs()
    with session_scope() as s:
        cfg = s.get(m.AgentConfig, name)
        changes = {}
        for field in ("enabled", "max_retries", "timeout_seconds", "llm_provider", "llm_model"):
            value = getattr(body, field)
            if value is not None:
                setattr(cfg, field, value)
                changes[field] = value
        if body.clear_llm_override:
            cfg.llm_provider = cfg.llm_model = None
            changes["llm_override"] = "cleared"
        AuditRepository(s).record(admin.username, "agent.configured", "agent", name, **changes)
    return next(a for a in list_agents()["items"] if a["name"] == name)
