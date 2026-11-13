"""Investigation service: runs the multi-agent workflow and persists everything."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.agents import AgentContext
from app.approvals import ApprovalService
from app.db import models as m
from app.db.base import session_scope
from app.db.repositories import AuditRepository, ReportRepository, WorkflowExecutionRepository
from app.knowledge.retriever import KnowledgeRetriever
from app.llm.manager import LLMManager
from app.schemas import InvestigationResult
from app.security.sanitize import mask_secrets
from app.servicenow.client import ServiceNowClient
from app.services.agent_configs import load_agent_configs
from app.workflows.graph import run_workflow
from app.workflows.state import initial_state

logger = logging.getLogger(__name__)


class DuplicateInvestigationError(Exception):
    """A run for the same record is already in progress."""

    def __init__(self, run_id: str):
        super().__init__(f"An investigation for this record is already in progress (run {run_id})")
        self.run_id = run_id


def _json_safe(value):
    return json.loads(json.dumps(value, default=str))


def build_result(state: dict, execution_id: str) -> InvestigationResult:
    record = state["record"]
    wf = state["workflow"]
    status = state.get("status", "completed")
    # Degraded runs keep their "partial"/"failed" status (so they can be resumed); only a clean
    # run with proposed actions waits for human approval.
    result_status = "awaiting_approval" if status == "completed" and state.get("proposed_actions") else status
    llm_calls = [h for h in state.get("history", []) if h["agent"] in ("classification", "rca")]
    return InvestigationResult(
        record_id=record["number"], workflow=wf, execution_id=execution_id,
        category=state.get("category", "general"),
        priority=state.get("priority") or record.get("priority", "3"),
        investigation_summary=f"{wf.title()} workflow analysis ({status}) for {record['short_description']}.",
        evidence=[f"{e['text']} [{e['source']}]" for e in state.get("evidence", [])],
        evidence_items=state.get("evidence", []),
        probable_root_cause=state.get("root_cause") or "Not determined",
        root_cause_status=state.get("root_cause_status", "insufficient_evidence"),
        confidence=float(state.get("confidence", 0.0)),
        recommendations=state.get("recommendations", []),
        status=result_status,
        agent_trace=state.get("trace", []),
        affected_service=state.get("affected_service", ""), business_impact=state.get("business_impact", ""),
        hypotheses=state.get("hypotheses", []), missing_information=state.get("missing_information", []),
        knowledge_sources=state.get("knowledge_results", []),
        knowledge_conflicts=state.get("knowledge_conflicts", []),
        similar_incidents=state.get("similar_incidents", []),
        corrective_actions=state.get("corrective_actions", []),
        preventive_actions=state.get("preventive_actions", []),
        change_assessment=state.get("change_assessment", {}),
        proposed_actions=state.get("proposed_actions", []),
        verification=state.get("verification", {}),
        security_flags=list(dict.fromkeys(state.get("security_flags", []))),
        errors=state.get("errors", []), skipped_agents=state.get("skipped", []),
        raw={"knowledge_results": state.get("knowledge_results", []),
             "verification": state.get("verification", {}),
             "stop_reason": state.get("stop_reason", ""),
             "steps": state.get("step_count", 0), "agent_calls": len(llm_calls)},
    )


class InvestigationService:
    def __init__(self, servicenow: ServiceNowClient | None = None, knowledge: KnowledgeRetriever | None = None,
                 approvals: ApprovalService | None = None):
        self.servicenow = servicenow or ServiceNowClient()
        self.knowledge = knowledge or KnowledgeRetriever()
        self.approvals = approvals or ApprovalService(self.servicenow)

    # ------------------------------------------------------------------ run
    def investigate(self, record_id: str, workflow: str = "incident", llm_provider: str | None = None,
                    requested_by: str = "system", diagnostics: dict | None = None,
                    force: bool = False) -> tuple[str, InvestigationResult]:
        """Run synchronously (start + run_job)."""
        run_id, job, existing = self.start(record_id, workflow, llm_provider, requested_by, diagnostics, force)
        if existing is not None:
            return run_id, existing
        return run_id, self.run_job(run_id, job)

    def run_job(self, run_id: str, job: dict) -> InvestigationResult:
        """Execute a job prepared by ``start`` (safe to call from a background thread)."""
        self._prime_knowledge(job["workflow"])
        return self._execute(run_id, job["state"], job["llm"], job["configs"], job["actor"])

    def recover_interrupted_runs(self) -> int:
        """Mark runs left 'running' by a crash/restart as failed so they can be resumed."""
        with session_scope() as session:
            rows = session.query(m.WorkflowExecution).filter(m.WorkflowExecution.status == "running").all()
            for row in rows:
                row.status, row.error = "failed", "Interrupted by service restart; resume to continue"
                AuditRepository(session).record("system", "workflow.interrupted", "workflow_execution", row.id)
            return len(rows)

    def start(self, record_id: str, workflow: str = "incident", llm_provider: str | None = None,
              requested_by: str = "system", diagnostics: dict | None = None, force: bool = False):
        """Create the execution row and prepare a job. Returns (run_id, job, existing_result).

        ``existing_result`` is set (and ``job`` is None) when an equivalent run is already awaiting
        approval, making duplicate submissions idempotent.
        """
        record = self.servicenow.get_record(workflow, record_id)
        llm = LLMManager(llm_provider)
        llm.ensure_usable()  # validates the provider before any state is created

        with session_scope() as session:
            repo = WorkflowExecutionRepository(session)
            existing = repo.find_active(workflow, record.number)
            if existing and not force:
                report = ReportRepository(session).latest_for_execution(existing.id)
                if report:
                    result = InvestigationResult(**report.report)
                    result.duplicate = True
                    return existing.id, None, result
                raise DuplicateInvestigationError(existing.id)
            execution = repo.add(m.WorkflowExecution(
                workflow=workflow, record_number=record.number, record_sys_id=record.sys_id, status="running",
                llm_provider=llm.provider, requested_by=requested_by))
            run_id = execution.id
            AuditRepository(session).record(requested_by, "workflow.started", "workflow_execution", run_id,
                                            workflow=workflow, record=record.number, provider=llm.provider)

        configs = load_agent_configs()
        disabled = [n for n, c in configs.items() if not c.get("enabled", True)]
        state = initial_state(_json_safe(record.model_dump()), workflow, diagnostics, disabled)
        return run_id, {"state": state, "llm": llm, "configs": configs, "actor": requested_by,
                        "workflow": workflow}, None

    def _prime_knowledge(self, workflow: str) -> None:
        """Index documents/incidents on the calling thread so parallel agents only read.

        Failures are logged, not raised: retrieval agents then degrade visibly (partial run).
        """
        try:
            self.knowledge.ensure_indexed()
            if workflow in ("incident", "problem") and hasattr(self.knowledge, "refresh_incidents"):
                self.knowledge.refresh_incidents(self.servicenow.list_records("incident"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Knowledge priming failed: %s", mask_secrets(f"{type(exc).__name__}: {exc}"))

    def resume(self, run_id: str, requested_by: str = "system") -> InvestigationResult:
        """Resume a failed/partial run: settled agents are kept, failed agents are retried."""
        with session_scope() as session:
            execution = session.get(m.WorkflowExecution, run_id)
            if execution is None:
                raise KeyError("Investigation run not found")
            if execution.status not in ("failed", "partial"):
                raise ValueError(f"Run in status '{execution.status}' cannot be resumed")
            state = dict(execution.state or {})
            provider = execution.llm_provider
            execution.status = "running"
            execution.error = None
            AuditRepository(session).record(requested_by, "workflow.resumed", "workflow_execution", run_id)
        if not state.get("record"):
            raise ValueError("No persisted state available to resume from")
        state.update({"failed": [], "errors": [], "status": "running", "stop_reason": "", "next": [],
                      "step_count": 0})
        configs = load_agent_configs()
        self._prime_knowledge(state.get("workflow", "incident"))
        return self._execute(run_id, state, LLMManager(provider), configs, requested_by)

    def _execute(self, run_id: str, state: dict, llm: LLMManager, configs: dict, actor: str) -> InvestigationResult:
        ctx = AgentContext(llm=llm, knowledge=self.knowledge, servicenow=self.servicenow.read_only(),
                           execution_id=run_id, agent_configs=configs)
        persisted = {"history": len(state.get("history", []))}

        def on_step(snapshot: dict) -> None:
            try:
                self._persist_step(run_id, snapshot, persisted)
            except Exception as exc:  # noqa: BLE001 - persistence must not break the run
                logger.error("Could not persist workflow step: %s", mask_secrets(str(exc)))

        try:
            final = run_workflow(ctx, state, on_step)
        except Exception as exc:  # noqa: BLE001
            message = mask_secrets(f"{type(exc).__name__}: {exc}")[:500]
            with session_scope() as session:
                ex = session.get(m.WorkflowExecution, run_id)
                ex.status, ex.error, ex.finished_at = "failed", message, datetime.now(timezone.utc)
                AuditRepository(session).record(actor, "workflow.failed", "workflow_execution", run_id, error=message)
            raise

        result = build_result(final, run_id)
        if result.status == "awaiting_approval":
            # Agents only propose; each proposal becomes a pending human approval request.
            result.proposed_actions = self.approvals.request_for_actions(result.proposed_actions, run_id, actor)
            if not any(a.get("approval_id") for a in result.proposed_actions):
                result.status = "completed"
        with session_scope() as session:
            ex = session.get(m.WorkflowExecution, run_id)
            ex.status = result.status if result.status != "completed" else "completed"
            ex.state = _json_safe(final)
            ex.step_count = final.get("step_count", 0)
            ex.finished_at = datetime.now(timezone.utc)
            ex.error = "; ".join(final.get("errors", []))[:1000] or None
            ReportRepository(session).add(m.InvestigationReport(
                workflow_execution_id=run_id, record_number=result.record_id, workflow=result.workflow,
                report=_json_safe(result.model_dump())))
            AuditRepository(session).record(actor, "workflow.finished", "workflow_execution", run_id,
                                            status=ex.status, steps=ex.step_count)
        return result

    def _persist_step(self, run_id: str, snapshot: dict, persisted: dict) -> None:
        history = snapshot.get("history", [])
        with session_scope() as session:
            repo = WorkflowExecutionRepository(session)
            for index in range(persisted["history"], len(history)):
                h = history[index]
                repo.add_agent_execution(
                    run_id, sequence=index + 1, agent_name=h["agent"], attempt=h.get("attempt", 1),
                    status=h["status"], input_summary=h.get("input", {}), output_summary=h.get("output", {}),
                    error=h.get("error"), duration_ms=h.get("duration_ms", 0.0),
                    started_at=datetime.fromisoformat(h["started_at"]))
            persisted["history"] = len(history)
            ex = session.get(m.WorkflowExecution, run_id)
            ex.state = _json_safe(snapshot)  # snapshot enables crash recovery
            ex.step_count = snapshot.get("step_count", 0)

    # ----------------------------------------------------------------- query
    def get_result(self, run_id: str) -> InvestigationResult:
        """The stored report, overlaid with the live workflow and approval statuses."""
        with session_scope() as session:
            report = ReportRepository(session).latest_for_execution(run_id)
            if report is None:
                raise KeyError("Investigation run not found")
            result = InvestigationResult(**report.report)
            execution = session.get(m.WorkflowExecution, run_id)
            if execution is not None:
                result.status = execution.status
            live = {a.id: a.status for a in session.query(m.ApprovalRequest).filter_by(workflow_execution_id=run_id)}
            for action in result.proposed_actions:
                if action.get("approval_id") in live:
                    action["status"] = "pending_approval" if live[action["approval_id"]] == "pending" \
                        else live[action["approval_id"]]
            return result
