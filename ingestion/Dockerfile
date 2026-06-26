# Anchor — Ingestion worker image.
#
# A batch worker: it walks data/documents, runs PDF -> text/OCR -> chunk ->
# embed -> ChromaDB, and exits. It is not a long-running HTTP service; the
# interactive upload path lives on the Agent API (POST /ingest).
#
#   docker build -f ingestion/Dockerfile -t anchor-ingestion .
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HF_HOME=/app/models

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY ingestion/requirements.txt /app/ingestion/requirements.txt
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        -r /app/ingestion/requirements.txt

COPY ingestion /app/ingestion
COPY agent /app/agent

RUN mkdir -p /app/chroma_data /app/data/documents /app/data/tickets /app/models

CMD ["python", "-m", "ingestion.main"]
