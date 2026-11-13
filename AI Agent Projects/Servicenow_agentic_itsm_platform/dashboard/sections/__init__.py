from functools import partial

from dashboard.sections import agents, approvals, audit, executive, history, knowledge, llm, monitor, records, system

# (label, renderer(api, me)) in navigation order: the 13 required sections.
SECTIONS = [
    ("Executive Dashboard", executive.render),
    ("Incident Management", partial(records.render, workflow="incident")),
    ("Problem Management", partial(records.render, workflow="problem")),
    ("Change Management", partial(records.render, workflow="change")),
    ("Service Requests", partial(records.render, workflow="request")),
    ("Agent Management", agents.render),
    ("Workflow Execution Monitor", monitor.render),
    ("Knowledge Base", knowledge.render),
    ("Human Approval Center", approvals.render),
    ("LLM Provider Configuration", llm.render),
    ("Execution History", history.render),
    ("Audit Logs", audit.render),
    ("System Configuration", system.render),
]
