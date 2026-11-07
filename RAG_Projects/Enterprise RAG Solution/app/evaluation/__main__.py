"""
Command line: ``python -m app.evaluation --dataset eval/golden.jsonl``

Examples
  python -m app.evaluation                                   # retrieval metrics, default settings
  python -m app.evaluation --mode vector,hybrid              # compare retrieval modes side by side
  python -m app.evaluation --mode hybrid --reranker cross-encoder --report out.json
  python -m app.evaluation --generate                        # also call the configured LLM (slow)
  python -m app.evaluation --min-context-hit 0.9             # exit code 1 when below a threshold
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.evaluation.runner import EvalReport, evaluate, load_dataset

METRIC_ORDER = [
    "items", "answerable", "unanswerable", "errors",
    "source_hit_at_1", "source_hit_at_k", "mrr", "context_hit", "fact_recall", "refusal_ok",
    "answer_has_facts", "cited_expected_source", "answer_refusal_ok",
    "latency_ms_median", "latency_ms_p95",
]


def _print_table(reports: list[EvalReport]) -> None:
    labels = [f"{r.config['retrieval_mode']}/{r.config['reranker']}/{r.config['chunk_strategy']}" for r in reports]
    width = max(len(label) for label in labels + ["metric"]) + 2
    print("metric".ljust(24) + "".join(label.ljust(width) for label in labels))
    for name in METRIC_ORDER:
        if not any(name in r.metrics for r in reports):
            continue
        row = name.ljust(24)
        for r in reports:
            value = r.metrics.get(name)
            row += ("-" if value is None else str(value)).ljust(width)
        print(row)


def _print_failures(report: EvalReport) -> None:
    failing = report.failures()
    if not failing:
        return
    label = f"{report.config['retrieval_mode']}/{report.config['reranker']}"
    print(f"\nItems with a failed check ({label}):")
    for item in failing:
        failed = item.error or ", ".join(k for k, v in item.checks.items() if not v)
        print(f"  - {item.id}: {failed}  | retrieved={item.retrieved_sources[:3]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "eval" / "golden.jsonl")
    parser.add_argument("--corpus", type=Path, default=PROJECT_ROOT / "sample_docs", help="Folder indexed into a temporary data directory.")
    parser.add_argument("--use-existing-data", action="store_true", help="Evaluate the configured DATA_DIR instead (read only).")
    parser.add_argument("--mode", default="", help="Retrieval mode(s), comma separated: vector,hybrid")
    parser.add_argument("--reranker", default="", help="none or cross-encoder")
    parser.add_argument("--chunk-strategy", default="", help="sentence, recursive or structure")
    parser.add_argument("--chunk-overlap", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--generate", action="store_true", help="Also run the full pipeline with the configured LLM.")
    parser.add_argument("--report", type=Path, default=None, help="Write the full JSON report here.")
    parser.add_argument("--min-context-hit", type=float, default=None, help="Fail (exit 1) below this context_hit.")
    parser.add_argument("--min-refusal-ok", type=float, default=None, help="Fail (exit 1) below this refusal_ok.")
    args = parser.parse_args(argv)

    items = load_dataset(args.dataset)
    modes = [m.strip() for m in args.mode.split(",") if m.strip()] or [None]

    overrides: dict = {}
    if args.reranker:
        overrides["rag_reranker"] = args.reranker
    if args.chunk_strategy:
        overrides["chunk_strategy"] = args.chunk_strategy
    if args.chunk_overlap is not None:
        overrides["chunk_overlap"] = args.chunk_overlap
    if args.top_k is not None:
        overrides["rag_top_k"] = args.top_k
    if args.min_score is not None:
        overrides["rag_min_score"] = args.min_score

    async def run_all() -> list[EvalReport]:
        reports = []
        for mode in modes:
            current = dict(overrides)
            if mode:
                current["rag_retrieval_mode"] = mode
            data_dir = None
            if args.use_existing_data:
                from app.core.config import get_settings
                data_dir = get_settings().data_path
            reports.append(
                await evaluate(items, corpus_dir=args.corpus, settings_overrides=current, generate=args.generate, data_dir=data_dir)
            )
        return reports

    reports = asyncio.run(run_all())
    print(f"dataset: {args.dataset.name} ({len(items)} items)   corpus: {'existing data' if args.use_existing_data else args.corpus}\n")
    _print_table(reports)
    for report in reports:
        _print_failures(report)
    if args.report:
        args.report.write_text(json.dumps([r.to_dict() for r in reports], indent=2), encoding="utf-8")
        print(f"\nreport written to {args.report}")

    exit_code = 0
    for report in reports:
        for key, minimum in (("context_hit", args.min_context_hit), ("refusal_ok", args.min_refusal_ok)):
            value = report.metrics.get(key)
            if minimum is not None and (value is None or value < minimum):
                print(f"\nFAIL: {key}={value} is below {minimum} ({report.config['retrieval_mode']})", file=sys.stderr)
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
