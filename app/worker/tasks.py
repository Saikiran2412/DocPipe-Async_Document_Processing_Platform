import logging
import random
import uuid

from celery.exceptions import Reject

from app.db.models import JobStatus, utcnow
from app.db.session import SessionLocal
from app.services.job_service import transition
from app.worker.celery_app import celery_app
from app.worker.pipeline import run_pipeline

from sqlalchemy import update

from app.db.models import Job
from app.services.events import TERMINAL, snapshot
from app.services.webhooks import WebhookRejected, WebhookRetryable, build_request, post_webhook


log = logging.getLogger(__name__)

MAX_RETRIES = 5
BACKOFF_BASE = 2      # seconds
BACKOFF_CAP = 60      # seconds


def backoff_delay(retries: int) -> float:
    """Exponential backoff with full jitter."""
    return random.uniform(0, min(BACKOFF_CAP, BACKOFF_BASE * 2 ** retries))


def _give_up(job_id: str, exc: Exception) -> None:
    db = SessionLocal()
    try:
        transition(
            db, uuid.UUID(job_id), JobStatus.FAILED,
            error=f"gave up after {MAX_RETRIES + 1} attempts: {exc}",
            finished_at=utcnow(),
        )
    finally:
        db.close()


@celery_app.task(name="process_job", bind=True, max_retries=MAX_RETRIES)
def process_job(self, job_id: str) -> None:
    redelivered = bool((self.request.delivery_info or {}).get("redelivered"))
    try:
        run_pipeline(job_id, redelivered=redelivered)
    except Exception as exc:                       # only transient errors reach here
        if self.request.retries >= self.max_retries:
            _give_up(job_id, exc)
            raise Reject(exc, requeue=False)       # -> dead-letter queue
        delay = backoff_delay(self.request.retries)
        log.warning("job %s transient failure, retry %d in %.1fs: %s",
                    job_id, self.request.retries + 1, delay, exc)
        raise self.retry(exc=exc, countdown=delay)

WEBHOOK_MAX_RETRIES = 5


def _set_webhook(db, job_id, **fields) -> None:
    db.execute(
        update(Job).where(Job.id == job_id).values(**fields)
        .execution_options(synchronize_session=False)
    )
    db.commit()


@celery_app.task(name="deliver_webhook", bind=True, max_retries=WEBHOOK_MAX_RETRIES)
def deliver_webhook(self, job_id: str) -> None:
    jid = uuid.UUID(job_id)
    db = SessionLocal()
    try:
        job = db.get(Job, jid, populate_existing=True)
        if job is None or not job.webhook_url or job.webhook_status == "DELIVERED":
            return
        if job.status not in TERMINAL:
            return

        event = "job.completed" if job.status == JobStatus.COMPLETED.value else "job.failed"
        delivery_id = f"{job.id}.{event}"
        payload = {"event": event, "delivery_id": delivery_id, "job": snapshot(db, jid)}
        url = job.webhook_url

        _set_webhook(db, jid, webhook_attempts=Job.webhook_attempts + 1)
        try:
            body, headers = build_request(event, delivery_id, payload)
            post_webhook(url, body, headers)
        except WebhookRejected as exc:
            _set_webhook(db, jid, webhook_status="FAILED", webhook_error=str(exc))
            log.error("webhook for job %s rejected: %s", job_id, exc)
            return
        except WebhookRetryable as exc:
            if self.request.retries >= self.max_retries:
                _set_webhook(db, jid, webhook_status="FAILED", webhook_error=f"gave up: {exc}")
                log.error("webhook for job %s gave up: %s", job_id, exc)
                return
            _set_webhook(db, jid, webhook_status="RETRYING", webhook_error=str(exc))
            delay = backoff_delay(self.request.retries)
            log.warning("webhook for job %s failed (%s), retry %d in %.1fs",
                        job_id, exc, self.request.retries + 1, delay)
            raise self.retry(exc=exc, countdown=delay)

        _set_webhook(db, jid, webhook_status="DELIVERED", webhook_error=None)
        log.info("webhook for job %s delivered", job_id)
    finally:
        db.close()