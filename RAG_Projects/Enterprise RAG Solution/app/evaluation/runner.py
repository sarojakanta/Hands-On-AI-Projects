"""
Evaluation runner.

A golden set is a JSON Lines file, one object per line::

    {"id": "pl-2020", "question": "What was the profit or loss in 2020?",
     "expected_sources": ["profit_and_loss.txt"], "expected_facts": ["net loss of $2.8 million"]}
    {"id": "off-topic", "question": "What is the capital of France?", "should_answer": false}

Retrieval metrics (no LLM, deterministic) are computed with the same relevance gate and context builder
that production answers use:

* ``source_hit_at_1`` / ``source_hit_at_k``: an expected source is the first / among the retrieved passages
* ``mrr``: mean reciprocal rank of the first expected source
* ``context_hit``: an expected source survives the relevance gate (it would be shown to the LLM)
* ``fact_recall``: every expected fact string appears in the gated context
* ``refusal_ok`` (for ``should_answer: false``): nothing passes the gate, so the LLM is not called

Generation metrics (``generate=True``, needs a working LLM) additionally check the real answer:
``answer_has_facts``, ``cited_expected_source`` and ``refusal_ok`` (answer says it was not found).
Fact checks are case-insensitive substring matches: strict on purpose and cheap, but a correct answer worded
differently from the fact string is counted as a miss.
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.container import build_services
from app.core.config import Settings
from app.rag.context_builder import build_context
from app.rag.pipeline import RagOptions


@dataclass(frozen=True)
class EvalItem:
    id: str
    question: str
    expected_sources: tuple[str, ...] = ()
    expected_facts: tuple[str, ...] = ()
    should_answer: bool = True


@dataclass
class ItemResult:
    id: str
    question: str
    should_answer: bool
    retrieved_sources: list[str] = field(default_factory=list)
    context_sources: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    reciprocal_rank: float | None = None
    answer: str | None = None
    error: str | None = None
    latency_ms: float = 0.0


@dataclass
class EvalReport:
    config: dict[str, Any]
    items: list[ItemResult]
    metrics: dict[str, float | int | None]

    def to_dict(self) -> dict[str, Any]:
        return {"config": self.config, "metrics": self.metrics, "items": [asdict(i) for i in self.items]}

    def failures(self) -> list[ItemResult]:
        return [i for i in self.items if i.error or not all(i.checks.values())]


def load_dataset(path: Path) -> list[EvalItem]:
    items: list[EvalItem] = []
    seen: set[str] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
            item = EvalItem(
                id=str(data["id"]),
                question=str(data["question"]).strip(),
                expected_sources=tuple(data.get("expected_sources", ())),
                expected_facts=tuple(data.get("expected_facts", ())),
                should_answer=bool(data.get("should_answer", True)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path.name} line {number}: invalid item ({exc})") from exc
        if not item.question:
            raise ValueError(f"{path.name} line {number}: empty question")
        if item.id in seen:
            raise ValueError(f"{path.name} line {number}: duplicate id {item.id!r}")
        if item.should_answer and not item.expected_sources:
            raise ValueError(f"{path.name} line {number}: answerable item {item.id!r} needs expected_sources")
        seen.add(item.id)
        items.append(item)
    if not items:
        raise ValueError(f"{path.name} contains no items")
    return items


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _summarise(results: list[ItemResult], generate: bool) -> dict[str, float | int | None]:
    answerable = [r for r in results if r.should_answer and not r.error]
    refusals = [r for r in results if not r.should_answer and not r.error]
    metrics: dict[str, float | int | None] = {
        "items": len(results),
        "answerable": len([r for r in results if r.should_answer]),
        "unanswerable": len([r for r in results if not r.should_answer]),
        "errors": len([r for r in results if r.error]),
        "source_hit_at_1": _rate([r.checks["source_hit_at_1"] for r in answerable]),
        "source_hit_at_k": _rate([r.checks["source_hit_at_k"] for r in answerable]),
        "mrr": round(statistics.fmean(r.reciprocal_rank or 0.0 for r in answerable), 4) if answerable else None,
        "context_hit": _rate([r.checks["context_hit"] for r in answerable]),
        "fact_recall": _rate([r.checks["fact_recall"] for r in answerable if "fact_recall" in r.checks]),
        "refusal_ok": _rate([r.checks["refusal_ok"] for r in refusals]),
    }
    if generate:
        metrics["answer_has_facts"] = _rate([r.checks["answer_has_facts"] for r in answerable if "answer_has_facts" in r.checks])
        metrics["cited_expected_source"] = _rate([r.checks["cited_expected_source"] for r in answerable])
        metrics["answer_refusal_ok"] = _rate([r.checks["answer_refusal_ok"] for r in refusals])
    latencies = sorted(r.latency_ms for r in results if not r.error)
    if latencies:
        metrics["latency_ms_median"] = round(statistics.median(latencies), 1)
        metrics["latency_ms_p95"] = round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 1)
    return metrics


async def evaluate(
    items: list[EvalItem],
    *,
    corpus_dir: Path,
    settings_overrides: dict[str, Any] | None = None,
    generate: bool = False,
    embedder=None,
    data_dir: Path | None = None,
) -> EvalReport:
    """
    Index ``corpus_dir`` into a throw-away data directory (so results are reproducible and your real
    knowledge base is never touched), then score every item. ``data_dir`` evaluates an existing one instead.
    """
    overrides = dict(settings_overrides or {})
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        settings = Settings(
            _env_file=None if embedder is not None else ".env",
            data_dir=data_dir or Path(scratch),
            seed_sample_docs=data_dir is None,
            seed_dir=corpus_dir,
            log_level="WARNING",
            **overrides,
        )
        services = build_services(settings, embedder=embedder)
        try:
            services.start()
            cfg = services.rag.config
            top_k = cfg.top_k
            results: list[ItemResult] = []
            for item in items:
                results.append(await _run_item(services, item, top_k, cfg, generate))
            config = {
                "retrieval_mode": settings.rag_retrieval_mode,
                "reranker": settings.rag_reranker,
                "chunk_strategy": settings.chunk_strategy,
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
                "top_k": top_k,
                "min_score": cfg.min_score,
                "embedding_model": services.embedder.model_id,
                "generate": generate,
                "llm": f"{services.llm.config.provider}/{services.llm.config.model}" if generate else None,
            }
            return EvalReport(config=config, items=results, metrics=_summarise(results, generate))
        finally:
            await services.aclose()


async def _run_item(services, item: EvalItem, top_k: int, cfg, generate: bool) -> ItemResult:
    result = ItemResult(id=item.id, question=item.question, should_answer=item.should_answer)
    started = time.perf_counter()
    try:
        chunks = await services.retriever.retrieve(item.question, top_k)
        context = build_context(
            chunks, min_score=cfg.min_score, max_chars=cfg.max_context_chars, max_items=top_k,
            lexical_min_coverage=cfg.lexical_min_coverage,
        )
        result.retrieved_sources = [c.source for c in chunks]
        result.context_sources = [i.chunk.source for i in context.items]
        context_text = "\n".join(i.chunk.text for i in context.items).lower()
        expected = set(item.expected_sources)

        if item.should_answer:
            ranks = [n for n, s in enumerate(result.retrieved_sources, start=1) if s in expected]
            result.reciprocal_rank = 1.0 / ranks[0] if ranks else 0.0
            result.checks["source_hit_at_1"] = bool(result.retrieved_sources) and result.retrieved_sources[0] in expected
            result.checks["source_hit_at_k"] = bool(ranks)
            result.checks["context_hit"] = any(s in expected for s in result.context_sources)
            if item.expected_facts:
                result.checks["fact_recall"] = all(f.lower() in context_text for f in item.expected_facts)
        else:
            result.checks["refusal_ok"] = not context.items

        if generate:
            answer = await services.rag.answer(item.question, options=RagOptions())
            result.answer = answer.answer
            lowered = answer.answer.lower()
            if item.should_answer:
                if item.expected_facts:
                    result.checks["answer_has_facts"] = all(f.lower() in lowered for f in item.expected_facts)
                result.checks["cited_expected_source"] = any(c.document in expected for c in answer.citations)
            else:
                result.checks["answer_refusal_ok"] = not answer.found_in_documents
    except Exception as exc:   # noqa: BLE001 - one bad item must not abort the run
        result.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    result.latency_ms = (time.perf_counter() - started) * 1000
    return result
