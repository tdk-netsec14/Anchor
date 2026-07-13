# Anchor — Evaluation harness image.
#
# Runs the regression suite against an already-running Agent API:
#   docker build -f eval/Dockerfile -t anchor-eval .
#   docker run --rm --network host \
#     -e ANCHOR_BASE_URL=http://localhost:8000 anchor-eval
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HF_HOME=/app/models \
    ANCHOR_BASE_URL=http://agent:8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY eval/requirements.txt /app/eval/requirements.txt
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        -r /app/eval/requirements.txt

COPY eval /app/eval

CMD ["python", "eval/run_eval.py"]
