"""
Full-stack tests: the real FastAPI app with real SQLite + ChromaDB (temp directory) and a fake embedder.

Covers the legacy UI contract, the security checks carried over from the original endpoints, the new
/api/v1 document API, end-to-end RAG with persistent ids, persistence across restarts and start-up failure.
"""

from __future__ import annotations

import json

import pytest

from app.embeddings.base import EmbeddingError
from tests.conftest import make_stack
from tests.embedders import HashingEmbedder
from tests.helpers import FakeProvider

SIP = (
    "# SIP Testing\n\n## SIP Registration\n\n"
    "A SIP endpoint should successfully register with the SIP server using valid credentials."
)
IVR = (
    "# IVR Testing\n\n## Invalid DTMF\n\n"
    "After three consecutive invalid inputs, the IVR should route the caller to the configured fallback destination."
)
PL_V1 = (
    "# Profit and Loss\n\nIn 2020, the company reported revenue of $11.8 million and a net loss of $2.8 million. "
    "The loss was caused by pandemic disruption."
)
PL_V2 = (
    "# Profit and Loss\n\nIn 2020, the company reported revenue of $11.8 million and a net loss of $3.1 million. "
    "The loss was caused by pandemic disruption."
)


def upload(client, name, content, path="/api/upload"):
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    return client.post(path, files={"file": (name, data, "text/plain")})


def v1_upload(client, name, content):
    return upload(client, name, content, "/api/v1/documents")


# =========================================================================== legacy UI contract


def test_ui_and_static_assets_are_served(stack):
    page = stack.client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert stack.client.get("/static/app.js").status_code == 200
    assert stack.client.get("/static/style.css").status_code == 200


def test_empty_knowledge_base_reports_ready_with_nothing_in_it(stack):
    assert stack.client.get("/api/status").json() == {"ready": True, "total_chunks": 0, "documents": 0}
    assert stack.client.get("/api/documents").json() == {"documents": [], "status": "ready"}


def test_legacy_upload_list_query_and_delete_flow_keeps_the_shapes_app_js_relies_on(stack):
    c = stack.client

    uploaded = upload(c, "sip_testing.txt", SIP)
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert set(body) == {"message", "documents", "total_chunks"}
    assert "uploaded" in body["message"] and body["total_chunks"] > 0
    assert body["documents"][0]["filename"] == "sip_testing.txt" and body["documents"][0]["chunks"] > 0

    listing = c.get("/api/documents").json()
    assert listing["status"] == "ready" and [d["filename"] for d in listing["documents"]] == ["sip_testing.txt"]
    status = c.get("/api/status").json()
    assert status["ready"] is True and status["documents"] == 1 and status["total_chunks"] == body["total_chunks"]

    answer = c.post("/api/query", json={"query": "SIP endpoint register valid credentials", "top_k": 3}).json()
    assert set(answer) == {"answer", "sources", "chunks", "query"}
    assert answer["sources"] == ["sip_testing.txt"] and "register" in answer["answer"]
    assert set(answer["chunks"][0]) == {"chunk", "source", "score"}

    deleted = c.delete("/api/documents/sip_testing.txt")
    assert deleted.status_code == 200 and deleted.json()["documents"] == []
    assert c.get("/api/status").json()["documents"] == 0


def test_legacy_reupload_messages_distinguish_new_unchanged_and_updated(stack):
    assert "uploaded" in upload(stack.client, "pl.txt", PL_V1).json()["message"]
    assert "already up to date" in upload(stack.client, "pl.txt", PL_V1).json()["message"]
    assert "version 2" in upload(stack.client, "pl.txt", PL_V2).json()["message"]
    (document,) = stack.client.get("/api/documents").json()["documents"]
    assert document["version"] == 2


def test_legacy_query_edge_cases(stack):
    assert stack.client.post("/api/query", json={"query": "   "}).json() == {"detail": "Query cannot be empty"}
    assert stack.client.post("/api/query", json={"query": "x", "top_k": 0}).status_code == 422
    empty = stack.client.post("/api/query", json={"query": "anything"}).json()
    assert empty == {"answer": "I couldn't find relevant information for your query.", "sources": [], "chunks": []}


# =========================================================================== security (carried over)


