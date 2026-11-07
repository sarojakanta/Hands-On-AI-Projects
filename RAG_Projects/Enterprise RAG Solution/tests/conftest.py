"""Shared pytest fixtures."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run anyio-marked async tests on asyncio only (trio is not a dependency)."""
    return "asyncio"


def make_stack(tmp_path, *, seed_dir=None, embedder=None, raise_server_exceptions=True, **settings_overrides):
    """
    Boot the REAL application (factory, routers, SQLite, ChromaDB) in a temp directory with a fake embedder,
    so tests exercise the full stack without downloading a model. Returns a namespace; call ``close()`` when done.
    """
    from fastapi.testclient import TestClient

    from app.container import build_services
    from app.core.config import Settings
    from app.main import create_app
    from tests.embedders import HashingEmbedder

    values = {
        "data_dir": tmp_path / "data",
        "seed_sample_docs": seed_dir is not None,
        "max_upload_bytes": 10_000,
        "log_level": "WARNING",
    }
    if seed_dir is not None:
        values["seed_dir"] = seed_dir
    values.update(settings_overrides)

    settings = Settings(_env_file=None, **values)
    services = build_services(settings, embedder=embedder or HashingEmbedder())
    app = create_app(settings, services)
    client = TestClient(app, raise_server_exceptions=raise_server_exceptions)
    client.__enter__()   # runs the lifespan: starts the ingestion service and seeds
    return SimpleNamespace(
        client=client, services=services, settings=settings, app=app, tmp=tmp_path,
        close=lambda: client.__exit__(None, None, None),
    )


@pytest.fixture
def stack(tmp_path):
    instance = make_stack(tmp_path)
    yield instance
    instance.close()
