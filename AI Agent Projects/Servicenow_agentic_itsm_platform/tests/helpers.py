"""Shared test doubles. No network, no credentials."""
from __future__ import annotations

from app.agents import AgentContext
from app.llm.manager import LLMManager
from app.schemas import ITSMRecord
from app.servicenow.client import ServiceNowClient
from app.workflows.state import initial_state


def make_record(**kw) -> ITSMRecord:
    base = dict(sys_id="t-1", number="INC9000001", short_description="Application server high CPU",
                description="CPU above 95% since 10:30 AM. Multiple users report slowness and error 500.",
                priority="3", state="New", category="software", impact="3", urgency="3",
                assignment_group="App Support", record_type="incident", source="mock")
    base.update(kw)
    return ITSMRecord(**base)


class FakeKnowledge:
    def __init__(self, hits=None):
        self.hits = hits or []
        self.queries = []

    def search(self, query, limit=3, **kw):
        self.queries.append(query)
        return self.hits[:limit]


class FailingServiceNow:
    """Simulates an unreachable ServiceNow instance."""

    def list_records(self, record_type):
        raise TimeoutError("ServiceNow timed out")

    def get_record(self, record_type, record_id):
        raise TimeoutError("ServiceNow timed out")


def make_ctx(llm_provider="mock", knowledge=None, servicenow=None, force_llm=False, **kw) -> AgentContext:
    return AgentContext(
        llm=LLMManager(llm_provider, usage_sink=None, sleep=lambda s: None),
        knowledge=knowledge or FakeKnowledge(),
        servicenow=servicenow or ServiceNowClient().read_only(),
        force_llm=force_llm, sleep=lambda s: None, **kw)


def make_state(record: ITSMRecord | None = None, workflow: str | None = None, **extra):
    record = record or make_record()
    state = initial_state(record.model_dump(), workflow or record.record_type)
    state.update(extra)
    return state


REDUCER_KEYS = {"history", "trace", "errors", "failed", "skipped", "rerun", "handoffs", "supervisor_log"}


def apply(state: dict, update: dict) -> dict:
    """Merge an agent update the way LangGraph would."""
    out = dict(state)
    for key, value in update.items():
        out[key] = [*out.get(key, []), *value] if key in REDUCER_KEYS else value
    return out


def kb_hit(title="high_cpu_troubleshooting", excerpt="High CPU: check top processes and recent deployments.",
           score=3, **meta):
    return {"title": title, "source": title, "excerpt": excerpt, "score": score, "metadata": meta}
