"""Database initialisation helpers."""
from __future__ import annotations

from app.config.logging_config import get_logger
from app.database.base import Base
from app.database.session import engine

# Importing the models package registers all tables on Base.metadata.
import app.models  # noqa: F401

logger = get_logger(__name__)


def create_all() -> None:
    """Create all tables that do not yet exist."""
    logger.info("Creating database tables (if not present)...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready.")


def drop_all() -> None:
    """Drop all tables. Destructive — used by tests and reseeding."""
    logger.warning("Dropping all database tables...")
    Base.metadata.drop_all(bind=engine)
