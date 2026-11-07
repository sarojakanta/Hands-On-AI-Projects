# syntax=docker/dockerfile:1
FROM python:3.12-slim

ARG WITH_POSTGRES=0
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf \
    DATA_DIR=/data \
    SEED_DIR=/app/sample_docs

WORKDIR /app

# CPU-only PyTorch first: the default wheel pulls gigabytes of CUDA libraries that this app never uses.
COPY requirements.txt requirements-postgres.txt ./
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt \
    && if [ "$WITH_POSTGRES" = "1" ]; then pip install -r requirements-postgres.txt; fi

# Bake the embedding model into the image so the container starts (and works) without internet access.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# From here on the model is read from the image only. Without this, every start tries to reach huggingface.co
# and stalls on retries when the container has no internet. Set HF_HUB_OFFLINE=0 to use RAG_RERANKER=cross-encoder
# (its model is not baked in and must be downloaded).
ENV HF_HUB_OFFLINE=1

COPY app ./app
COPY static ./static
COPY sample_docs ./sample_docs
COPY eval ./eval
COPY main.py rag_engine.py ./

# Run as an unprivileged user; /data holds the database, uploads and the vector index.
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser /data /opt/hf
USER appuser
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4).status == 200 else 1)"

# One worker: the embedding model and the embedded Chroma index live in this process.
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
