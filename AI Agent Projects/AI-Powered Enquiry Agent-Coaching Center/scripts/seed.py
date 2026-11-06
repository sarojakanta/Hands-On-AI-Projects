"""Seed the BizAssist database with dummy data.

Usage:
    python -m scripts.seed            # create tables + seed (idempotent-ish)
    python -m scripts.seed --reset    # drop everything first, then seed

This script seeds institutes, admins, courses, batches and registers the
knowledge-base documents found on disk. Vector ingestion is handled
separately in Phase 4 (scripts/ingest.py).
"""
from __future__ import annotations

import argparse
import os
from datetime import timedelta

from app.config import get_settings
from app.config.logging_config import get_logger
from app.database.base import utcnow
from app.database.init_db import create_all, drop_all
from app.database.session import SessionLocal
from app.auth.security import hash_password
from app.models import (
    AdminUser,
    Batch,
    Course,
    Institute,
    KnowledgeDocument,
)
from sample_data import DEMO_ADMIN_PASSWORD, INSTITUTES

logger = get_logger("seed")
settings = get_settings()


def _seed_institute(db, data: dict) -> Institute:
    """Create one institute with its admin, courses, batches and docs."""
    institute = Institute(
        name=data["name"],
        slug=data["slug"],
        api_key=data["api_key"],
        contact_email=data.get("contact_email"),
        contact_phone=data.get("contact_phone"),
        address=data.get("address"),
        description=data.get("description"),
        is_active=True,
    )
    db.add(institute)
    db.flush()  # assign institute.id

    # --- Admin user ---
    admin_data = data["admin"]
    admin = AdminUser(
        institute_id=institute.id,
        email=admin_data["email"],
        full_name=admin_data.get("full_name"),
        role=admin_data.get("role", "admin"),
        hashed_password=hash_password(DEMO_ADMIN_PASSWORD),
        is_active=True,
    )
    db.add(admin)

    # --- Courses + batches ---
    for course_data in data.get("courses", []):
        course = Course(
            institute_id=institute.id,
            name=course_data["name"],
            slug=course_data["slug"],
            description=course_data.get("description"),
            fee=course_data.get("fee", 0.0),
            currency=course_data.get("currency", "INR"),
            duration=course_data.get("duration"),
            eligibility=course_data.get("eligibility"),
            mode=course_data.get("mode"),
            is_active=True,
        )
        db.add(course)
        db.flush()  # assign course.id

        for batch_data in course_data.get("batches", []):
            start = utcnow() + timedelta(days=batch_data.get("start_offset_days", 14))
            end = start + timedelta(days=batch_data.get("duration_days", 90))
            db.add(
                Batch(
                    course_id=course.id,
                    name=batch_data["name"],
                    start_date=start,
                    end_date=end,
                    timing=batch_data.get("timing"),
                    seats_total=batch_data.get("seats_total", 0),
                    seats_available=batch_data.get("seats_available", 0),
                    is_active=True,
                )
            )

    # --- Knowledge documents (register rows; ingestion is Phase 4) ---
    kb_dir = os.path.join(settings.knowledge_base_dir, institute.slug)
    if os.path.isdir(kb_dir):
        for filename in sorted(os.listdir(kb_dir)):
            path = os.path.join(kb_dir, filename)
            if not os.path.isfile(path):
                continue
            db.add(
                KnowledgeDocument(
                    institute_id=institute.id,
                    filename=filename,
                    stored_path=path,
                    content_type="text/plain",
                    title=filename.rsplit(".", 1)[0].replace("_", " ").title(),
                    status="pending",
                    chunk_count=0,
                )
            )
        logger.info("Registered knowledge documents from %s", kb_dir)
    else:
        logger.warning("No knowledge-base directory found at %s", kb_dir)

    return institute


def seed(reset: bool = False) -> None:
    """Run the seed. If reset=True, drop all tables first."""
    if reset:
        drop_all()
    create_all()

    db = SessionLocal()
    try:
        existing = db.query(Institute).count()
        if existing and not reset:
            logger.info(
                "Database already has %d institute(s); skipping seed. "
                "Use --reset to reseed.",
                existing,
            )
            return

        for data in INSTITUTES:
            inst = _seed_institute(db, data)
            logger.info("Seeded institute: %s (api_key=%s)", inst.name, inst.api_key)

        db.commit()
        logger.info("Seed complete.")
        logger.info(
            "Demo admin login -> email: %s  password: %s",
            INSTITUTES[0]["admin"]["email"],
            DEMO_ADMIN_PASSWORD,
        )
    except Exception:
        db.rollback()
        logger.exception("Seeding failed; rolled back.")
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the BizAssist database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop all tables before seeding (destructive).",
    )
    args = parser.parse_args()
    seed(reset=args.reset)


if __name__ == "__main__":
    main()
