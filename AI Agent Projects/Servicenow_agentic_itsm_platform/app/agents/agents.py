"""Specialised ITSM agents. Each returns a *partial* state update.

Design rules enforced here:
* Agents never create LLM clients; they use ``ctx.llm`` through ``complete_json``.
* Ticket text, logs and knowledge are untrusted: they are wrapped before reaching a model.
* Hypotheses are always ``unverified`` unless confirmed by an external, verified source.
* Agents only *propose* actions (``proposed_actions``); they never execute or write.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import AgentContext, BaseAgent
from app.llm.manager import LLMError
from app.schemas import ITSMRecord
from app.security.sanitize import UNTRUSTED_NOTICE, detect_injection, mask_secrets, wrap_untrusted

CATEGORIES = ("hardware", "network", "software", "database", "access", "change", "availability", "general")

_CATEGORY_RULES = {
    "hardware": ["laptop", "device", "printer", "hardware"],
    "network": ["network", "connectivity", "vpn", "dns"],
    "software": ["application", "portal", "error", "http", "software"],
    "database": ["database", "sql", "query", "connection pool"],
    "access": ["login", "password", "permission", "access", "provision"],
    "change": ["upgrade", "migration", "deployment", "maintenance"],
    "availability": ["outage", "unavailable", "availability", "downtime"],
}

_SERVICE_HINTS = [
    ("employee portal", "Employee Portal"), ("portal", "Web Portal"), ("application server", "Application Server"),
    ("database", "Database Service"), ("vpn", "VPN"), ("email", "Email"), ("laptop", "End-User Computing"),
    ("printer", "Print Service"), ("dns", "DNS"), ("network", "Network"),
]


def _rec(state: dict) -> ITSMRecord:
    return ITSMRecord(**state["record"])


def _text(record: ITSMRecord) -> str:
    return f"{record.short_description} {record.description}".strip()


def _display(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("display_value") or value.get("value") or "")
    return str(value or "")


def _try_llm(ctx: AgentContext, agent: str, system: str, user: str, schema: type[BaseModel], trace: list[str]):
    """Call the LLM Manager; return a validated model or ``None`` (with a trace note)."""
    if not ctx.llm_enabled:
        return None
    try:
        return ctx.llm.complete_json(f"{system}\n{UNTRUSTED_NOTICE}", user, schema, **ctx.llm_kwargs(agent))
    except LLMError as exc:
        trace.append(f"{agent}: LLM unavailable or invalid ({type(exc).__name__}); used rule-based fallback")
        return None


def _demote_certainty(text: str) -> str:
    """Models and rules must never present an unverified hypothesis as confirmed."""
    return re.sub(r"(?i)\b(confirmed|definitely|certainly|proven)\b", "suspected", text)


# --------------------------------------------------------------------------- classification
class _ClassificationOut(BaseModel):
    category: str
    affected_service: str = ""
    business_impact: str = ""


class ClassificationAgent(BaseAgent):
    name = "classification"
    description = "Classifies the record, identifies the affected service and business impact."
    critical = True
    input_keys = ("record",)

    def run(self, ctx, state):
        record = _rec(state)
        text = _text(record).lower()
        category = next((k for k, words in _CATEGORY_RULES.items() if any(w in text for w in words)),
                        record.category or "general")
        service = (_display(record.extra.get("business_service")) or _display(record.extra.get("cmdb_ci"))
                   or next((label for hint, label in _SERVICE_HINTS if hint in text), "") or "Unspecified service")
        level = {"1": "High", "2": "Medium", "3": "Low"}.get(record.impact, "Unknown")
        scope = " - multiple users affected" if re.search(r"multiple users|all users|several (users|employees)", text) else ""
        impact = f"{level} business impact{scope}"
        trace: list[str] = []

        out = _try_llm(
            ctx, self.name,
            "You classify ITSM records. Choose category from: " + ", ".join(CATEGORIES) + ".",
            wrap_untrusted("record", _text(record)), _ClassificationOut, trace)
        if out:
            if out.category.lower() in CATEGORIES:
                category = out.category.lower()
            service = out.affected_service.strip()[:120] or service
            impact = out.business_impact.strip()[:200] or impact
            trace.append("classification: refined with LLM (schema validated)")

        trace.append(f"classification: category={category}, service={service}")
        return {"category": category, "affected_service": service, "business_impact": impact,
                "priority": state.get("priority") or record.priority, "trace": trace}


# --------------------------------------------------------------------------- priority
_PRIORITY_MATRIX = {("1", "1"): 1, ("1", "2"): 2, ("2", "1"): 2, ("2", "2"): 3, ("1", "3"): 3,
                    ("3", "1"): 3, ("2", "3"): 4, ("3", "2"): 4, ("3", "3"): 5}
_P1_SIGNALS = ["all users", "multiple users", "outage", "unavailable", "95%", "production down"]
_P2_SIGNALS = ["intermittent", "slowness", "degraded", "503", "recurring"]


class PriorityAgent(BaseAgent):
    name = "priority"
    description = "Assesses priority from impact, urgency and text severity signals (deterministic, explainable)."
    input_keys = ("record", "affected_service")

    def run(self, ctx, state):
        record = _rec(state)
        text = _text(record).lower()
        candidates: dict[int, str] = {}
        if (record.impact, record.urgency) != ("3", "3") and (record.impact, record.urgency) in _PRIORITY_MATRIX:
            candidates[_PRIORITY_MATRIX[(record.impact, record.urgency)]] = (
                f"impact={record.impact}, urgency={record.urgency}")
        if any(s in text for s in _P1_SIGNALS):
            candidates[1] = "severity signals indicate broad or critical impact"
        elif any(s in text for s in _P2_SIGNALS):
            candidates[2] = "severity signals indicate degraded or recurring impact"
        current = int(record.priority) if record.priority.isdigit() else 3
        if candidates:
            best = min(candidates)
            priority, why = str(best), candidates[best]
        else:
            priority, why = str(current), "no stronger signal than the existing priority"
        update: dict = {"priority": priority, "priority_rationale": f"Recommended P{priority}: {why}.",
                        "trace": [f"priority: recommended P{priority} ({why})"]}
        if priority != record.priority:
            update["proposed_actions"] = [{
                "action_type": "priority_change", "record_type": record.record_type,
                "record_number": record.number, "status": "proposed",
                "payload": {"priority": priority}, "rationale": update["priority_rationale"]}]
        return update


# --------------------------------------------------------------------------- investigation
_TOKEN = re.compile(r"[a-z0-9]{4,}")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def keyword_similar_incidents(ctx: AgentContext, record: ITSMRecord, limit: int, threshold: float) -> list[dict]:
    """Fallback similarity: token overlap against other incidents in ServiceNow."""
    base = _tokens(_text(record))
    scored = []
    for other in ctx.servicenow.list_records("incident"):
        if other.number == record.number:
            continue
        toks = _tokens(_text(other))
        union = base | toks
        score = len(base & toks) / len(union) if union else 0.0
        if score >= threshold:
            scored.append({"number": other.number, "short_description": other.short_description,
                           "state": other.state, "score": round(score, 3), "source": f"incident:{other.number}"})
    return sorted(scored, key=lambda x: x["score"], reverse=True)[:limit]


class InvestigationAgent(BaseAgent):
    name = "investigation"
    description = "Collects evidence, finds similar historical incidents and identifies missing information."
    input_keys = ("record", "category", "affected_service", "diagnostics")

    def run(self, ctx, state):
        record = _rec(state)
        text = _text(record)
        rounds_done = sum(1 for h in state.get("history", []) if h["agent"] == self.name and h["status"] == "success")
        extended = rounds_done >= 1
        src = f"servicenow:{record.number}"
        evidence = [
            {"text": f"{record.record_type.title()} {record.number} is in state '{record.state}'", "source": src},
            {"text": f"Assignment group: {record.assignment_group or 'not specified'}", "source": src},
            {"text": f"Reported: {record.short_description}", "source": src},
        ]
        if record.description:
            evidence.append({"text": f"Description: {mask_secrets(record.description)[:500]}", "source": src})
        for key, value in (state.get("diagnostics") or {}).items():
            evidence.append({"text": f"{key}: {mask_secrets(str(value))[:300]}", "source": "diagnostics"})

        trace: list[str] = []
        missing: list[str] = []
        flags: list[str] = []
        low = text.lower()
        if len(record.description.strip()) < 20:
            missing.append("Detailed problem description")
        if not re.search(r"\b(\d{1,2}[:.]\d{2}|since|yesterday|today|ago|last (week|night))\b", low):
            missing.append("Incident start time")
        if not re.search(r"\b(users?|all|multiple|single|team|site|region|employees?)\b", low):
            missing.append("Scope: number or group of affected users")
        if not re.search(r"\b(\d{3}|error|exception|code|failed|timeout)\b", low):
            missing.append("Error message or code")
        if not state.get("diagnostics"):
            missing.append("Diagnostic data (logs, metrics) was not supplied")

        similar: list[dict] = []
        if state["workflow"] in ("incident", "problem"):
            try:
                finder = getattr(ctx.knowledge, "similar_incidents", None)
                similar = (finder(_text(record), exclude=record.number, limit=5 if extended else 3,
                                  extended=extended)
                           if finder else keyword_similar_incidents(ctx, record, 5 if extended else 3,
                                                                    0.08 if extended else 0.15))
                for item in similar:
                    evidence.append({"text": f"Similar historical incident {item['number']}: "
                                             f"{item['short_description']}", "source": item["source"]})
            except Exception as exc:  # noqa: BLE001 - degrade gracefully (e.g. ServiceNow timeout)
                missing.append("Historical incident lookup unavailable")
                trace.append(f"investigation: historical lookup failed ({type(exc).__name__}); continuing without it")

        if detect_injection(text):
            flags.append("possible_prompt_injection")
            evidence.append({"text": "Record text contains instruction-like content; it is treated as data only",
                             "source": "security"})
        insufficient = len(record.description.strip()) < 20 and len(record.short_description.split()) < 5
        trace.append(f"investigation: round {rounds_done + 1}, {len(evidence)} evidence item(s), "
                     f"{len(similar)} similar incident(s), {len(missing)} information gap(s)")
        return {"evidence": evidence, "missing_information": missing, "similar_incidents": similar,
                "insufficient_information": insufficient, "security_flags": flags, "trace": trace}


# --------------------------------------------------------------------------- knowledge
_DIRECTIVES = [("restart", "do not restart"), ("reboot", "do not reboot"), ("increase", "decrease"),
               ("enable", "disable")]


def find_conflicts(results: list[dict]) -> list[dict]:
    conflicts = []
    for i, a in enumerate(results):
        for b in results[i + 1:]:
            ma, mb = a.get("metadata") or {}, b.get("metadata") or {}
            if ma.get("topic") and ma.get("topic") == mb.get("topic") and ma.get("version") != mb.get("version"):
                conflicts.append({"sources": [a["source"], b["source"]],
                                  "reason": f"Different versions of topic '{ma['topic']}'"})
                continue
            la, lb = a["excerpt"].lower(), b["excerpt"].lower()
            for pos, neg in _DIRECTIVES:
                if (pos in la and neg not in la and neg in lb) or (pos in lb and neg not in lb and neg in la):
                    conflicts.append({"sources": [a["source"], b["source"]],
                                      "reason": f"Opposing guidance on '{pos}'"})
                    break
    return conflicts


class KnowledgeAgent(BaseAgent):
    name = "knowledge"
    description = "Retrieves relevant knowledge excerpts with citations. Never generates unsupported answers."
    input_keys = ("record", "category")

    def run(self, ctx, state):
        record = _rec(state)
        rounds_done = sum(1 for h in state.get("history", []) if h["agent"] == self.name and h["status"] == "success")
        limit = 8 if rounds_done else 5
        query = f"{_text(record)} {state.get('category', '')} {state.get('affected_service', '')}".strip()
        hits = ctx.knowledge.search(query, limit=limit)
        results = []
        for hit in hits:
            source = hit.get("source") or hit.get("title", "")
            results.append({
                "title": hit.get("title", ""),
                "source": source,
                "citation": hit.get("citation") or source,
                "excerpt": (hit.get("excerpt") or hit.get("content", ""))[:600],
                "score": hit.get("score", 0),
                "metadata": hit.get("metadata", {}),
                "suspicious": bool(hit.get("suspicious", False)),
            })
        conflicts = find_conflicts(results)
        trace = [f"knowledge: retrieved {len(results)} excerpt(s)"]
        if conflicts:
            trace.append(f"knowledge: {len(conflicts)} conflicting guidance pair(s) detected; not resolved automatically")
        update: dict = {"knowledge_results": results, "knowledge_conflicts": conflicts, "trace": trace}
        if any(r["suspicious"] for r in results):
            update["security_flags"] = ["suspicious_knowledge_content"]
            trace.append("knowledge: an excerpt contains instruction-like text; it is treated as data only")
        return update


# --------------------------------------------------------------------------- RCA
class _HypothesisItem(BaseModel):
    cause: str
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: list[str] = []


class _RCAOut(BaseModel):
    hypotheses: list[_HypothesisItem] = Field(min_length=1)
    corrective_actions: list[str] = []
    preventive_actions: list[str] = []


# keywords, cause, base confidence, corrective, preventive
_RCA_RULES = [
    (("cpu",), "Resource saturation or a runaway process causing high CPU", 0.55,
     ["Identify top CPU-consuming processes and compare against recent deployments",
      "Review application logs around the reported start time"],
     ["Add CPU utilisation alerting and capacity thresholds", "Load-test releases before production rollout"]),
    (("503", "unavailable", "outage", "downtime"), "Failed or overloaded service instance or upstream dependency", 0.55,
     ["Check health of service instances, load balancer and upstream dependencies",
      "Inspect error logs for the 503 responses"],
     ["Add synthetic availability monitoring", "Review redundancy and failover configuration"]),
    (("password", "login", "locked", "permission", "access denied"),
     "Authentication, account-state or authorisation problem", 0.5,
     ["Verify account status, group membership and recent access changes", "Check identity provider logs"],
     ["Review access-request and provisioning workflow for gaps"]),
    (("database", "sql", "query", "connection pool"), "Database contention, slow queries or connection-pool exhaustion",
     0.5, ["Review slow-query and connection-pool metrics", "Check for blocking sessions or locks"],
     ["Add database performance monitoring and connection limits review"]),
    (("network", "vpn", "dns", "connectivity", "latency"), "Network path, DNS or VPN gateway issue", 0.5,
     ["Test connectivity and DNS resolution from affected locations", "Review gateway and firewall logs"],
     ["Add network path monitoring for critical services"]),
    (("memory", "oom", "out of memory"), "Memory leak or insufficient memory allocation", 0.5,
     ["Review memory usage trend and garbage-collection or OOM logs"],
     ["Set memory alert thresholds and review resource limits"]),
    (("disk", "storage", "space"), "Storage capacity exhaustion", 0.5,
     ["Check filesystem and volume utilisation"], ["Add storage capacity forecasting and alerts"]),
    (("slow", "slowness", "degraded", "performance", "response time"),
     "Performance degradation from resource contention or a recent change", 0.45,
     ["Compare current performance baselines with the period before the issue began"],
     ["Track performance baselines and review change history for correlation"]),
]


class RCAAgent(BaseAgent):
    name = "rca"
    description = "Generates evidence-linked, unverified root-cause hypotheses with corrective and preventive actions."
    input_keys = ("evidence", "knowledge_results", "similar_incidents", "missing_information")

    def run(self, ctx, state):
        record = _rec(state)
        low = _text(record).lower()
        kb = state.get("knowledge_results", [])
        similar = state.get("similar_incidents", [])
        conflicts = state.get("knowledge_conflicts", [])
        missing = state.get("missing_information", [])
        trace: list[str] = []

        hypotheses: list[dict] = []
        corrective: list[str] = []
        preventive: list[str] = []
        has_metric = bool(re.search(r"\d+\s?%", low))
        has_timing = bool(re.search(r"\b(\d{1,2}[:.]\d{2}|since)\b", low))
        for keywords, cause, base, fix, prevent in _RCA_RULES:
            matched = [k for k in keywords if k in low]
            if not matched:
                continue
            evidence = [f"Record text mentions '{k}'" for k in matched]
            conf = base
            for item in kb:
                if any(k in (item["title"] + " " + item["excerpt"]).lower() for k in matched):
                    evidence.append(f"Knowledge article: {item['source']}")
                    conf += 0.1
                    break
            for item in similar[:2]:
                evidence.append(f"Similar incident {item['number']}")
                conf += 0.05
            if has_metric:
                evidence.append("Quantitative metric reported in the record")
                conf += 0.05
            if has_timing:
                evidence.append("Start time reported, enabling change correlation")
                conf += 0.05
            hypotheses.append({"cause": cause, "confidence": round(min(conf, 0.85), 2), "evidence": evidence,
                               "status": "unverified", "source": "rules"})
            corrective += fix
            preventive += prevent
        if state["workflow"] == "problem":
            refs = [f"Related incident {s['number']}" for s in similar]
            hypotheses.append({
                "cause": "A shared underlying cause common to the related incidents",
                "confidence": round(min(0.4 + 0.05 * len(similar), 0.7), 2),
                "evidence": refs or ["Record describes recurring symptoms"], "status": "unverified", "source": "rules"})
            corrective += ["Correlate linked incidents, changes and error patterns to find the common factor"]
            preventive += ["Document a known error and workaround once a cause is verified"]

        out = _try_llm(
            ctx, self.name,
            "You are an ITSM root-cause analyst. Use ONLY the supplied evidence. Propose hypotheses, never "
            "confirmed causes. Cite supporting_evidence strings copied from the supplied evidence.",
            wrap_untrusted("evidence", self._evidence_blob(state)), _RCAOut, trace)
        if out:
            allowed = " ".join([e["text"] for e in state.get("evidence", [])]
                               + [k["source"] + " " + k["title"] for k in kb]
                               + [s["number"] for s in similar]).lower()
            llm_h = []
            for h in out.hypotheses[:5]:
                grounded = [c for c in h.supporting_evidence
                            if c.strip() and (c.lower() in allowed or c.lower()[:40] in allowed)]
                conf = min(h.confidence, 0.85)
                if not grounded:
                    conf = min(conf, 0.4)
                llm_h.append({"cause": _demote_certainty(h.cause)[:300], "confidence": round(conf, 2),
                              "evidence": grounded or ["No grounded evidence cited by the model"],
                              "status": "unverified", "source": "llm"})
            hypotheses = llm_h
            corrective = [_demote_certainty(a) for a in out.corrective_actions] or corrective
            preventive = [_demote_certainty(a) for a in out.preventive_actions] or preventive
            trace.append(f"rca: {len(llm_h)} hypothesis(es) from LLM, grounded against supplied evidence")

        if conflicts:
            for h in hypotheses:
                h["confidence"] = round(max(0.0, h["confidence"] - 0.1), 2)
                h["evidence"].append("Note: knowledge sources conflict; confidence reduced")
        hypotheses.sort(key=lambda h: h["confidence"], reverse=True)

        if hypotheses:
            top = hypotheses[0]
            root = f"Probable cause (unverified hypothesis): {top['cause']}"
            status, conf = "unverified_hypothesis", top["confidence"]
        else:
            root = "Insufficient evidence to propose a probable cause; additional diagnostics are required."
            status, conf = "insufficient_evidence", 0.3
        needs_more = conf < 0.5 and bool(missing)
        trace.append(f"rca: {len(hypotheses)} hypothesis(es), top confidence {conf:.2f}, status={status}")
        return {"hypotheses": hypotheses, "root_cause": root, "root_cause_status": status, "confidence": conf,
                "corrective_actions": list(dict.fromkeys(corrective)),
                "preventive_actions": list(dict.fromkeys(preventive)),
                "needs_more_investigation": needs_more, "trace": trace}

    @staticmethod
    def _evidence_blob(state: dict) -> str:
        parts = [e["text"] for e in state.get("evidence", [])]
        parts += [f"KB[{k['source']}]: {k['excerpt'][:300]}" for k in state.get("knowledge_results", [])]
        parts += [f"Incident {s['number']}: {s['short_description']}" for s in state.get("similar_incidents", [])]
        parts += [f"Missing: {m}" for m in state.get("missing_information", [])]
        return "\n".join(parts)


# --------------------------------------------------------------------------- change management
class ChangeManagementAgent(BaseAgent):
    name = "change_management"
    description = "Assesses change risk, readiness and missing requirements. Recommends; never approves."
    input_keys = ("record", "category", "knowledge_results")

    def run(self, ctx, state):
        record = _rec(state)
        low = _text(record).lower()
        factors, score = [], 0
        for pattern, weight, label in [
            (r"production|prod\b", 2, "Targets a production environment"),
            (r"upgrade|migration|patch|deployment", 1, "Involves an upgrade, migration or deployment"),
            (r"emergency|urgent", 2, "Emergency or urgent change"),
            (r"database|firewall|core network|authentication", 1, "Touches a critical shared component"),
        ]:
            if re.search(pattern, low):
                score += weight
                factors.append(label)
        required = [("rollback", "Rollback plan"), ("test", "Test plan"), ("window|schedule|date", "Maintenance window"),
                    ("impact", "Impact assessment"), ("implementation|steps", "Implementation plan")]
        missing = [label for pattern, label in required if not re.search(pattern, low)]
        score += len(missing)
        level = "high" if score >= 5 else "medium" if score >= 3 else "low"
        decision = "needs_more_information" if missing else "ready_for_cab_review"
        assessment = {
            "risk_level": level, "risk_score": score, "risk_factors": factors,
            "missing_requirements": missing, "recommended_decision": decision,
            "approval_required": True,
            "note": "Keyword-based readiness check; plan contents are not validated. Final decision rests with the CAB.",
            "related_knowledge": [k["source"] for k in state.get("knowledge_results", [])[:3]],
        }
        return {"change_assessment": assessment, "root_cause": f"Not applicable (change assessment: {level} risk)",
                "root_cause_status": "not_applicable", "confidence": 0.6,
                "trace": [f"change_management: risk={level}, decision={decision}, {len(missing)} gap(s)"]}


# --------------------------------------------------------------------------- resolution
class ResolutionAgent(BaseAgent):
    name = "resolution"
    description = "Prepares recommendations and proposes (never executes) record updates for human approval."
    input_keys = ("root_cause", "corrective_actions", "knowledge_results", "change_assessment")

    def run(self, ctx, state):
        record = _rec(state)
        wf = state["workflow"]
        recs: list[str] = []
        if state.get("insufficient_information"):
            recs.append("Request missing details from the reporter: "
                        + "; ".join(state.get("missing_information", [])[:4]))
        if wf in ("incident", "problem"):
            recs += state.get("corrective_actions", [])
            if wf == "problem":
                recs += ["Create a permanent corrective-action plan with an owner and target date"]
            recs += [f"Follow documented guidance: {k['source']}" for k in state.get("knowledge_results", [])[:2]]
            if not state.get("corrective_actions"):
                recs += ["Collect logs and metrics for the affected service around the reported time"]
        elif wf == "change":
            assessment = state.get("change_assessment", {})
            recs += [f"Provide: {m}" for m in assessment.get("missing_requirements", [])]
            recs += ["Validate dependencies and affected services", "Obtain CAB approval before scheduling"]
        else:  # request
            recs += ["Validate requester identity and entitlement", "Check required approvals and prerequisites",
                     "Route to the responsible fulfilment group"]
        recs += [f"Preventive: {p}" for p in state.get("preventive_actions", [])]
        recs = list(dict.fromkeys(recs))

        note = (f"AI {wf} analysis (hypotheses are unverified; no action has been executed)\n"
                f"Assessment: {state.get('root_cause', 'n/a')}\n"
                f"Confidence: {state.get('confidence', 0):.0%}\n"
                f"Recommendations: {'; '.join(recs[:6])}")
        actions = [a for a in state.get("proposed_actions", [])]
        actions.append({"action_type": "incident_update", "record_type": record.record_type,
                        "record_number": record.number, "status": "proposed",
                        "payload": {"work_notes": mask_secrets(note)},
                        "rationale": "Record the AI analysis as a work note"})
        assessment = state.get("change_assessment")
        if assessment and assessment.get("recommended_decision") == "ready_for_cab_review":
            actions.append({"action_type": "change_approval", "record_type": "change",
                            "record_number": record.number, "status": "proposed",
                            "payload": {"work_notes": f"AI recommends CAB review ({assessment['risk_level']} risk)"},
                            "rationale": "Change appears ready for human CAB review"})
        update: dict = {"recommendations": recs, "proposed_actions": actions,
                        "trace": [f"resolution: {len(recs)} recommendation(s), {len(actions)} action(s) proposed "
                                  "for approval; nothing executed"]}
        if not state.get("root_cause"):  # e.g. service requests: fulfilment, not failure analysis
            update.update({"root_cause": "Not applicable (fulfilment workflow; no failure to analyse)",
                           "root_cause_status": "not_applicable"})
        return update


# --------------------------------------------------------------------------- verification
class VerificationAgent(BaseAgent):
    name = "verification"
    description = "Validates the workflow output for consistency, evidence and policy compliance."
    input_keys = ("root_cause", "proposed_actions", "hypotheses", "failed")

    def run(self, ctx, state):
        checks = []
        actions = state.get("proposed_actions", [])
        checks.append({"name": "no_unapproved_execution", "passed": all(a.get("status") == "proposed" for a in actions),
                       "detail": f"{len(actions)} action(s) proposed, none executed"})
        root = state.get("root_cause", "")
        overclaim = bool(re.search(r"(?i)\b(confirmed|definitely|certainly|proven)\b", root)) \
            and state.get("root_cause_status") != "confirmed"
        checks.append({"name": "root_cause_not_overclaimed", "passed": not overclaim,
                       "detail": "Root cause is phrased as a hypothesis" if not overclaim
                       else "Unverified cause was phrased as confirmed; corrected"})
        if state["workflow"] in ("incident", "problem"):
            weak = [h["cause"] for h in state.get("hypotheses", []) if not h.get("evidence")]
            checks.append({"name": "hypotheses_have_evidence", "passed": not weak,
                           "detail": "All hypotheses cite evidence" if not weak else f"{len(weak)} without evidence"})
        checks.append({"name": "recommendations_present", "passed": bool(state.get("recommendations")),
                       "detail": f"{len(state.get('recommendations', []))} recommendation(s)"})
        checks.append({"name": "all_agents_succeeded", "passed": not state.get("failed"),
                       "detail": "No agent failures" if not state.get("failed")
                       else "Failed agents: " + ", ".join(state["failed"])})
        if state.get("security_flags"):
            checks.append({"name": "untrusted_input_flagged", "passed": True,
                           "detail": "Flags: " + ", ".join(state["security_flags"]) + " (input treated as data)"})
        verification = {
            "checks": checks, "all_passed": all(c["passed"] for c in checks),
            "required_followup": ["Confirm the outcome with the requester/owner",
                                  "Validate that no related error is recurring",
                                  "Record evidence before any closure"],
            "closure_allowed": False,
            "reason": "Closure requires an executed, independently verified fix and explicit human approval.",
        }
        update: dict = {"verification": verification,
                        "trace": [f"verification: {sum(c['passed'] for c in checks)}/{len(checks)} checks passed; "
                                  "closure not authorised"]}
        if overclaim:
            update["root_cause"] = _demote_certainty(root)
        return update
