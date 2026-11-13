from app.agents.agents import (
    ChangeManagementAgent,
    ClassificationAgent,
    InvestigationAgent,
    KnowledgeAgent,
    PriorityAgent,
    RCAAgent,
    ResolutionAgent,
    VerificationAgent,
)
from app.agents.base import AgentContext, BaseAgent, run_agent
from app.agents.supervisor import Supervisor

AGENT_CLASSES: dict[str, type[BaseAgent]] = {
    cls.name: cls
    for cls in (ClassificationAgent, PriorityAgent, InvestigationAgent, KnowledgeAgent, RCAAgent,
                ResolutionAgent, ChangeManagementAgent, VerificationAgent)
}


def agent_catalog() -> list[dict]:
    """Descriptions of all agents including the supervisor (for dashboards and seeding)."""
    items = [{"name": Supervisor.name, "description": Supervisor.description, "critical": False}]
    items += [{"name": n, "description": c.description, "critical": c.critical} for n, c in AGENT_CLASSES.items()]
    return items


__all__ = ["AGENT_CLASSES", "AgentContext", "BaseAgent", "Supervisor", "agent_catalog", "run_agent"]
