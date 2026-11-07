"""
SQLAlchemy models. The relational database is the system of record: documents, their versions
and chunks, conversations and application settings. A vector index can be rebuilt from this.

Design notes
* A ``Document`` has many ``DocumentVersion`` rows; ``active_version_id`` names the one answers use.
  Keeping old versions makes re-index and rollback a metadata change, not a destructive one.
* ``Chunk`` rows mirror what is stored in the vector index, so the index can be rebuilt and so
  citations resolve to stable ids without reading the vector store.
* Timestamps are stored as timezone-aware UTC.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    """ISO-8601 UTC string. SQLite returns naive datetimes; they were stored as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- documents


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255))                              # display name, as uploaded
    name_key: Mapped[str] = mapped_column(String(255), unique=True)             # lower-cased: names are case-insensitive
    source_type: Mapped[str] = mapped_column(String(32), default="upload")      # upload | seed
    # Plain column (no foreign key): documents and versions reference each other, and the service flips
    # this value inside a single transaction. A foreign key cycle would complicate SQLite for no gain.
    active_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentVersion.version_number",
    )


class DocumentVersion(Base, TimestampMixin):
    """
    One ingest of a document's content.

    status: processing -> ready (searchable) | failed; a previous ready version becomes superseded
    when a newer one is activated.
    reason: why this version was created (upload | update | reindex).
    """

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_version_number"),
        Index("ix_version_doc_status", "document_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version_number: Mapped[int] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(16), default="processing")
    reason: Mapped[str] = mapped_column(String(16), default="upload")

    content_hash: Mapped[str] = mapped_column(String(64))            # sha256 of the raw bytes
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    file_extension: Mapped[str] = mapped_column(String(16), default=".txt")
    stored_path: Mapped[str | None] = mapped_column(Text, nullable=True)   # original file on disk

    embedding_provider: Mapped[str] = mapped_column(String(64), default="")
    embedding_model: Mapped[str] = mapped_column(String(128), default="")
    embedding_dimension: Mapped[int] = mapped_column(Integer, default=0)
    collection: Mapped[str] = mapped_column(String(128), default="")
    chunk_size: Mapped[int] = mapped_column(Integer, default=0)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=0)
    chunk_strategy: Mapped[str] = mapped_column(String(16), default="sentence", server_default="sentence")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped["Document"] = relationship(back_populates="versions")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="Chunk.ordinal"
    )


class Chunk(Base):
    """A passage of a version. ``vector_id`` is the id of the matching record in the vector store."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="uq_chunk_ordinal"),
        Index("ix_chunk_version", "version_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)          # position within the version, 0-based
    vector_id: Mapped[str] = mapped_column(String(64))     # id in the vector store
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)

    version: Mapped["DocumentVersion"] = relationship(back_populates="chunks")


# --------------------------------------------------------------------------- conversations


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.sequence"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_message_sequence"),
        Index("ix_message_conversation", "conversation_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)         # monotonic order within the conversation
    role: Mapped[str] = mapped_column(String(16))          # user | assistant
    content: Mapped[str] = mapped_column(Text)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON: citations, flags
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


# --------------------------------------------------------------------------- settings


class AppSetting(Base, TimestampMixin):
    """Small key/value store for persisted runtime settings (e.g. the active LLM configuration)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
