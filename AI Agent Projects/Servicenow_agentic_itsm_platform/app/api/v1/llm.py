import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import require_role
from app.api.schemas import LLMTest
from app.config import settings
from app.db import models as m
from app.db.base import session_scope
from app.db.repositories import LLMUsageRepository
from app.llm.manager import LLMError, LLMManager, provider_status
from app.security.rbac import Principal
from app.security.sanitize import mask_secrets

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/providers", summary="Configured LLM providers (secrets never included)")
def providers(_: Principal = Depends(require_role("viewer"))):
    return {"default_provider": settings.llm_provider,
            "fallback_providers": [p.strip() for p in settings.llm_fallback_providers.split(",") if p.strip()],
            "timeout_seconds": settings.llm_timeout, "max_retries": settings.llm_max_retries,
            "items": provider_status()}


@router.get("/usage", summary="Token, latency, cost and failure metrics")
def usage(limit: int = Query(50, ge=1, le=500), _: Principal = Depends(require_role("viewer"))):
    with session_scope() as s:
        repo = LLMUsageRepository(s)
        by_model = s.execute(select(
            m.LLMUsage.provider, m.LLMUsage.model, func.count(), func.sum(m.LLMUsage.prompt_tokens),
            func.sum(m.LLMUsage.completion_tokens), func.avg(m.LLMUsage.latency_ms),
            func.sum(m.LLMUsage.estimated_cost)).group_by(m.LLMUsage.provider, m.LLMUsage.model)).all()
        by_agent = s.execute(select(m.LLMUsage.agent_name, func.count(), func.sum(m.LLMUsage.prompt_tokens
                                    + m.LLMUsage.completion_tokens)).group_by(m.LLMUsage.agent_name)).all()
        recent = [{"timestamp": r.timestamp.isoformat(), "agent": r.agent_name, "provider": r.provider,
                   "model": r.model, "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                   "latency_ms": round(r.latency_ms, 1), "cost": r.estimated_cost, "success": r.success,
                   "error": r.error} for r in repo.list(limit=limit)]
        return {"summary": repo.summary(),
                "by_model": [{"provider": p, "model": mo, "calls": int(c), "prompt_tokens": int(pt or 0),
                              "completion_tokens": int(ct or 0), "avg_latency_ms": round(float(al or 0), 1),
                              "estimated_cost": float(cost or 0)} for p, mo, c, pt, ct, al, cost in by_model],
                "by_agent": [{"agent": a or "(unattributed)", "calls": int(c), "tokens": int(t or 0)}
                             for a, c, t in by_agent],
                "recent": recent}


@router.post("/test", summary="Connectivity check for a provider (admin)")
def test_provider(body: LLMTest, _: Principal = Depends(require_role("admin"))):
    started = time.perf_counter()
    try:
        manager = LLMManager(body.provider, body.model, fallbacks=[], usage_sink=None)
        manager.complete("You are a health check.", "Reply with the word OK.", agent="llm_test")
        ok, error = True, None
    except LLMError as exc:
        ok, error = False, mask_secrets(str(exc))
    return {"provider": body.provider, "ok": ok, "error": error,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
