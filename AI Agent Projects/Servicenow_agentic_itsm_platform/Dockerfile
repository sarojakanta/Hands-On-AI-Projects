# syntax=docker/dockerfile:1
# ---- build stage: resolve and install dependencies into an isolated venv ----
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- runtime stage: no build tooling, non-root user ----
FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production LOG_JSON=true CHROMA_DIR=/app/data/chroma KNOWLEDGE_DIR=/app/data/knowledge
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app app ./app
COPY --chown=app:app dashboard ./dashboard
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app data/knowledge ./data/knowledge
RUN mkdir -p /app/data/chroma /app/data/knowledge/uploads && chown -R app:app /app/data
USER app
EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/ready', timeout=4)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
