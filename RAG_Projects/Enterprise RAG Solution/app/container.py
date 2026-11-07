"""
Application services, built once from settings.

Everything that holds state (database, vector index, embedding model, LLM providers) is created here and
shared through ``app.state.services``. Tests build the same container with a fake embedder and temp
directories, so the full stack can be exercised without downloading a model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import anyio.to_thread

from app.core.config import Settings
from app.db.conversation_store import SqlConversationStore
from app.db.session import Database
from app.db.settings_repo import SettingsRepository
from app.embeddings.base import EmbeddingProvider
from app.embeddings.factory import create_embedder
from app.ingestion.chunking import ChunkingConfig
from app.ingestion.file_store import FileStore
from app.ingestion.service import IngestionService
from app.llm.persistence import KeyValuePersistence
from app.llm.service import LLMService, build_llm_service
from app.rag.pipeline import RagConfig, RagPipeline
from app.rag.reranker import create_reranker
from app.rag.store_retriever import StoreRetriever
from app.vectorstore.base import VectorStore
from app.vectorstore.factory import create_vector_store

logger = logging.getLogger("app")


@dataclass
class AppServices:
    settings: Settings
    db: Database
    embedder: EmbeddingProvider
    vector_store: VectorStore
    files: FileStore
    ingestion: IngestionService
    retriever: StoreRetriever
    settings_repo: SettingsRepository
    conversations: SqlConversationStore
    llm: LLMService
    rag: RagPipeline

    def start(self) -> None:
        """Blocking start-up work (loads the embedding model, recovers, seeds). Call from a worker thread."""
        self.ingestion.start()
        if self.settings.seed_sample_docs:
            self.ingestion.seed_from_directory(self.settings.seed_path)

    async def aclose(self) -> None:
        await self.llm.registry.aclose()          # provider HTTP connections
        await anyio.to_thread.run_sync(self._close_storage)

    def _close_storage(self) -> None:
        try:
            self.vector_store.close()
        finally:
            self.db.dispose()


def build_services(
    settings: Settings,
    *,
    embedder: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> AppServices:
    settings.data_path.mkdir(parents=True, exist_ok=True)

    db = Database(settings.database_dsn)
    db.create_all()   # idempotent; also needed before the saved LLM configuration can be read

    embedder = embedder or create_embedder(settings)
    vector_store = vector_store or create_vector_store(settings)
    files = FileStore(settings.upload_path)

    ingestion = IngestionService(
        db,
        vector_store,
        embedder,
        files,
        ChunkingConfig(settings.chunk_size, settings.chunk_overlap, settings.chunk_strategy),
        max_upload_bytes=settings.max_upload_bytes,
    )
    retriever = StoreRetriever(
        ingestion,
        vector_store,
        embedder,
        mode=settings.rag_retrieval_mode,
        candidate_multiplier=settings.rag_candidate_multiplier,
        rrf_k=settings.rag_rrf_k,
        reranker=create_reranker(settings.rag_reranker, settings.rag_reranker_model),
    )

    settings_repo = SettingsRepository(db)
    conversations = SqlConversationStore(db, settings.rag_max_conversations)
    llm = build_llm_service(settings, persistence=KeyValuePersistence(settings_repo))
    rag = RagPipeline(retriever, llm, conversations, RagConfig.from_settings(settings))

    return AppServices(
        settings=settings,
        db=db,
        embedder=embedder,
        vector_store=vector_store,
        files=files,
        ingestion=ingestion,
        retriever=retriever,
        settings_repo=settings_repo,
        conversations=conversations,
        llm=llm,
        rag=rag,
    )
