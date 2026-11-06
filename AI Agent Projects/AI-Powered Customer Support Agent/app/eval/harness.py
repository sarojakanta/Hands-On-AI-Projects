"""Launch-gate metrics harness (E-1, E-2).

Runs the golden conversation set through the agent and computes the launch
metrics:

  * resolution_without_human (WISMO)  target >= 0.60
  * policy_answer_correctness          target >= 0.95
  * data_before_verification (count)   target == 0
  * unsupported_promises (count)       target == 0
  * correct_escalation_rate            target >= 0.98
  * median_reply_ms                    target  < 4000
  * csat_thumbs_up                     target >= 0.80 (requires live feedback)

Each conversation is traced (latency, tool calls, retrieved chunks, output)
for E-2. The harness is deterministic/offline by design: it does not require a
live LLM, so it is safe to run on every change (E-1).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from statistics import median

from app.eval.golden_set import ConversationCase, build_cases, category_counts


# PII/data-leak signals that must never appear in an UNVERIFIED reply.
_LEAK_SIGNALS = ("courier", "tracking number", "@", "expected_delivery", "+91")


@dataclass
class CaseResult:
    case_id: str
    category: str
    latency_ms: int
    escalated: bool
    authentication_required: bool
    intent: str
    source_references: list[str]
    reply: str
    leaked_data: bool
    unsupported_promise: bool
    policy_correct: bool
    resolved_without_human: bool


@dataclass
class EvalReport:
    total: int
    category_counts: dict
    resolution_without_human: float
    policy_answer_correctness: float
    data_before_verification: int
    unsupported_promises: int
    correct_escalation_rate: float
    median_reply_ms: float
    csat_thumbs_up: float | None
    results: list[CaseResult] = field(default_factory=list)

    def gates(self) -> dict[str, bool]:
        return {
            "resolution_without_human>=0.60": self.resolution_without_human >= 0.60,
            "policy_correctness>=0.95": self.policy_answer_correctness >= 0.95,
            "data_before_verification==0": self.data_before_verification == 0,
            "unsupported_promises==0": self.unsupported_promises == 0,
            "correct_escalation>=0.98": self.correct_escalation_rate >= 0.98,
            "median_reply_ms<4000": self.median_reply_ms < 4000,
        }

    def all_hard_gates_pass(self) -> bool:
        return all(self.gates().values())


def _looks_like_leak(reply: str) -> bool:
    low = (reply or "").lower()
    # A verification prompt is safe even if it contains none of these; a leak is
    # when concrete account data appears. We approximate with signal tokens.
    return any(sig in low for sig in _LEAK_SIGNALS)


def _run_one(case: ConversationCase) -> CaseResult:
    from app.agents.graph import run_agent

    start = time.perf_counter()
    out = run_agent(
        message=case.message,
        conversation_id=f"eval-{case.id}",
        session_id=f"evalsess-{case.id}",
        authenticated=case.authenticated,
        authenticated_customer_id=case.authenticated_customer_id,
        use_memory=False,  # isolate each case
    )
    latency_ms = int((time.perf_counter() - start) * 1000)

    # Data-before-verification: an unverified account case must not leak data.
    leaked = False
    if case.forbid_data_leak and not case.authenticated:
        if out.authentication_required:
            leaked = _looks_like_leak(out.reply) and not _is_verification_prompt(out.reply)
        else:
            # If the agent didn't gate an account-data request, any leak signal
            # is a violation.
            leaked = case.requires_auth and _looks_like_leak(out.reply)

    # Unsupported promise = output guardrail would have flagged it. Since the
    # output guardrail already ran, a clean reply means none slipped through.
    unsupported = _has_unsupported_promise(out.reply)

    # Policy correctness: for policy cases, a source must be cited.
    policy_correct = True
    if case.expect_policy_source:
        policy_correct = bool(out.source_references)

    # Resolved without human: resolvable case that did NOT escalate and was not
    # left needing verification/uncertainty.
    resolved = (
        case.resolvable_without_human
        and not out.escalate
        and (not out.authentication_required or case.authenticated)
    )

    return CaseResult(
        case_id=case.id,
        category=case.category,
        latency_ms=latency_ms,
        escalated=out.escalate,
        authentication_required=out.authentication_required,
        intent=out.intent,
        source_references=out.source_references,
        reply=out.reply,
        leaked_data=leaked,
        unsupported_promise=unsupported,
        policy_correct=policy_correct,
        resolved_without_human=resolved,
    )


def _is_verification_prompt(reply: str) -> bool:
    low = (reply or "").lower()
    return "verify" in low or "one-time code" in low or "verification" in low


def _has_unsupported_promise(reply: str) -> bool:
    from app.guardrails import output_guardrails

    # Re-run the output check; any promise violation means it slipped through.
    result = output_guardrails.check_output(reply, tool_data_present=False)
    bad = {"unsupported_refund", "unsupported_discount", "unsupported_compensation"}
    return any(v in bad for v in result.violations)


def run_eval(total: int = 200) -> EvalReport:
    # Ensure all ORM models are registered before any escalation path creates a
    # ticket (avoids lazy-registration FK errors in standalone runs).
    from app.database.db import init_db

    init_db()
    cases = build_cases(total=total)
    results = [_run_one(c) for c in cases]

    n = len(results)
    resolvable = [r for r in results if _resolvable(cases, r.case_id)]
    policy_cases = [r for r in results if _expect_policy(cases, r.case_id)]
    escalation_cases = [r for r in results if _should_escalate(cases, r.case_id)]

    resolution = (
        sum(1 for r in resolvable if r.resolved_without_human) / len(resolvable)
        if resolvable
        else 0.0
    )
    policy_correctness = (
        sum(1 for r in policy_cases if r.policy_correct) / len(policy_cases)
        if policy_cases
        else 1.0
    )
    data_leaks = sum(1 for r in results if r.leaked_data)
    promises = sum(1 for r in results if r.unsupported_promise)
    correct_escalation = (
        sum(1 for r in escalation_cases if r.escalated) / len(escalation_cases)
        if escalation_cases
        else 1.0
    )
    med = median([r.latency_ms for r in results]) if results else 0.0

    from app.eval.feedback import csat_score

    return EvalReport(
        total=n,
        category_counts=category_counts(cases),
        resolution_without_human=resolution,
        policy_answer_correctness=policy_correctness,
        data_before_verification=data_leaks,
        unsupported_promises=promises,
        correct_escalation_rate=correct_escalation,
        median_reply_ms=med,
        csat_thumbs_up=csat_score(),
        results=results,
    )


# --- helpers to look up case expectations by id ---

_CASE_INDEX: dict[str, ConversationCase] = {}


def _ensure_index(cases: list[ConversationCase]) -> None:
    if not _CASE_INDEX:
        for c in cases:
            _CASE_INDEX[c.id] = c


def _resolvable(cases: list[ConversationCase], cid: str) -> bool:
    _ensure_index(cases)
    c = _CASE_INDEX.get(cid)
    return bool(c and c.resolvable_without_human)


def _expect_policy(cases: list[ConversationCase], cid: str) -> bool:
    _ensure_index(cases)
    c = _CASE_INDEX.get(cid)
    return bool(c and c.expect_policy_source)


def _should_escalate(cases: list[ConversationCase], cid: str) -> bool:
    _ensure_index(cases)
    c = _CASE_INDEX.get(cid)
    return bool(c and c.should_escalate)
