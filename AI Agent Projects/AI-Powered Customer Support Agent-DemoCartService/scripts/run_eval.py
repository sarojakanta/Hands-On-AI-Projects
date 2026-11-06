"""Run the full launch-evaluation set and print metrics vs. gates (E-1, E-2).

Usage (from project root):
    python scripts/run_eval.py

Runs deterministically (no live LLM required) so it is safe for CI on every
prompt / model / tool change.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.eval.harness import run_eval  # noqa: E402


def main() -> None:
    report = run_eval(total=200)
    print(f"Golden set: {report.total} conversations")
    print("Category distribution:")
    for cat, n in report.category_counts.items():
        print(f"  {cat:16} {n:3}  ({n / report.total:.0%})")

    print("\nLaunch metrics:")
    print(f"  resolution_without_human : {report.resolution_without_human:.1%}  (target >= 60%)")
    print(f"  policy_answer_correctness: {report.policy_answer_correctness:.1%}  (target >= 95%)")
    print(f"  data_before_verification : {report.data_before_verification}      (target == 0)")
    print(f"  unsupported_promises     : {report.unsupported_promises}      (target == 0)")
    print(f"  correct_escalation_rate  : {report.correct_escalation_rate:.1%}  (target >= 98%)")
    print(f"  median_reply_ms          : {report.median_reply_ms:.0f} ms  (target < 4000)")
    csat = "n/a (needs live feedback)" if report.csat_thumbs_up is None else f"{report.csat_thumbs_up:.1%}"
    print(f"  csat_thumbs_up           : {csat}  (target >= 80%)")

    print("\nHard gates:")
    for gate, ok in report.gates().items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {gate}")
    print(f"\nAll hard gates pass: {report.all_hard_gates_pass()}")


if __name__ == "__main__":
    main()
