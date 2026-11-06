"""Pytest fixtures for BizAssist.

Everything runs offline and for free:
  * The LLM is forced to the deterministic MockLLMClient (USE_MOCK_LLM=true).
  * Embeddings use the local HashingEmbedder.
  * Each test session gets an isolated temp SQLite DB and temp Chroma dir.

Environment variables MUST be set before importing app modules, because
settings are cached and the DB engine / vector store are created at import
time. We therefore configure the environment at the very top of this file.
"""
from __future__ import annotations

import os
import tempfile

# --- Configure an isolated test environment BEFORE importing the app -------
_TMP = tempfile.mkdtemp(prefix="bizassist_test_")
os.environ["USE_MOCK_LLM"] = "true"
os.environ["OPENAI_API_KEY"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ["CHROMA_PERSIST_DIR"] = os.path.join(_TMP, "chroma")
os.environ["KNOWLEDGE_BASE_DIR"] = os.path.join(_TMP, "kb")
os.environ["PUBLIC_CHAT_RATE_LIMIT"] = "1000/minute"  # effectively off for most tests
os.environ["SECRET_KEY"] = "test-secret-key"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth.security import hash_password  # noqa: E402
from app.database.init_db import create_all, drop_all  # noqa: E402
from app.database.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AdminUser,
    Batch,
    Course,
    Institute,
)
from app.rag.vector_store import get_vector_store  # noqa: E402

ADMIN_EMAIL = "admin@test.example"
ADMIN_PASSWORD = "test-pass-123"
API_KEY_A = "test-key-institute-a"
API_KEY_B = "test-key-institute-b"


@pytest.fixture(scope="session", autouse=True)
def _db_schema():
    """Create all tables once for the test session."""
    drop_all()
    create_all()
    yield
    drop_all()


@pytest.fixture(autouse=True)
def _clean_state(_db_schema):
    """Reset DB rows and vector store between every test for isolation."""
    # Clear tables (keep schema).
    db = SessionLocal()
    try:
        for model in (Batch, Course, AdminUser):
            db.query(model).delete()
        # Order matters for FK-less SQLite but we delete children first anyway.
        from app.models import (
            ChatMessage,
            Conversation,
            Escalation,
            FollowUp,
            KnowledgeDocument,
            StudentLead,
        )

        for model in (
            ChatMessage,
            Escalation,
            FollowUp,
            Conversation,
            StudentLead,
            KnowledgeDocument,
            Institute,
        ):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()
    # Reset vectors.
    try:
        get_vector_store().reset()
    except Exception:
        pass
    yield


@pytest.fixture
def db():
    """A database session for direct DB assertions in tests."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_institute(session, name, slug, api_key) -> Institute:
    inst = Institute(
        name=name, slug=slug, api_key=api_key, is_active=True, contact_email="x@y.z"
    )
    session.add(inst)
    session.flush()
    return inst


def _make_courses(session, institute_id):
    py = Course(
        institute_id=institute_id,
        name="Python Programming",
        slug="python-programming",
        description="Beginner Python course.",
        fee=15000.0,
        duration="3 months",
        eligibility="No prior coding needed.",
        mode="offline",
        is_active=True,
    )
    java = Course(
        institute_id=institute_id,
        name="Java Development",
        slug="java-development",
        description="Core and advanced Java.",
        fee=20000.0,
        duration="4 months",
        mode="offline",
        is_active=True,
    )
    session.add_all([py, java])
    session.flush()
    session.add(
        Batch(
            course_id=py.id,
            name="PY-Morning",
            timing="Mon-Fri 9-10:30 AM",
            seats_total=30,
            seats_available=10,
            is_active=True,
        )
    )
    session.flush()
    return py, java


@pytest.fixture
def institute(db):
    """Seed a primary institute (A) with an admin + courses + a batch."""
    inst = _make_institute(db, "Test Academy A", "test-a", API_KEY_A)
    db.add(
        AdminUser(
            institute_id=inst.id,
            email=ADMIN_EMAIL,
            full_name="Test Admin",
            hashed_password=hash_password(ADMIN_PASSWORD),
            role="admin",
            is_active=True,
        )
    )
    _make_courses(db, inst.id)
    db.commit()
    db.refresh(inst)
    return inst


@pytest.fixture
def institute_b(db):
    """Seed a second institute (B) for tenant-isolation tests."""
    inst = _make_institute(db, "Test Institute B", "test-b", API_KEY_B)
    db.add(
        Course(
            institute_id=inst.id,
            name="Banking Preparation",
            slug="banking-preparation",
            fee=12000.0,
            duration="5 months",
            is_active=True,
        )
    )
    db.commit()
    db.refresh(inst)
    return inst


@pytest.fixture
def client():
    """A TestClient for the app."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_token(client, institute):
    """Log in as the seeded admin and return a bearer token."""
    res = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


@pytest.fixture
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}
