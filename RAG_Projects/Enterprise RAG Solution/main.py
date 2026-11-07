"""
Entry point: ``uvicorn main:app --port 8000``.

All application logic lives in the ``app`` package; see ``app.main.create_app``.
(The original single-file backend was replaced by the persistent, versioned document store; its in-memory
FAISS engine is kept in ``rag_engine.py`` for reference only and is no longer used.)
"""

from app.main import create_app

app = create_app()