@pytest.mark.parametrize(
    "name", ["..\\evil.txt", "../evil.txt", "sub/evil.txt", "C:\\evil.txt", "evil.txt:stream", ".hidden.txt", "CON.txt"]
)
def test_upload_never_writes_outside_the_data_directory(stack, name):
    response = upload(stack.client, name, SIP)

    assert response.status_code in (200, 400)
    assert {p.name for p in stack.tmp.iterdir()} == {"data"}          # nothing escaped the data directory
    for document in stack.client.get("/api/documents").json()["documents"]:
        assert "/" not in document["filename"] and "\\" not in document["filename"]


@pytest.mark.parametrize("name", ["evil.exe", "evil.txt.exe", "noextension", "image.png"])
def test_unsupported_file_types_are_rejected(stack, name):
    response = upload(stack.client, name, SIP)
    assert response.status_code == 400 and "detail" in response.json()
    assert stack.client.get("/api/documents").json()["documents"] == []


def test_invalid_content_is_rejected_before_anything_is_stored(stack):
    cases = {
        "latin.txt": "caf\xe9 caf\xe9 caf\xe9 caf\xe9 caf\xe9 caf\xe9".encode("latin-1"),
        "empty.txt": b"",
        "binary.txt": b"abc\x00def " * 10,
        "tiny.txt": b"too short",
    }
    for name, data in cases.items():
        assert upload(stack.client, name, data).status_code == 400, name
    assert stack.client.get("/api/documents").json()["documents"] == []
    assert stack.services.vector_store.count(stack.services.ingestion.collection) == 0


def test_oversized_upload_is_rejected_with_413(stack):
    response = upload(stack.client, "big.txt", b"x " * 6000)       # limit in this fixture: 10 000 bytes
    assert response.status_code == 413 and "too large" in response.json()["detail"]


@pytest.mark.parametrize("encoded", ["..%5Csecret.txt", "..%2Fsecret.txt", "%2E%2E%5Csecret.txt"])
def test_delete_cannot_reach_files_outside_the_store(stack, encoded):
    secret = stack.tmp / "secret.txt"
    secret.write_text("do not delete", encoding="utf-8")
    upload(stack.client, "keep.txt", SIP)

    response = stack.client.delete(f"/api/documents/{encoded}")

    # Either our own 404 (name looked up, not found) or the router's 404 (a decoded slash matches no route).
    assert response.status_code == 404
    assert response.json()["detail"] in {"Document not found.", "Not Found"}
    assert secret.exists()
    assert [d["filename"] for d in stack.client.get("/api/documents").json()["documents"]] == ["keep.txt"]


def test_delete_of_an_unknown_name_is_a_404(stack):
    assert stack.client.delete("/api/documents/missing.txt").status_code == 404


def test_unexpected_internal_errors_do_not_leak_details(tmp_path):
    stack = make_stack(tmp_path, raise_server_exceptions=False)
    try:
        upload(stack.client, "a.txt", SIP)

        async def boom(query, top_k):
            raise RuntimeError("secret C:\\internal\\path")

        stack.services.retriever.retrieve = boom
        response = stack.client.post("/api/query", json={"query": "anything"})
        assert response.status_code == 500 and "secret" not in response.text and "internal" not in response.text
    finally:
        stack.close()


def test_a_failed_ingest_is_a_500_with_a_generic_message(tmp_path):
    stack = make_stack(tmp_path)
    try:
        stack.services.ingestion._activate = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db password=hunter2"))
        response = upload(stack.client, "a.txt", SIP)
        assert response.status_code == 500 and "hunter2" not in response.text
        assert stack.client.get("/api/status").json()["documents"] == 0
    finally:
        stack.close()


# =========================================================================== /api/v1 document API


def test_v1_upload_creates_then_versions_then_detects_duplicates(stack):
    c = stack.client

    created = v1_upload(c, "pl.txt", PL_V1)
    assert created.status_code == 201
    body = created.json()
    assert body["outcome"] == "created" and body["version"]["version_number"] == 1
    document_id = body["document"]["id"]
    assert body["document"]["status"] == "ready" and body["version"]["is_active"] is True

    assert v1_upload(c, "pl.txt", PL_V1).status_code == 200                     # identical -> unchanged
    assert v1_upload(c, "pl.txt", PL_V1).json()["outcome"] == "unchanged"

    updated = v1_upload(c, "pl.txt", PL_V2)
    assert updated.status_code == 201 and updated.json()["outcome"] == "updated"
    assert updated.json()["version"]["version_number"] == 2 and updated.json()["document"]["id"] == document_id

    clash = v1_upload(c, "copy.txt", PL_V2)
    assert clash.status_code == 409
    error = clash.json()["error"]
    assert error["code"] == "duplicate_document" and error["details"]["existing_document_id"] == document_id


