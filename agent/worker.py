"""The background ingestion worker.

Extraction, OCR, embedding and indexing take seconds to minutes. Running them
inside the upload request would hold an HTTP connection open for the duration
and make the API look broken, so ``POST /documents`` only validates, stores the
bytes and writes a job row. This process does the rest.

It is a plain polling loop over Postgres rather than a broker like Celery. That
is a deliberate trade: it needs no Redis, no separate scheduler and no
additional service to operate, at the cost of a poll interval's latency. For a
workload of this shape — a few documents an hour, each taking tens of seconds —
the trade is clearly worth it.

Run it as its own process:

    python -m agent.worker

Claiming is a conditional UPDATE rather than ``SELECT ... FOR UPDATE SKIP
LOCKED``, because that is the one primitive that works identically on SQLite
and PostgreSQL. It is still safe to run several workers: only the transaction
whose UPDATE affects a row goes on to process it.
"""

from __future__ import annotations

import contextlib
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agent import audit
from agent.config import get_settings
from agent.db.base import session_scope
from agent.db.models import Document, DocumentStatus, IngestionJob, JobStatus
from agent.observability.logger import get_logger
from agent.storage import StorageError, get_storage

log = get_logger(__name__)


class WorkerStopped(Exception):
    """Raised internally to unwind out of the poll loop on shutdown."""


@dataclass(slots=True)
class _Stop:
    requested: bool = False

    def request(self) -> None:
        self.requested = True


def claim_job(session: Session) -> IngestionJob | None:
    """Take the oldest pending job, or return ``None``.

    The claim is a single conditional UPDATE. If another worker got there first
    the row count is zero and this call reports no work, so two workers polling
    the same table never process the same job.
    """
    pending = session.execute(
        select(IngestionJob.id)
        .where(IngestionJob.status == JobStatus.PENDING)
        .order_by(IngestionJob.created_at)
        .limit(1)
    ).first()
    if pending is None:
        return None

    job_id = pending[0]
    result = session.execute(
        update(IngestionJob)
        .where(IngestionJob.id == job_id, IngestionJob.status == JobStatus.PENDING)
        .values(status=JobStatus.RUNNING, started_at=datetime.now(UTC))
    )
    session.commit()
    if result.rowcount == 0:
        return None

    return session.get(IngestionJob, job_id)


def process_job(session: Session, job: IngestionJob) -> None:
    """Run one ingestion end to end, recording the outcome either way."""
    document = session.get(Document, job.document_id)
    if document is None:
        # The document was deleted between the upload and the worker picking
        # this up. Nothing to do, and nothing worth failing over.
        job.status = JobStatus.SUCCEEDED
        job.finished_at = datetime.now(UTC)
        session.commit()
        return

    document.status = DocumentStatus.PROCESSING
    document.error_message = None
    session.commit()

    try:
        data = get_storage().get(document.storage_key)
    except StorageError as exc:
        _fail(session, job, document, str(exc))
        return

    from ingestion.pipeline import ingest_document

    try:
        result = ingest_document(
            data,
            document.name,
            workspace_id=document.workspace_id,
            document_id=document.id,
        )
    except Exception as exc:
        # Recorded as a string built from the exception, not from any document
        # content: a parse failure message can quote the page that broke.
        _fail(session, job, document, f"{type(exc).__name__}: {exc}"[:500])
        return

    document.status = DocumentStatus.INDEXED
    document.chunks_created = result.chunks_created
    document.pages_processed = result.pages_processed
    document.pages_using_ocr = result.pages_using_ocr
    document.total_tokens = result.total_tokens
    document.ocr_used = result.pages_using_ocr > 0
    document.indexed_at = datetime.now(UTC)
    job.status = JobStatus.SUCCEEDED
    job.finished_at = datetime.now(UTC)
    session.commit()

    audit.record_event(
        session,
        action="document.indexed",
        workspace_id=document.workspace_id,
        user_id=document.uploaded_by_user_id,
        target_type="document",
        target_id=document.id,
        detail={"chunks": result.chunks_created, "pages": result.pages_processed},
    )
    log.info(
        "worker.ingestion_succeeded",
        context={
            "job_id": job.id,
            "document_id": document.id,
            "workspace_id": document.workspace_id,
            "chunks": result.chunks_created,
        },
    )


def _fail(session: Session, job: IngestionJob, document: Document, reason: str) -> None:
    """Mark a job failed, retrying up to the configured limit.

    The first failures of a transient kind — an object store blip, a timeout —
    get another attempt. The last one sticks, so a document that cannot be
    parsed does not spin forever.
    """
    job.attempts += 1
    settings = get_settings()
    retryable = job.attempts < settings.WORKER_MAX_ATTEMPTS

    document.error_message = reason[:500]
    if retryable:
        job.status = JobStatus.PENDING
        document.status = DocumentStatus.QUEUED
        log.warning(
            "worker.ingestion_retry",
            context={"job_id": job.id, "attempt": job.attempts, "reason": reason[:200]},
        )
    else:
        job.status = JobStatus.FAILED
        job.finished_at = datetime.now(UTC)
        document.status = DocumentStatus.FAILED
        audit.record_event(
            session,
            action="document.ingestion_failed",
            workspace_id=document.workspace_id,
            user_id=document.uploaded_by_user_id,
            target_type="document",
            target_id=document.id,
            outcome="failure",
            detail={"reason": reason[:200], "attempts": job.attempts},
        )
        log.error(
            "worker.ingestion_failed",
            context={
                "job_id": job.id,
                "document_id": document.id,
                "workspace_id": document.workspace_id,
                "attempts": job.attempts,
                "reason": reason[:200],
            },
        )
    session.commit()


def run_once() -> int:
    """Process at most one job. Returns 1 if work was done, 0 if idle."""
    with session_scope() as session:
        job = claim_job(session)
        if job is None:
            return 0
        process_job(session, job)
        return 1


def run_forever(stop: _Stop | None = None) -> None:
    """Poll until asked to stop. Used by ``python -m agent.worker``."""
    settings = get_settings()
    shutdown = stop or _Stop()

    def _handle(signum, _frame):  # noqa: ANN001 - signal handler signature
        log.info("worker.signal", context={"signal": int(signum)})
        shutdown.request()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Not every platform defines both; Windows has SIGTERM but the
        # registration can still fail for a signal the runtime lacks.
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(sig, _handle)

    log.info(
        "worker.started",
        context={"poll_seconds": settings.WORKER_POLL_SECONDS, "pid": _pid()},
    )
    idle_polls = 0
    processed = 0
    while not shutdown.requested:
        try:
            did_work = run_once()
        except Exception:
            # A failure processing one job must not take the worker down: the
            # next poll would find it, and the API stays up either way.
            log.error("worker.iteration_failed", exc_info=True)
            did_work = 0

        if did_work:
            # More may be waiting; go straight back rather than sleeping.
            processed += 1
            idle_polls = 0
            continue

        idle_polls += 1
        # Back off to a slower poll when idle, so an empty queue does not mean
        # a query every two seconds for the life of the process.
        delay = settings.WORKER_POLL_SECONDS * min(2 ** min(idle_polls, 4), 15)
        time.sleep(delay)

    log.info("worker.stopped", context={"processed": processed})


def _pid() -> int:
    import os

    return os.getpid()


def _main() -> int:
    from agent.config import get_settings as settings_for_logging
    from agent.observability.logger import configure_logging

    settings = settings_for_logging()
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)
    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
