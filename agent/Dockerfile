# Anchor — Agent API image.
#
# Build from the repository root so the image can contain both the `agent` and
# `ingestion` packages (they share the vector-store and embedding code):
#   docker build -f agent/Dockerfile -t anchor-agent .
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HF_HOME=/app/models

WORKDIR /app

# tesseract-ocr  : the OCR engine pytesseract shells out to.
# libgomp1       : OpenMP runtime required by onnxruntime (ChromaDB).
# curl           : container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# --extra-index-url selects the CPU-only torch wheel (~200 MB instead of ~2.5 GB).
COPY agent/requirements.txt /app/agent/requirements.txt
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        -r /app/agent/requirements.txt

COPY agent /app/agent
COPY ingestion /app/ingestion

# The schema is owned by Alembic, and a managed platform needs to be able to run
# `alembic upgrade head` from inside the image (Render's preDeployCommand, a
# release step, or a one-off shell). Shipping the migrations but not the
# alembic config — or neither — leaves a deploy with no way to create a schema.
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini

RUN mkdir -p /app/chroma_data /app/data/tickets /app/models

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
