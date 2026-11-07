"""HTTP-level tests for /api/v1/chat and /api/v1/conversations/*. The LLM and retriever are fakes."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_rag_pipeline
from app.api.routes.chat import router
from app.core.errors import register_error_handlers
from app.llm.errors import ProviderUnavailableError, StreamInterruptedError
from app.rag.types import RetrievedChunk
from tests.helpers import FakeProvider
from tests.rag_helpers import ANSWER, REWRITTEN, build


def make_api(**build_kwargs):
    harness = build(**build_kwargs)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_rag_pipeline] = lambda: harness.pipeline
    harness.client = TestClient(app)
    return harness


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


def chat(api, **body):
    body.setdefault("question", "What was the loss in 2020?")
    return api.client.post("/api/v1/chat", json=body)


# --------------------------------------------------------------------------- JSON answers


def test_chat_returns_a_grounded_answer_with_citations_and_sources():
    api = make_api()
    response = chat(api)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == ANSWER and body["found_in_documents"] is True
    assert response.headers["x-conversation-id"] == body["conversation_id"]
    assert [(c["number"], c["document"], c["chunk_id"]) for c in body["citations"]] == [
        (1, "profit_and_loss.txt", "profit_and_loss.txt#2"),
        (2, "profit_and_loss.txt", "profit_and_loss.txt#3"),
    ]
    assert all(c["excerpt"] for c in body["citations"])
    assert [(r["number"], r["cited"]) for r in body["retrieved"]] == [(1, True), (2, True)]
    assert body["provider"] == "primary" and body["model"] == "fm"
    assert body["warnings"] == [] and body["used_fallback"] is False
    assert {"retrieval", "generation", "total"} <= set(body["timings_ms"])


def test_retrieved_passages_that_were_not_cited_are_marked_as_such():
    api = make_api(answer="Only the first matters [1].")
    body = chat(api).json()
    assert [(r["number"], r["cited"]) for r in body["retrieved"]] == [(1, True), (2, False)]


def test_no_relevant_documents_gives_an_honest_answer_without_calling_the_llm():
    api = make_api(chunks=[RetrievedChunk("Unrelated.", "x.txt", 0.01, 0)])
    body = chat(api).json()

    assert body["found_in_documents"] is False and body["citations"] == [] and body["provider"] is None
    assert api.provider.calls == []


def test_request_options_reach_the_pipeline():
    api = make_api()
    chat(api, top_k=2, temperature=0.7, max_tokens=33)
    assert api.retriever.queries[-1][1] == 2
    assert (api.provider.calls[0].temperature, api.provider.calls[0].max_tokens) == (0.7, 33)


# --------------------------------------------------------------------------- conversations


def test_follow_up_questions_and_conversation_history():
    api = make_api()
    first = chat(api).json()
    second = chat(api, question="and for 2023?", conversation_id=first["conversation_id"]).json()

    assert second["conversation_id"] == first["conversation_id"]
    assert second["standalone_query"] == REWRITTEN

    history = api.client.get(f"/api/v1/conversations/{first['conversation_id']}").json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant", "user", "assistant"]
    assert history["messages"][0]["content"] == "What was the loss in 2020?"
    assert [c["number"] for c in history["messages"][1]["citations"]] == [1, 2]
    assert "citations" not in history["messages"][0]


def test_clearing_a_conversation():
    api = make_api()
    conversation_id = chat(api).json()["conversation_id"]

    deleted = api.client.delete(f"/api/v1/conversations/{conversation_id}")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True

    for response in (
        api.client.get(f"/api/v1/conversations/{conversation_id}"),
        api.client.delete(f"/api/v1/conversations/{conversation_id}"),
        chat(api, conversation_id=conversation_id),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "conversation_not_found"


def test_conversation_ids_are_validated_at_the_edge():
    api = make_api()
    assert chat(api, conversation_id="not-a-valid-id").status_code == 422
    assert chat(api, conversation_id="a" * 32).status_code == 404   # well formed but unknown
    assert api.retriever.queries == []


# --------------------------------------------------------------------------- validation and errors


@pytest.mark.parametrize(
    "payload",
    [{}, {"question": ""}, {"question": "x" * 4001}, {"question": "q", "top_k": 0}, {"question": "q", "min_score": 2},
     {"question": "q", "temperature": 3}, {"question": "q", "max_tokens": 0}],
)
def test_invalid_requests_are_rejected_with_422(payload):
    api = make_api()
    assert api.client.post("/api/v1/chat", json=payload).status_code == 422
    assert api.retriever.queries == []


def test_question_longer_than_the_configured_limit_gets_a_structured_error():
    api = make_api()
    response = chat(api, question="x" * 2500)
    assert response.status_code == 422 and response.json()["error"]["code"] == "question_too_long"


def test_index_not_ready_is_a_503():
    api = make_api()
    api.retriever._ready = False
    response = chat(api)
    assert response.status_code == 503 and response.json()["error"]["code"] == "knowledge_base_not_ready"


def test_provider_outage_is_a_503_unless_a_fallback_is_configured():
    down = {"error": ProviderUnavailableError("down", provider="primary")}
    failing = make_api(primary_kwargs=down)
    response = chat(failing)
    assert response.status_code == 503 and response.json()["error"]["code"] == "provider_unavailable"

    backup = FakeProvider("backup", default_model="bm", reply="From backup [1].")
    recovered = make_api(primary_kwargs=down, backup=backup, fallback=True)
    body = chat(recovered).json()
    assert (body["provider"], body["used_fallback"], body["fallback_from"]) == ("backup", True, "primary")
    assert body["answer"] == "From backup [1]."


# --------------------------------------------------------------------------- streaming


def test_streaming_emits_retrieval_start_tokens_and_a_final_event():
    api = make_api()
    response = chat(api, stream=True)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    names = [name for name, _ in events]

    assert names[:2] == ["retrieval", "start"] and names[-1] == "done"
    assert set(names[2:-1]) == {"token"} and len(names) > 4

    retrieval = events[0][1]
    assert [s["number"] for s in retrieval["sources"]] == [1, 2]
    assert all("text" not in s for s in retrieval["sources"])   # passages travel in the final event only
    assert events[1][1]["provider"] == "primary"

    done = events[-1][1]
    assert "".join(d["delta"] for n, d in events if n == "token") == ANSWER
    assert done["answer"] == ANSWER and [c["number"] for c in done["citations"]] == [1, 2]
    assert response.headers["x-conversation-id"] == done["conversation_id"] == retrieval["conversation_id"]


def test_stream_without_relevant_documents_has_no_generation():
    api = make_api(chunks=[RetrievedChunk("Unrelated.", "x.txt", 0.01, 0)])
    events = parse_sse(chat(api, stream=True).text)
    assert [n for n, _ in events] == ["retrieval", "done"]
    assert events[0][1]["found_context"] is False and events[1][1]["found_in_documents"] is False
    assert api.provider.calls == []


def test_stream_failures_before_generation_are_real_http_errors():
    down = make_api(primary_kwargs={"error": ProviderUnavailableError("down", provider="primary")})
    response = chat(down, stream=True)
    assert response.status_code == 503 and response.json()["error"]["code"] == "provider_unavailable"

    unknown = make_api()
    assert chat(unknown, stream=True, conversation_id="b" * 32).status_code == 404

    not_ready = make_api()
    not_ready.retriever._ready = False
    assert chat(not_ready, stream=True).status_code == 503


def test_stream_failure_mid_answer_ends_with_an_error_event():
    api = make_api(primary_kwargs={"error_after_tokens": StreamInterruptedError("dropped", provider="primary")})
    response = chat(api, stream=True)
    events = parse_sse(response.text)

    assert response.status_code == 200
    assert events[-1][0] == "error" and events[-1][1]["code"] == "stream_interrupted"
    assert "done" not in [n for n, _ in events]
    conversation_id = events[0][1]["conversation_id"]
    assert api.store.get(conversation_id).messages == []   # partial answers are not stored


def test_stream_fallback_is_announced_in_the_start_event():
    backup = FakeProvider("backup", default_model="bm", reply="From backup [1].")
    api = make_api(primary_kwargs={"error": ProviderUnavailableError("down", provider="primary")}, backup=backup, fallback=True)
    events = parse_sse(chat(api, stream=True).text)

    start = dict(events)["start"]
    assert (start["provider"], start["used_fallback"], start["fallback_from"]) == ("backup", True, "primary")
    assert dict(events)["done"]["used_fallback"] is True
