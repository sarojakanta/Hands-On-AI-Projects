"""The evaluation harness: dataset validation, metric arithmetic, CLI exit codes."""

import json

import pytest

from app.evaluation import EvalItem, evaluate, load_dataset
from app.evaluation.__main__ import main
from tests.embedders import HashingEmbedder

pytestmark = pytest.mark.anyio

CORPUS = {
    "tea.txt": "Green tea contains catechins. Brewing green tea at eighty degrees avoids bitterness completely.",
    "bikes.txt": "A derailleur moves the chain between sprockets. Adjust the derailleur cable tension with the barrel adjuster.",
}


@pytest.fixture
def corpus(tmp_path):
    folder = tmp_path / "corpus"
    folder.mkdir()
    for name, text in CORPUS.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def write_dataset(tmp_path, *lines):
    path = tmp_path / "golden.jsonl"
    path.write_text("\n".join(json.dumps(line) if isinstance(line, dict) else line for line in lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------- dataset


def test_dataset_loading_skips_comments_and_blank_lines(tmp_path):
    path = write_dataset(tmp_path, "# comment", "", {"id": "a", "question": " What? ", "expected_sources": ["tea.txt"]},
                         {"id": "b", "question": "Off topic", "should_answer": False})
    items = load_dataset(path)
    assert [i.id for i in items] == ["a", "b"] and items[0].question == "What?" and items[1].should_answer is False


@pytest.mark.parametrize(
    "line, message",
    [
        ('{"id": "a"}', "invalid item"),
        ("not json", "invalid item"),
        ('{"id": "a", "question": "  "}', "empty question"),
        ('{"id": "a", "question": "x"}', "needs expected_sources"),
    ],
)
def test_invalid_dataset_lines_name_the_line(tmp_path, line, message):
    with pytest.raises(ValueError, match=message) as info:
        load_dataset(write_dataset(tmp_path, line))
    assert "line 1" in str(info.value)


def test_duplicate_ids_and_empty_files_are_rejected(tmp_path):
    item = {"id": "a", "question": "x", "expected_sources": ["tea.txt"]}
    with pytest.raises(ValueError, match="duplicate id"):
        load_dataset(write_dataset(tmp_path, item, item))
    with pytest.raises(ValueError, match="no items"):
        load_dataset(write_dataset(tmp_path, "# only a comment"))


def test_the_shipped_golden_set_is_valid():
    from app.core.config import PROJECT_ROOT

    items = load_dataset(PROJECT_ROOT / "eval" / "golden.jsonl")
    assert len(items) >= 15 and any(not i.should_answer for i in items)
    sample_names = {p.name for p in (PROJECT_ROOT / "sample_docs").iterdir()}
    assert {s for i in items for s in i.expected_sources} <= sample_names


# ---------------------------------------------------------------- metrics


async def run(corpus, items, generate=False, **overrides):
    return await evaluate(items, corpus_dir=corpus, embedder=HashingEmbedder(), generate=generate,
                          settings_overrides={"rag_min_score": 0.3, **overrides})


async def test_hits_misses_and_mrr_are_computed_per_item(corpus):
    items = [
        EvalItem("hit", "How do I brew green tea without bitterness?", ("tea.txt",), ("eighty degrees",)),
        EvalItem("wrong-source", "How do I brew green tea without bitterness?", ("bikes.txt",)),
        EvalItem("missing-fact", "How do I brew green tea without bitterness?", ("tea.txt",), ("ninety degrees",)),
    ]
    report = await run(corpus, items)
    by_id = {r.id: r for r in report.items}
    assert by_id["hit"].checks == {"source_hit_at_1": True, "source_hit_at_k": True, "context_hit": True, "fact_recall": True}
    assert by_id["hit"].reciprocal_rank == 1.0
    assert by_id["wrong-source"].checks["source_hit_at_1"] is False
    assert by_id["wrong-source"].reciprocal_rank == 0.5          # bikes.txt is retrieved second
    assert by_id["missing-fact"].checks["fact_recall"] is False
    m = report.metrics
    assert m["answerable"] == 3 and m["source_hit_at_k"] == pytest.approx(1.0) and m["source_hit_at_1"] == pytest.approx(0.6667)
    assert m["fact_recall"] == 0.5          # only items that list facts count
    assert m["mrr"] == pytest.approx(0.8333)
    assert {i.id for i in report.failures()} == {"wrong-source", "missing-fact"}


async def test_unanswerable_questions_pass_when_nothing_clears_the_gate(corpus):
    report = await run(corpus, [EvalItem("off", "zzz qqq xxx unrelated", (), (), should_answer=False)], rag_min_score=0.9)
    assert report.items[0].checks == {"refusal_ok": True} and report.metrics["refusal_ok"] == 1.0
    leaky = await run(corpus, [EvalItem("leak", "How do I brew green tea?", (), (), should_answer=False)], rag_min_score=0.0)
    assert leaky.items[0].checks == {"refusal_ok": False}


async def test_report_records_the_configuration_and_serialises(corpus):
    report = await run(corpus, [EvalItem("a", "derailleur cable", ("bikes.txt",))], rag_retrieval_mode="vector", chunk_strategy="recursive")
    assert report.config["retrieval_mode"] == "vector" and report.config["chunk_strategy"] == "recursive"
    json.dumps(report.to_dict())      # must be JSON serialisable


async def test_one_failing_item_does_not_abort_the_run(corpus, monkeypatch):
    from app.rag.store_retriever import StoreRetriever

    real = StoreRetriever.retrieve

    async def flaky(self, query, top_k):
        if "explode" in query:
            raise RuntimeError("boom")
        return await real(self, query, top_k)

    monkeypatch.setattr(StoreRetriever, "retrieve", flaky)
    items = [EvalItem("bad", "explode please", ("tea.txt",)), EvalItem("good", "derailleur cable tension", ("bikes.txt",))]
    report = await run(corpus, items)
    assert report.metrics["errors"] == 1 and report.items[0].error.startswith("RuntimeError")
    assert report.items[1].checks["source_hit_at_1"] is True


async def test_generation_checks_use_the_pipeline_answer(corpus, monkeypatch):
    from types import SimpleNamespace

    from app.rag.pipeline import RagPipeline

    async def fake_answer(self, question, conversation_id=None, options=None):
        if "tea" in question:
            return SimpleNamespace(answer="Brew at eighty degrees [1].", found_in_documents=True, citations=[SimpleNamespace(document="tea.txt")])
        return SimpleNamespace(answer="I could not find that.", found_in_documents=False, citations=[])

    monkeypatch.setattr(RagPipeline, "answer", fake_answer)
    items = [
        EvalItem("tea", "tea temperature?", ("tea.txt",), ("EIGHTY degrees",)),
        EvalItem("off", "unrelated nonsense words", (), (), should_answer=False),
    ]
    report = await run(corpus, items, generate=True)
    assert report.items[0].checks["answer_has_facts"] and report.items[0].checks["cited_expected_source"]
    assert report.items[1].checks["answer_refusal_ok"] is True
    assert report.metrics["answer_has_facts"] == 1.0 and report.metrics["answer_refusal_ok"] == 1.0


# ---------------------------------------------------------------- command line


def test_cli_prints_a_table_writes_a_report_and_enforces_thresholds(corpus, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("app.container.create_embedder", lambda settings: HashingEmbedder())
    dataset = write_dataset(tmp_path, {"id": "bike", "question": "derailleur cable tension", "expected_sources": ["bikes.txt"]})
    report_path = tmp_path / "out.json"
    code = main(["--dataset", str(dataset), "--corpus", str(corpus), "--mode", "vector,hybrid",
                 "--report", str(report_path), "--min-context-hit", "0.5"])
    out = capsys.readouterr().out
    assert code == 0 and "vector/none/sentence" in out and "hybrid/none/sentence" in out and "context_hit" in out
    assert len(json.loads(report_path.read_text())) == 2

    failing = write_dataset(tmp_path, {"id": "x", "question": "derailleur cable tension", "expected_sources": ["tea.txt"]})
    code = main(["--dataset", str(failing), "--corpus", str(corpus), "--min-context-hit", "0.9"])
    captured = capsys.readouterr()
    assert code == 1 and "FAIL: context_hit" in captured.err
