"""Shared builders for RAG tests: a fake retriever and a pipeline wired to fake LLM providers."""

from __future__ import annotations

from types import SimpleNamespace

from app.llm.registry import ProviderRegistry
from app.llm.service import LLMRuntimeConfig, LLMService
from app.rag.conversations import InMemoryConversationStore
from app.rag.pipeline import RagConfig, RagPipeline
from app.rag.retrieval import KnowledgeBaseNotReadyError
from app.rag.types import RetrievedChunk
from tests.helpers import FakeProvider

ANSWER = "Revenue was $11.8 million [1] and the loss was $2.8 million [1][2]."
REWRITTEN = "What was the profit or loss in 2023?"

CHUNKS = [
    RetrievedChunk("In 2020, the company reported revenue of $11.8 million and a net loss of $2.8 million.",
                   "profit_and_loss.txt", 0.82, 2),
    RetrievedChunk("The 2020 loss was the largest in the period and was caused by pandemic disruption.",
                   "profit_and_loss.txt", 0.61, 3),
    RetrievedChunk("Python is a high-level programming language.", "python_programming.txt", 0.12, 0),
]


class FakeRetriever:
    def __init__(self, chunks=None, ready=True):
        self.chunks = list(CHUNKS if chunks is None else chunks)
        self._ready = ready
        self.queries: list[tuple[str, int]] = []

    @property
    def is_ready(self):
        return self._ready

    async def retrieve(self, query, top_k):
        if not self._ready:
            raise KnowledgeBaseNotReadyError("not ready")
        self.queries.append((query, top_k))
        return list(self.chunks)


def is_rewrite(messages):
    return messages[0].content.startswith("You rewrite")


def build(answer=ANSWER, rewrite=REWRITTEN, *, chunks=None, config=None, primary_kwargs=None, backup=None,
          fallback=False, extra_providers=()):
    """Create a pipeline whose LLM answers with `answer` and rewrites follow-ups to `rewrite`."""

    def reply(messages):
        if is_rewrite(messages):
            if isinstance(rewrite, Exception):
                raise rewrite
            return rewrite
        return answer

    primary = FakeProvider("primary", reply_fn=reply, **(primary_kwargs or {}))
    providers = [primary, *([backup] if backup else []), *extra_providers]
    llm_config = LLMRuntimeConfig(
        provider="primary",
        model="fm",
        fallback_enabled=fallback,
        fallback_provider=backup.name if backup else "",
    )
    llm = LLMService(ProviderRegistry(providers), llm_config)
    retriever = FakeRetriever(chunks)
    store = InMemoryConversationStore()
    pipeline = RagPipeline(retriever, llm, store, config or RagConfig())
    return SimpleNamespace(pipeline=pipeline, retriever=retriever, provider=primary, store=store, llm=llm)


def answer_prompt(provider):
    """The user message of the most recent non-rewrite call."""
    for messages in reversed(provider.all_messages):
        if not is_rewrite(messages):
            return messages[1].content
    raise AssertionError("no answer call recorded")
