"""Index building + auto re-index (R-6, R-7).

Builds the customer-safe and staff-only indexes from their KB directories. On a
full rebuild the collections are reset, so superseded policy versions are
retired and can never be returned (R-6). A lightweight ``maybe_reindex`` checks
the newest file mtime against the last index time and the REINDEX_INTERVAL so a
long-running process picks up changes within the window.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.config import config
from app.rag.ingestion import build_chunks
from app.rag.vector_store import vector_store, staff_vector_store
from app.rag.bm25_store import bm25_store, staff_bm25_store

# Tracks the last successful index time per KB directory.
_last_indexed: dict[str, float] = {}


def _newest_mtime(directory: str) -> float:
    p = Path(directory)
    if not p.exists():
        return 0.0
    times = [f.stat().st_mtime for f in p.iterdir() if f.is_file()]
    return max(times) if times else 0.0


def build_customer_index() -> int:
    """Rebuild the customer-safe index (policies + team knowledge).

    Merges the main knowledge base with the team-knowledge directory (approved
    edge-case answers). Both are customer-safe. Returns chunk count. Retires old
    data by resetting the collection (R-6).
    """
    chunks = build_chunks(config.KNOWLEDGE_BASE_DIR, customer_safe_only=True)
    if Path(config.TEAM_KB_DIR).exists():
        chunks += build_chunks(config.TEAM_KB_DIR, customer_safe_only=True)
    vector_store.reset()
    vector_store.add_chunks(chunks)
    bm25_store.build(chunks)
    _last_indexed[config.KNOWLEDGE_BASE_DIR] = time.time()
    return len(chunks)


def build_staff_index() -> int:
    """Rebuild the staff-only index from the staff KB directory (R-7)."""
    staff_dir = config.STAFF_KB_DIR
    if not Path(staff_dir).exists():
        return 0
    chunks = build_chunks(staff_dir, customer_safe_only=None)
    staff_vector_store.reset()
    staff_vector_store.add_chunks(chunks)
    staff_bm25_store.build(chunks)
    _last_indexed[staff_dir] = time.time()
    return len(chunks)


def build_all() -> dict[str, int]:
    return {
        "customer_chunks": build_customer_index(),
        "staff_chunks": build_staff_index(),
    }


def maybe_reindex(force: bool = False) -> bool:
    """Reindex the customer KB if it changed and the interval elapsed (R-6).

    Returns True if a reindex ran. A production deployment would run this on a
    scheduler (e.g. every minute) with REINDEX_INTERVAL_SECONDS as the SLA.
    """
    kb = config.KNOWLEDGE_BASE_DIR
    last = _last_indexed.get(kb, 0.0)
    changed = _newest_mtime(kb) > last
    due = (time.time() - last) >= config.REINDEX_INTERVAL_SECONDS
    if force or (changed and (due or last == 0.0)):
        build_customer_index()
        return True
    return False
