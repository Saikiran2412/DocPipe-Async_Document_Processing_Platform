from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import Job, JobStatus, utcnow
from app.services.events import publish_job_event

import logging

from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)

TERMINAL_STATES = {JobStatus.COMPLETED, JobStatus.FAILED}


def _enqueue_webhook(job_id) -> None:
    try:
        celery_app.send_task("deliver_webhook", args=[str(job_id)])
    except Exception:
        log.warning("could not enqueue webhook for job %s", job_id, exc_info=True)

# state -> set of states it may move to
ALLOWED: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING_UPLOAD: {JobStatus.QUEUED},
    JobStatus.QUEUED: {JobStatus.PROCESSING, JobStatus.FAILED},
    JobStatus.PROCESSING: {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.RETRYING},
    JobStatus.RETRYING: {JobStatus.PROCESSING, JobStatus.FAILED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: {JobStatus.QUEUED},
}


def transition(db: Session, job_id, to: JobStatus, **fields) -> bool:
    sources = [s.value for s, targets in ALLOWED.items() if to in targets]
    stmt = (
        update(Job)
        .where(Job.id == job_id, Job.status.in_(sources))
        .values(status=to.value, updated_at=utcnow(), **fields)
        .execution_options(synchronize_session=False)
    )
    result = db.execute(stmt)
    db.commit()
    changed = result.rowcount == 1
    if changed:
        publish_job_event(db, job_id)
        if to in TERMINAL_STATES:
            _enqueue_webhook(job_id)
    return changed

def update_progress(db: Session, job_id, stage: str, progress: int) -> None:
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.PROCESSING.value)
        .values(stage=stage, progress=progress, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if result.rowcount == 1:
        publish_job_event(db, job_id)
