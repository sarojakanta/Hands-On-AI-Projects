"""SQLAlchemy engine, session factory, and declarative base.

This module centralises database access. The engine is created lazily from
the application config so tests can point it at a different database.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, scoped_session

from app.config import config


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Engine + session are created once and reused. ``check_same_thread`` is
# disabled so the SQLite connection can be shared across Flask request
# threads safely for this demo workload.
_engine = create_engine(
    config.sqlalchemy_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False}
    if config.sqlalchemy_url.startswith("sqlite")
    else {},
)

SessionLocal = scoped_session(
    sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
)


def get_engine():
    """Return the shared SQLAlchemy engine."""
    return _engine


def get_session():
    """Return a scoped session instance."""
    return SessionLocal()


def init_db() -> None:
    """Create all tables. Importing models registers them on ``Base``."""
    config.ensure_dirs()
    # Import models so they are registered with the declarative Base before
    # create_all runs. Imported here to avoid circular imports at module load.
    from app.models import (  # noqa: F401
        customer,
        order,
        shipment,
        store,
        ticket,
        otp_challenge,
        address_change,
        customer_memory,
    )

    Base.metadata.create_all(bind=_engine)


def reset_db() -> None:
    """Drop and recreate all tables. Used by tests and fresh seeding."""
    from app.models import (  # noqa: F401
        customer,
        order,
        shipment,
        store,
        ticket,
        otp_challenge,
        address_change,
        customer_memory,
    )

    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