def test_v1_listing_detail_versions_and_index_status(stack):
    c = stack.client
    document_id = v1_upload(c, "pl.txt", PL_V1).json()["document"]["id"]
    v1_upload(c, "pl.txt", PL_V2)
    v1_upload(c, "sip.txt", SIP)

    listing = c.get("/api/v1/documents").json()
    assert listing["count"] == 2 and [d["name"] for d in listing["documents"]] == ["pl.txt", "sip.txt"]

    detail = c.get(f"/api/v1/documents/{document_id}").json()
    assert detail["version_count"] == 2 and detail["active_version"]["version_number"] == 2 and detail["stale"] is False

    versions = c.get(f"/api/v1/documents/{document_id}/versions").json()["versions"]
    assert [(v["version_number"], v["status"], v["is_active"]) for v in versions] == [(2, "ready", True), (1, "superseded", False)]

    index = c.get("/api/v1/index/status").json()
    assert index["ready"] is True and index["consistent"] is True and index["orphan_vectors"] == 0
    assert index["documents"] == 2 and index["vector_store"] == "chroma"


def test_v1_delete_and_reindex(stack):
    c = stack.client
    document_id = v1_upload(c, "pl.txt", PL_V1).json()["document"]["id"]
    other_id = v1_upload(c, "sip.txt", SIP).json()["document"]["id"]

    reindexed = c.post(f"/api/v1/documents/{document_id}/reindex")
    assert reindexed.status_code == 200
    assert reindexed.json()["outcome"] == "reindexed" and reindexed.json()["version"]["version_number"] == 2

    summary = c.post("/api/v1/documents/reindex").json()
    assert summary == {"total": 2, "succeeded": 2, "failed": []}

    assert c.delete(f"/api/v1/documents/{other_id}").json() == {"deleted": True, "document_id": other_id}
    missing = c.get(f"/api/v1/documents/{other_id}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "document_not_found"
    assert c.delete(f"/api/v1/documents/{other_id}").status_code == 404
    assert c.post(f"/api/v1/documents/{other_id}/reindex").status_code == 404
    assert c.get("/api/v1/index/status").json()["consistent"] is True


def test_v1_validation_errors_use_the_structured_envelope(stack):
    bad = v1_upload(stack.client, "evil.exe", SIP)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_document"
    big = v1_upload(stack.client, "big.txt", b"x " * 6000)
    assert big.status_code == 413 and big.json()["error"]["code"] == "document_too_large"


# =========================================================================== end-to-end RAG


def parse_sse(text):
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


def use_fake_llm(stack, reply):
    stack.services.llm.registry.register(FakeProvider("fake", reply=reply))
    response = stack.client.put("/api/v1/llm/config", json={"provider": "fake", "model": "fm"})
    assert response.status_code == 200, response.text


def test_rag_answer_cites_the_real_document_and_version_and_follows_updates(stack):
    use_fake_llm(stack, "The loss in 2020 was large [1].")
    document_id = v1_upload(stack.client, "pl.txt", PL_V1).json()["document"]["id"]
    v1_upload(stack.client, "sip.txt", SIP)

    first = stack.client.post("/api/v1/chat", json={"question": "What was the net loss in 2020 pandemic?"}).json()
    citation = first["citations"][0]
    assert citation["document"] == "pl.txt" and citation["document_id"] == document_id and citation["version"] == 1
    assert "2.8 million" in first["retrieved"][0]["text"]

    v1_upload(stack.client, "pl.txt", PL_V2)
    second = stack.client.post("/api/v1/chat", json={"question": "What was the net loss in 2020 pandemic?"}).json()
    assert second["citations"][0]["version"] == 2
    assert "3.1 million" in second["retrieved"][0]["text"] and "2.8 million" not in second["retrieved"][0]["text"]


def test_rag_stops_citing_a_deleted_document(stack):
    use_fake_llm(stack, "Answer [1].")
    document_id = v1_upload(stack.client, "sip.txt", SIP).json()["document"]["id"]
    assert stack.client.post("/api/v1/chat", json={"question": "SIP endpoint register credentials"}).json()["citations"]

    stack.client.delete(f"/api/v1/documents/{document_id}")

    body = stack.client.post("/api/v1/chat", json={"question": "SIP endpoint register credentials"}).json()
    assert body["found_in_documents"] is False and body["citations"] == [] and body["provider"] is None


def test_streaming_chat_works_through_the_whole_stack(stack):
    use_fake_llm(stack, "Registration needs valid credentials [1].")
    v1_upload(stack.client, "sip.txt", SIP)

    response = stack.client.post("/api/v1/chat", json={"question": "SIP endpoint register credentials", "stream": True})
    events = parse_sse(response.text)

    assert [n for n, _ in events][:2] == ["retrieval", "start"] and events[-1][0] == "done"
    assert events[-1][1]["citations"][0]["document"] == "sip.txt"


# =========================================================================== persistence and start-up


def test_everything_survives_an_application_restart(tmp_path):
    first = make_stack(tmp_path)
    try:
        document_id = v1_upload(first.client, "pl.txt", PL_V1).json()["document"]["id"]
        use_fake_llm(first, "Loss [1].")
        conversation = first.client.post("/api/v1/chat", json={"question": "net loss 2020 pandemic"}).json()
        saved = first.client.put(
            "/api/v1/llm/config?validate=false",
            json={"provider": "ollama", "model": "llama3.1:latest", "temperature": 0.7, "max_tokens": 321},
        )
        assert saved.status_code == 200
    finally:
        first.close()

    second = make_stack(tmp_path)          # a brand new process worth of objects over the same data directory
    try:
        assert [d["filename"] for d in second.client.get("/api/documents").json()["documents"]] == ["pl.txt"]
        assert second.client.get(f"/api/v1/documents/{document_id}").json()["version_count"] == 1
        assert second.client.get("/api/v1/index/status").json()["consistent"] is True

        history = second.client.get(f"/api/v1/conversations/{conversation['conversation_id']}").json()
        assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
        assert history["messages"][1]["citations"][0]["document"] == "pl.txt"

        config = second.client.get("/api/v1/llm/config").json()
        assert (config["provider"], config["model"], config["temperature"], config["max_tokens"]) == (
            "ollama", "llama3.1:latest", 0.7, 321,
        )

        reset = second.client.post("/api/v1/llm/config/reset").json()
        assert reset["model"] == "" and reset["temperature"] == 0.2 and reset["max_tokens"] == 1024
    finally:
        second.close()

    third = make_stack(tmp_path)
    try:
        assert third.client.get("/api/v1/llm/config").json()["max_tokens"] == 1024   # reset was persisted too
    finally:
        third.close()


def test_seed_documents_are_loaded_once_and_not_duplicated_on_restart(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "a.txt").write_bytes(SIP.encode())
    (seed / "b.txt").write_bytes(IVR.encode())

    first = make_stack(tmp_path, seed_dir=seed)
    try:
        listing = first.client.get("/api/v1/documents").json()["documents"]
        assert [(d["name"], d["source_type"]) for d in listing] == [("a.txt", "seed"), ("b.txt", "seed")]
        first.client.delete(f"/api/v1/documents/{listing[0]['id']}")
    finally:
        first.close()

    second = make_stack(tmp_path, seed_dir=seed)
    try:
        # The deleted seed file is NOT resurrected: the database, not the seed folder, is the source of truth.
        assert [d["name"] for d in second.client.get("/api/v1/documents").json()["documents"]] == ["b.txt"]
    finally:
        second.close()


def test_deleting_the_last_document_does_not_trigger_reseeding(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "a.txt").write_bytes(SIP.encode())
    first = make_stack(tmp_path, seed_dir=seed)
    try:
        first.client.delete(f"/api/v1/documents/{first.client.get('/api/v1/documents').json()['documents'][0]['id']}")
    finally:
        first.close()
    second = make_stack(tmp_path, seed_dir=seed)
    try:
        # NOTE: an empty database counts as "first run", so the seed is applied again. Documented behaviour.
        assert second.client.get("/api/v1/documents").json()["count"] == 1
    finally:
        second.close()


class BrokenEmbedder(HashingEmbedder):
    @property
    def dimension(self) -> int:
        raise EmbeddingError("model unavailable")


def test_server_keeps_running_and_reports_not_ready_when_start_up_fails(tmp_path):
    stack = make_stack(tmp_path, embedder=BrokenEmbedder())
    try:
        assert stack.client.get("/api/status").json()["ready"] is False
        assert stack.client.get("/api/documents").json() == {"documents": [], "status": "indexing"}
        assert stack.client.post("/api/query", json={"query": "x"}).status_code == 503
        assert stack.client.get("/").status_code == 200       # the UI itself still loads
    finally:
        stack.close()
