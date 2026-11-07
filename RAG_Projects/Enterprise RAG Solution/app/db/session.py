"""
Database engine and session management (synchronous SQLAlchemy).

The pipeline runs blocking store/LLM work in worker threads already, so a synchronous session
used inside ``anyio.to_thread`` keeps the data layer simple and avoids a second async driver.
``session_scope`` gives a transaction that commits on success and rolls back on error.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

logger = logging.getLogger("app.db")


def create_database_engine(dsn: str) -> Engine:
    is_sqlite = dsn.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(dsn, future=True, connect_args=connect_args, pool_pre_ping=not is_sqlite)

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):   # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")        # enforce ON DELETE CASCADE
            cursor.execute("PRAGMA journal_mode=WAL")       # concurrent readers during a write
            cursor.execute("PRAGMA busy_timeout=5000")      # wait rather than fail on a brief lock
            cursor.close()

    return engine


class Database:
    def __init__(self, dsn: str) -> None:
        self.engine = create_database_engine(dsn)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """
        Minimal forward-only upgrade: ``create_all`` never alters an existing table, so columns added in
        later releases are added here (nullable or with a server default only). Real migrations (Alembic)
        are a documented gap.
        """
        inspector = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    continue
                existing = {c["name"] for c in inspector.get_columns(table.name)}
                for column in table.columns:
                    if column.name in existing:
                        continue
                    if not column.nullable and column.server_default is None:
                        raise RuntimeError(f"Cannot add required column {table.name}.{column.name} without a default.")
                    ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(self.engine.dialect)}"
                    if column.server_default is not None:
                        ddl += f" DEFAULT {column.server_default.arg!r}" if isinstance(column.server_default.arg, str) else ""
                    if not column.nullable:
                        ddl += " NOT NULL"
                    conn.execute(text(ddl))
                    logger.info("db_column_added table=%s column=%s", table.name, column.name)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
