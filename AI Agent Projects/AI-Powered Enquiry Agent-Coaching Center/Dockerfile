# BizAssist — production-oriented image.
FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files / buffering stdout.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: build tools are needed by some wheels (chromadb/onnxruntime).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the application.
COPY app ./app
COPY sample_data ./sample_data
COPY scripts ./scripts
COPY knowledge_base ./knowledge_base
COPY .env.example ./.env.example

# Create a non-root user and give it ownership.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/chroma_store /app/knowledge_base/uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Seed + ingest on first boot (idempotent), then launch the API.
# docker-compose overrides this with a volume-aware entrypoint.
CMD ["sh", "-c", "python -m scripts.seed && python -m scripts.ingest && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

# Basic container healthcheck.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1
