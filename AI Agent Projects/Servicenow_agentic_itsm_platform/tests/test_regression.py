"""Golden-output regression tests: the mock dataset must keep producing these outcomes."""
import pytest

from app.services.investigation import InvestigationService

CASES = [
    # record, workflow, category, priority, top knowledge source, root_cause_status, proposed action types, executed agents
    ("INC0010025", "incident", "software", "1", "high_cpu_troubleshooting.md", "unverified_hypothesis",
     {"incident_update"}, {"classification", "priority", "investigation", "knowledge", "rca", "resolution", "verification"}),
    ("INC0010026", "incident", "software", "2", "http_503_troubleshooting.md", "unverified_hypothesis",
     {"incident_update"}, {"classification", "priority", "investigation", "knowledge", "rca", "resolution", "verification"}),
    # No KB assertion for the problem: its wording ("availability") shares no vocabulary with the 503 article, and
    # the offline lexical embedder can not bridge that gap. A semantic embedder would; see README "RAG".
    ("PRB0001001", "problem", "software", "2", None, "unverified_hypothesis",
     {"incident_update"}, {"classification", "investigation", "knowledge", "rca", "resolution", "verification"}),
    ("CHG0002001", "change", "software", "3", None, "not_applicable",
     {"incident_update"}, {"classification", "knowledge", "change_management", "resolution", "verification"}),
    ("REQ0013001", "request", "hardware", "3", None, "not_applicable",
     {"incident_update"}, {"classification", "knowledge", "resolution", "verification"}),
]


@pytest.mark.parametrize("record,workflow,category,priority,kb,status,actions,agents", CASES)
def test_golden_outcomes(record, workflow, category, priority, kb, status, actions, agents):
    svc = InvestigationService()
    run_id, result = svc.investigate(record, workflow, "mock")
    assert (result.category, result.priority, result.root_cause_status) == (category, priority, status)
    if kb:
        assert result.knowledge_sources[0]["source"] == kb
    assert {a["action_type"] for a in result.proposed_actions} == actions
    assert result.status == "awaiting_approval" and result.errors == []
    assert "Probable cause (unverified hypothesis)" in result.probable_root_cause or status == "not_applicable"
    from app.api.v1.investigations import timeline  # noqa: F401  (route module imports cleanly)
    from app.db import models as m
    from app.db.base import session_scope

    with session_scope() as s:
        ran = {a.agent_name for a in s.get(m.WorkflowExecution, run_id).agent_executions
               if a.status == "success" and a.agent_name != "supervisor"}
    assert ran == agents


def test_cpu_incident_confidence_stays_in_the_expected_band():
    _, result = InvestigationService().investigate("INC0010025", "incident", "mock")
    assert 0.6 <= result.confidence <= 0.85                      # never presented as certain
    assert result.hypotheses[0]["status"] == "unverified"
    assert result.verification["closure_allowed"] is False


def test_every_result_keeps_the_original_response_fields():
    """The v0.2 response contract (fields the old dashboard/clients relied on) must remain."""
    _, result = InvestigationService().investigate("INC0010026", "incident", "mock")
    payload = result.model_dump()
    for field in ("record_id", "workflow", "category", "priority", "investigation_summary", "evidence",
                  "probable_root_cause", "confidence", "recommendations", "requires_human_approval", "status",
                  "agent_trace", "raw"):
        assert field in payload
    assert payload["requires_human_approval"] is True and len(payload["agent_trace"]) >= 6
