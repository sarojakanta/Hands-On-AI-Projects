"""Ingest institute knowledge documents into the vector store.

Usage:
    python -m scripts.ingest              # ingest all institutes
    python -m scripts.ingest --institute 1
"""
from __future__ import annotations

import argparse

from app.config.logging_config import get_logger
from app.database.session import SessionLocal
from app.models import Institute
from app.rag.ingestion import ingest_institute

logger = get_logger("ingest")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest knowledge documents.")
    parser.add_argument("--institute", type=int, default=None, help="Institute id.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.institute:
            ids = [args.institute]
        else:
            ids = [i.id for i in db.query(Institute).all()]
        if not ids:
            logger.warning("No institutes found. Run the seed script first.")
            return
        for institute_id in ids:
            summary = ingest_institute(db, institute_id)
            logger.info("Institute %d: %s", institute_id, summary)
    finally:
        db.close()


if __name__ == "__main__":
    main()
