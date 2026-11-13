"""Downloadable investigation reports (Markdown)."""
from __future__ import annotations

from app.schemas import InvestigationResult
from app.security.sanitize import mask_secrets


def _bullets(items, empty="None") -> str:
    return "\n".join(f"- {mask_secrets(str(i))}" for i in items) if items else f"_{empty}_"


def render_markdown(result: InvestigationResult, execution: dict | None = None) -> str:
    ex = execution or {}
    lines = [
        f"# Investigation report: {result.record_id}",
        "",
        f"- **Workflow:** {result.workflow}",
        f"- **Status:** {result.status}",
        f"- **Run ID:** {result.execution_id}",
        f"- **Category:** {result.category}  |  **Priority (recommended):** P{result.priority}",
        f"- **Affected service:** {result.affected_service or 'n/a'}",
        f"- **Business impact:** {result.business_impact or 'n/a'}",
    ]
    if ex:
        lines += [f"- **Requested by:** {ex.get('requested_by', 'n/a')}  |  **LLM provider:** {ex.get('llm_provider', 'n/a')}",
                  f"- **Started:** {ex.get('started_at', 'n/a')}  |  **Finished:** {ex.get('finished_at') or 'n/a'}"]
    lines += ["", "## Assessment",
              f"{mask_secrets(result.probable_root_cause)}",
              "", f"_Status: **{result.root_cause_status}**. Hypotheses are not confirmed causes. "
              f"Confidence: {result.confidence:.0%}._", "", "## Hypotheses"]
    if result.hypotheses:
        for h in result.hypotheses:
            lines.append(f"### {mask_secrets(h['cause'])} ({h['confidence']:.0%}, {h['status']})")
            lines.append(_bullets(h.get("evidence", []), "No evidence cited"))
    else:
        lines.append("_No hypothesis could be formed from the available evidence._")
    lines += ["", "## Missing information", _bullets(result.missing_information),
              "", "## Evidence", _bullets(result.evidence),
              "", "## Knowledge sources (cited excerpts)"]
    if result.knowledge_sources:
        for k in result.knowledge_sources:
            lines.append(f"- **{k.get('citation') or k['source']}** (score {k.get('score', 0)}): "
                         f"{mask_secrets(k['excerpt'][:240])!r}")
    else:
        lines.append("_No relevant knowledge found._")
    if result.knowledge_conflicts:
        lines += ["", "**Conflicting knowledge:**", _bullets(
            f"{' vs '.join(c['sources'])}: {c['reason']}" for c in result.knowledge_conflicts)]
    lines += ["", "## Similar historical incidents",
              _bullets(f"{s['number']} - {s['short_description']} (similarity {s['score']})"
                       for s in result.similar_incidents)]
    if result.change_assessment:
        ca = result.change_assessment
        lines += ["", "## Change assessment", f"- Risk: **{ca.get('risk_level')}**, decision: "
                  f"**{ca.get('recommended_decision')}** (human CAB approval required)",
                  f"- Missing: {', '.join(ca.get('missing_requirements', [])) or 'none'}"]
    lines += ["", "## Recommended corrective actions", _bullets(result.corrective_actions),
              "", "## Recommended preventive actions", _bullets(result.preventive_actions),
              "", "## Proposed actions (require human approval)"]
    lines += [f"- `{a['action_type']}` -> **{a.get('status')}**" for a in result.proposed_actions] or ["_None_"]
    if result.verification:
        lines += ["", "## Verification"]
        lines += [f"- [{'x' if c['passed'] else ' '}] {c['name']}: {c['detail']}"
                  for c in result.verification.get("checks", [])]
        lines.append(f"\nClosure allowed: **{result.verification.get('closure_allowed')}**")
    if result.security_flags:
        lines += ["", "## Security flags", _bullets(result.security_flags)]
    if result.errors:
        lines += ["", "## Errors", _bullets(result.errors)]
    lines += ["", "## Agent trace", _bullets(result.agent_trace), ""]
    return "\n".join(lines)
