import json
import logging

import redis

from app.config import settings
from app.db.models import Job, JobStatus
from app.schemas.job import JobOut

log = logging.getLogger(__name__)

TERMINAL = {JobStatus.COMPLETED.value, JobStatus.FAILED.value}

_redis = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)


def channel_for(job_id) -> str:
    return f"job:{job_id}"


def snapshot(db, job_id) -> dict | None:
    """Current job state as a JSON-ready dict, read fresh from the database."""
    job = db.get(Job, job_id, populate_existing=True)
    if job is None:
        return None
    return JobOut.model_validate(job).model_dump(mode="json")


def publish_job_event(db, job_id) -> None:
    """Publish the job's current state. Never raises: a Redis outage must not fail a job."""
    try:
        snap = snapshot(db, job_id)
        if snap is not None:
            _redis.publish(channel_for(job_id), json.dumps(snap))
    except Exception:
        log.warning("could not publish event for job %s", job_id, exc_info=True)