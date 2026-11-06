"""Ingest the knowledge base into the RAG indexes (R-1..R-7).

Builds the customer-safe index and the separate staff-only index, carrying full
chunk metadata. A full rebuild retires superseded policy versions (R-6).

Usage (from project root, Windows PowerShell):
    python scripts/ingest_kb.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import config  # noqa: E402
from app.rag.indexer import build_all  # noqa: E402


def main() -> None:
    config.ensure_dirs()
    print("Building RAG indexes (customer-safe + staff-only)...")
    counts = build_all()
    print("Ingest complete.")
    print(f"  customer-safe chunks: {counts['customer_chunks']}")
    print(f"  staff-only chunks:    {counts['staff_chunks']}")


if __name__ == "__main__":
    main()
