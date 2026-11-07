"""
RAG orchestration: question -> (rewrite) -> retrieve -> build context -> grounded generation -> citations.

Both ``answer`` (one JSON result) and ``stream`` (incremental events) share the same preparation
code, so they behave identically except for how the text is delivered.

Behaviour worth knowing
* When no retrieved chunk passes the similarity threshold the LLM is NOT called: answering without
  evidence is exactly how hallucinations happen, and it saves a paid request.
* The follow-up rewrite is best effort. If it fails the original question is used and the answer
  carries a ``query_rewrite_failed`` warning.
* Citations shown to the user always refer to chunks that were really in the prompt
  (see ``app.rag.citations``). Whether a citation truly supports a claim is NOT checked here.
* Only complete turns are stored in the conversation; an interrupted stream stores nothing.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.config import Settings
from app.core.errors import ValidationFailedError
from app.llm.base import LLMResult, StreamEvent
from app.llm.errors import LLMError
from app.llm.service import LLMService, StreamStart
from app.rag.citations import resolve_citations
from app.rag.context_builder import build_context, format_context
from app.rag.conversations import (
    ConversationMessage,
    ConversationNotFoundError,
    ConversationStore,
)
from app.rag.prompts import (
    NO_CONTEXT_ANSWER,
    NOT_FOUND_PHRASE,
    build_answer_messages,
    build_rewrite_messages,
    format_history,
    parse_rewrite,
)
from app.rag.retrieval import Retriever
from app.rag.types import BuiltContext, ContextItem, RagAnswer

logger = logging.getLogger("app.rag")

REWRITE_MAX_TOKENS = 120
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# --------------------------------------------------------------------------- configuration


@dataclass(frozen=True)
class RagConfig:
    top_k: int = 5
    min_score: float = 0.25
    max_context_chars: int = 6000
    history_turns: int = 3
    rewrite_enabled: bool = True
    max_question_chars: int = 2000
    lexical_min_coverage: float = 0.6

    @classmethod
    def from_settings(cls, settings: Settings) -> "RagConfig":
        return cls(
            top_k=settings.rag_top_k,
            min_score=settings.rag_min_score,
            max_context_chars=settings.rag_max_context_chars,
            history_turns=settings.rag_history_turns,
            rewrite_enabled=settings.rag_rewrite_enabled,
            max_question_chars=settings.rag_max_question_chars,
            lexical_min_coverage=settings.rag_lexical_min_coverage,
        )


@dataclass
class RagOptions:
    """Per-request overrides. ``None`` means "use the configured value"."""

    top_k: int | None = None
    min_score: float | None = None
    rewrite: bool | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    allow_fallback: bool | None = None


# --------------------------------------------------------------------------- stream events


@dataclass
class RetrievalEvent:
    """Emitted once, before generation: what will be used as evidence."""

    conversation_id: str
    standalone_query: str
    rewrite_failed: bool
    sources: list[ContextItem]
    found_context: bool
    timings_ms: dict[str, float]


@dataclass
class GenerationStart:
    provider: str
    model: str
    used_fallback: bool = False
    fallback_from: str | None = None
    fallback_reason: str | None = None


@dataclass
class TokenEvent:
    delta: str


@dataclass
class AnswerDone:
    """Authoritative final result. Its answer text has invalid citation markers removed."""

    answer: RagAnswer


RagEvent = RetrievalEvent | GenerationStart | TokenEvent | AnswerDone


# --------------------------------------------------------------------------- helpers


def normalize_question(question: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", _CONTROL_CHARS.sub("", question or "")).strip()
    if not text:
        raise ValidationFailedError("Question must not be empty.", code="question_required")
    if len(text) > max_chars:
        raise ValidationFailedError(
            f"Question is too long (maximum {max_chars} characters).", code="question_too_long"
        )
    return text


def says_not_found(answer: str) -> bool:
    """Heuristic: did the model use the agreed "not in the documents" sentence?"""
    normalised = answer.replace("\u2019", "'").lower()
    return NOT_FOUND_PHRASE.lower() in normalised


@dataclass
class _Prepared:
    started: float
    question: str
    conversation_id: str
    standalone_query: str
    rewrite_failed: bool
    context: BuiltContext
    history_text: str
    options: RagOptions
    timings_ms: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def llm_kwargs(self) -> dict[str, Any]:
        o = self.options
        return {
            "provider": o.provider,
            "model": o.model,
            "temperature": o.temperature,
            "max_tokens": o.max_tokens,
            "allow_fallback": o.allow_fallback,
        }


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


# --------------------------------------------------------------------------- pipeline


class RagPipeline:
    def __init__(
        self,
        retriever: Retriever,
        llm: LLMService,
        conversations: ConversationStore,
        config: RagConfig | None = None,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.conversations = conversations
        self.config = config or RagConfig()

    # ---- public API ---------------------------------------------------------

    async def answer(
        self, question: str, *, conversation_id: str | None = None, options: RagOptions | None = None
    ) -> RagAnswer:
        prep = await self._prepare(question, conversation_id, options or RagOptions())
        if prep.context.is_empty:
            return self._finish_without_llm(prep)

        started = time.perf_counter()
        result = await self.llm.generate(self._answer_messages(prep), **prep.llm_kwargs)
        prep.timings_ms["generation"] = _ms(started)
        return self._finish(prep, result.text, result)

    async def stream(
        self, question: str, *, conversation_id: str | None = None, options: RagOptions | None = None
    ) -> AsyncIterator[RagEvent]:
        prep = await self._prepare(question, conversation_id, options or RagOptions())
        yield RetrievalEvent(
            conversation_id=prep.conversation_id,
            standalone_query=prep.standalone_query,
            rewrite_failed=prep.rewrite_failed,
            sources=list(prep.context.items),
            found_context=not prep.context.is_empty,
            timings_ms=dict(prep.timings_ms),
        )
        if prep.context.is_empty:
            yield AnswerDone(self._finish_without_llm(prep))
            return

        parts: list[str] = []
        start: StreamStart | None = None
        final: StreamEvent | None = None
        started = time.perf_counter()
        async for item in self.llm.stream(self._answer_messages(prep), **prep.llm_kwargs):
            if isinstance(item, StreamStart):
                start = item
                yield GenerationStart(
                    provider=item.provider,
                    model=item.model,
                    used_fallback=item.used_fallback,
                    fallback_from=item.fallback_from,
                    fallback_reason=item.fallback_reason,
                )
            elif item.done:
                final = item
            elif item.delta:
                parts.append(item.delta)
                yield TokenEvent(item.delta)
        prep.timings_ms["generation"] = _ms(started)

        text = "".join(parts)
        result = LLMResult(
            text=text,
            provider=start.provider if start else "",
            model=start.model if start else "",
            usage=final.usage if final else None,
            finish_reason=final.finish_reason if final else None,
            used_fallback=start.used_fallback if start else False,
            fallback_from=start.fallback_from if start else None,
            fallback_reason=start.fallback_reason if start else None,
        )
        yield AnswerDone(self._finish(prep, text, result))

    # ---- preparation --------------------------------------------------------

    async def _prepare(self, question: str, conversation_id: str | None, options: RagOptions) -> _Prepared:
        cfg = self.config
        started = time.perf_counter()
        text = normalize_question(question, cfg.max_question_chars)

        if conversation_id is None:
            conversation_id = self.conversations.create().id
            history: list[ConversationMessage] = []
        else:
            history = self.conversations.recent(conversation_id, cfg.history_turns * 2)   # raises if unknown

        prep = _Prepared(
            started=started,
            question=text,
            conversation_id=conversation_id,
            standalone_query=text,
            rewrite_failed=False,
            context=BuiltContext(),
            history_text=format_history(history) if history else "",
            options=options,
        )

        want_rewrite = cfg.rewrite_enabled if options.rewrite is None else options.rewrite
        if want_rewrite and history:
            await self._rewrite(prep)

        top_k = options.top_k or cfg.top_k
        retrieval_started = time.perf_counter()
        chunks = await self.retriever.retrieve(prep.standalone_query, top_k)
        prep.timings_ms["retrieval"] = _ms(retrieval_started)

        prep.context = build_context(
            chunks,
            min_score=cfg.min_score if options.min_score is None else options.min_score,
            max_chars=cfg.max_context_chars,
            max_items=top_k,
            lexical_min_coverage=cfg.lexical_min_coverage,
        )
        prep.warnings.extend(getattr(chunks, "warnings", ()))
        if prep.context.dropped_budget or prep.context.truncated_first:
            prep.warnings.append("context_truncated")
        return prep

    async def _rewrite(self, prep: _Prepared) -> None:
        """Turn a follow-up into a standalone question. Failure is non-fatal and reported."""
        started = time.perf_counter()
        kwargs = {**prep.llm_kwargs, "temperature": 0.0, "max_tokens": REWRITE_MAX_TOKENS}
        try:
            result = await self.llm.generate(
                build_rewrite_messages(prep.question, prep.history_text), **kwargs
            )
            rewritten = parse_rewrite(result.text, prep.question)
        except LLMError as exc:
            logger.warning("rag_rewrite_failed code=%s", exc.code)
            rewritten = None
        prep.timings_ms["rewrite"] = _ms(started)

        if rewritten is None:
            prep.rewrite_failed = True
            prep.warnings.append("query_rewrite_failed")
        else:
            prep.standalone_query = rewritten

    def _answer_messages(self, prep: _Prepared):
        return build_answer_messages(
            prep.question, format_context(prep.context), prep.history_text, prep.standalone_query
        )

    # ---- completion -----------------------------------------------------------

    def _finish_without_llm(self, prep: _Prepared) -> RagAnswer:
        logger.info(
            "rag_answer llm_called=false retrieved=0 dropped_low_score=%d question_chars=%d",
            prep.context.dropped_low_score,
            len(prep.question),
        )
        return self._store_and_build(
            prep,
            answer_text=NO_CONTEXT_ANSWER,
            found=False,
            citations=[],
            result=None,
        )

    def _finish(self, prep: _Prepared, text: str, result: LLMResult) -> RagAnswer:
        resolution = resolve_citations(text, prep.context)
        answer_text = resolution.text.strip()
        found = not says_not_found(answer_text)

        if not answer_text:
            answer_text = "The model returned an empty answer. Please try again."
            found = False
            prep.warnings.append("empty_answer")
        if resolution.invalid_numbers:
            prep.warnings.append("invalid_citations_removed")
        if found and not resolution.citations:
            prep.warnings.append("no_valid_citations")
        if result.finish_reason == "length":
            prep.warnings.append("answer_truncated")

        logger.info(
            "rag_answer llm_called=true provider=%s model=%s context_chunks=%d citations=%d "
            "fallback=%s warnings=%s generation_ms=%s",
            result.provider,
            result.model,
            len(prep.context.items),
            len(resolution.citations),
            result.used_fallback,
            ",".join(prep.warnings) or "-",
            prep.timings_ms.get("generation"),
        )
        return self._store_and_build(
            prep,
            answer_text=answer_text,
            found=found,
            citations=resolution.citations,
            result=result,
        )

    def _store_and_build(self, prep, *, answer_text, found, citations, result: LLMResult | None) -> RagAnswer:
        assistant = ConversationMessage(
            role="assistant",
            content=answer_text,
            metadata={
                "found_in_documents": found,
                "citations": [asdict(c) for c in citations],
            },
        )
        try:
            self.conversations.append(
                prep.conversation_id,
                [ConversationMessage(role="user", content=prep.question), assistant],
            )
        except ConversationNotFoundError:   # evicted while we were working: the answer is still valid
            logger.warning("rag_conversation_evicted")

        prep.timings_ms["total"] = _ms(prep.started)
        return RagAnswer(
            conversation_id=prep.conversation_id,
            message_id=assistant.id,
            question=prep.question,
            standalone_query=prep.standalone_query,
            answer=answer_text,
            found_in_documents=found,
            citations=citations,
            retrieved=list(prep.context.items),
            provider=result.provider if result else None,
            model=result.model if result else None,
            usage=result.usage if result else None,
            finish_reason=result.finish_reason if result else None,
            used_fallback=result.used_fallback if result else False,
            fallback_from=result.fallback_from if result else None,
            fallback_reason=result.fallback_reason if result else None,
            attempts=result.attempts if result else 1,
            rewrite_failed=prep.rewrite_failed,
            timings_ms=dict(prep.timings_ms),
            warnings=list(prep.warnings),
        )
