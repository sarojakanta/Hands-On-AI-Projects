# syntax=docker/dockerfile:1

# ---- Base image ----
FROM python:3.12-slim AS base

# Keep Python output unbuffered and skip .pyc files for cleaner container logs.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=5000

WORKDIR /app

# ---- Dependencies ----
# Copy only the metadata first so dependency install is cached independently
# of source changes.
COPY pyproject.toml README.md ./
COPY src ./src

# Install the package plus a production WSGI server.
RUN pip install --upgrade pip \
    && pip install . gunicorn

# ---- Runtime ----
# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 5000

# A light healthcheck against the homepage.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/').read()" || exit 1

# Serve with gunicorn. The app factory is created via a small import expression.
# 2 workers x 4 threads handles the I/O-bound LLM/API calls without heavy CPU.
CMD ["sh", "-c", "gunicorn --bind ${WEB_HOST}:${WEB_PORT} --workers 2 --threads 4 --timeout 120 'multi_agent_assistant.web:create_app()'"]
