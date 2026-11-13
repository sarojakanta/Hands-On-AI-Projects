"""Database engine and session management.

SQLite is used for local development and tests; PostgreSQL in production.
The engine is created lazily and can be re-pointed with ``configure_database``
so tests can use an isolated in-memory database.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def normalize_url(url: str) -> str:
    """Map plain postgresql:// URLs to the psycopg v3 driver we ship."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def _build_engine(url: str) -> Engine:
    url = normalize_url(url)
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool
        else:
            path = url.split("///", 1)[-1]
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_pragma(dbapi_conn, _record):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def configure_database(url: str | None = None, create_tables: bool = False) -> Engine:
    """(Re)configure the global engine and return it."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = _build_engine(url or settings.database_url)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    if create_tables:
        init_db()
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure_database(create_tables=settings.auto_create_tables)
    assert _engine is not None
    return _engine


def init_db() -> None:
    """Create tables from metadata (dev/test). Production uses Alembic migrations."""
    from app.db import models  # noqa: F401  (registers models on Base.metadata)

    Base.metadata.create_all(get_engine())


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with session_scope() as session:
        yield session
