import asyncio
import json
import logging
import uuid
from datetime import timedelta

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select, update

from app.db.models import Job, JobStatus, utcnow
from app.db.session import SessionLocal
from app.services.job_service import transition
from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)

SWEEP_INTERVAL = 30       # seconds between sweeps
STALE_QUEUED = 60         # QUEUED this long: the message was probably lost
STALE_RETRYING = 180      # RETRYING normally waits at most about 60 s
STALE_PROCESSING = 900    # no progress for 15 minutes: worker presumed dead
STALE_WEBHOOK = 60        # finished job whose webhook never started
BACKLOG = 50              # a deep queue means slow workers, not lost messages


def queue_depths() -> dict:
    out = {"jobs": None, "jobs.dead": None}
    for name in out:
        try:
            with celery_app.connection_for_read() as conn:
                out[name] = conn.channel().queue_declare(queue=name, passive=True).message_count
        except Exception:
            out[name] = None
    return out


def _touch(db, job_id) -> None:
    db.execute(update(Job).where(Job.id == job_id).values(updated_at=utcnow())
               .execution_options(synchronize_session=False))
    db.commit()


def sweep_once() -> dict:
    out = {"requeued": 0, "recovered": 0, "webhooks": 0, "skipped": False}
    depth = queue_depths()["jobs"]
    if depth is not None and depth >= BACKLOG:
        out["skipped"] = True
        log.warning("sweeper skipped: queue depth %s (workers are behind)", depth)
        return out
    now = utcnow()
    db = SessionLocal()
    try:
        queued = db.scalars(select(Job.id).where(
            Job.status == JobStatus.QUEUED.value,
            Job.updated_at < now - timedelta(seconds=STALE_QUEUED))).all()
        retrying = db.scalars(select(Job.id).where(
            Job.status == JobStatus.RETRYING.value,
            Job.updated_at < now - timedelta(seconds=STALE_RETRYING))).all()
        for jid in [*queued, *retrying]:
            _touch(db, jid)
            celery_app.send_task("process_job", args=[str(jid)])
            out["requeued"] += 1

        stalled = db.scalars(select(Job.id).where(
            Job.status == JobStatus.PROCESSING.value,
            Job.updated_at < now - timedelta(seconds=STALE_PROCESSING))).all()
        for jid in stalled:
            if transition(db, jid, JobStatus.RETRYING, error="stalled; recovered by sweeper"):
                celery_app.send_task("process_job", args=[str(jid)])
                out["recovered"] += 1

        hooks = db.scalars(select(Job.id).where(
            Job.status.in_([JobStatus.COMPLETED.value, JobStatus.FAILED.value]),
            Job.webhook_url.is_not(None), Job.webhook_status == "PENDING",
            Job.finished_at < now - timedelta(seconds=STALE_WEBHOOK))).all()
        for jid in hooks:
            celery_app.send_task("deliver_webhook", args=[str(jid)])
            out["webhooks"] += 1
    finally:
        db.close()
    if out["requeued"] or out["recovered"] or out["webhooks"]:
        log.warning("sweeper: %s", out)
    return out


async def sweeper_loop() -> None:
    while True:
        await asyncio.sleep(SWEEP_INTERVAL)
        try:
            await run_in_threadpool(sweep_once)
        except Exception:
            log.warning("sweeper failed", exc_info=True)


def retry_job(job_id: uuid.UUID) -> str:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return "missing"
        if job.status != JobStatus.FAILED.value:
            return "not_failed"
        ok = transition(
            db, job_id, JobStatus.QUEUED,
            error=None, result=None, attempts=0, stage=None, progress=0,
            started_at=None, finished_at=None, webhook_attempts=0, webhook_error=None,
            webhook_status="PENDING" if job.webhook_url else None,
        )
        if not ok:
            return "conflict"
    finally:
        db.close()
    celery_app.send_task("process_job", args=[str(job_id)])
    return "ok"


def replay_dead_letters(limit: int = 100) -> dict:
    replayed = skipped = 0
    with celery_app.connection_for_write() as conn:
        ch = conn.channel()
        for _ in range(limit):
            msg = ch.basic_get(queue="jobs.dead", no_ack=False)
            if msg is None:
                break
            try:
                job_id = uuid.UUID(json.loads(msg.body)[0][0])
                result = retry_job(job_id)
            except Exception:
                log.warning("unreadable dead letter dropped", exc_info=True)
                result = "bad"
            if result == "ok":
                replayed += 1
            else:
                skipped += 1
            ch.basic_ack(msg.delivery_tag)
    return {"replayed": replayed, "skipped": skipped}


def get_stats() -> dict:
    db = SessionLocal()
    try:
        by_status = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        hooks = dict(db.execute(select(Job.webhook_status, func.count())
                                .where(Job.webhook_url.is_not(None))
                                .group_by(Job.webhook_status)).all())
    finally:
        db.close()
    return {"jobs": by_status, "webhooks": hooks, "queues": queue_depths()}
