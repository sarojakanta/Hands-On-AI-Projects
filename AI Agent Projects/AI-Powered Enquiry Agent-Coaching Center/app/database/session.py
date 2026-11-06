"""Database engine and session management.

Uses SQLAlchemy 2.0 style. SQLite for the MVP, but the engine URL is
driven entirely by configuration so switching to PostgreSQL is a
one-line change in the environment.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()


def _make_engine():
    """Create the SQLAlchemy engine with dialect-appropriate options."""
    url = _settings.database_url
    connect_args: dict = {}
    if url.startswith("sqlite"):
        # Needed so a single SQLite connection can be shared across threads
        # (FastAPI serves requests from a threadpool).
        connect_args = {"check_same_thread": False}
    return create_engine(
        url,
        echo=False,
        future=True,
        connect_args=connect_args,
    )


engine = _make_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
