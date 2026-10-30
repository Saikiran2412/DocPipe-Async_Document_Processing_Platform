import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Job
from app.db.session import get_db
from app.services import events, maintenance, storage
from app.worker.celery_app import celery_app

router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    checks = {}

    def check(name, fn):
        try:
            fn()
            checks[name] = True
        except Exception:
            checks[name] = False

    def broker():
        with celery_app.connection_for_read() as conn:
            conn.ensure_connection(max_retries=1)

    check("database", lambda: db.execute(text("select 1")))
    check("redis", lambda: events._redis.ping())
    check("storage", lambda: storage._client.head_bucket(Bucket=settings.s3_bucket))
    check("broker", broker)
    ok = all(checks.values())
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ok" if ok else "degraded", "checks": checks})


@router.get("/admin/stats")
def stats():
    return maintenance.get_stats()


@router.post("/admin/sweep")
def sweep():
    return maintenance.sweep_once()


@router.post("/admin/dead-letters/replay")
def replay():
    return maintenance.replay_dead_letters()


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry(job_id: uuid.UUID):
    result = maintenance.retry_job(job_id)
    if result == "missing":
        raise HTTPException(404, "Job not found")
    if result != "ok":
        raise HTTPException(409, "Only FAILED jobs can be retried")
    return {"job_id": str(job_id), "status": "QUEUED"}


@router.get("/jobs/{job_id}/download/{name}")
def download(job_id: uuid.UUID, name: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    key = ((job.result or {}).get("outputs") or {}).get(name) if job else None
    if not key:
        raise HTTPException(404, "Output not found")
    return RedirectResponse(storage.presigned_download_url(key), status_code=307)
