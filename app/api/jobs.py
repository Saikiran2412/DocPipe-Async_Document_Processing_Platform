import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, JobStatus
from app.db.session import get_db
from app.schemas.job import JobCreate, JobCreated, JobOut
from app.services import storage
from app.worker.tasks import process_job
from app.services.job_service import transition
from app.services.webhooks import validate_webhook_url



router = APIRouter(prefix="/jobs", tags=["jobs"])
URL_TTL = 900


@router.post("", response_model=JobCreated, status_code=201)
def create_job(body: JobCreate, db: Session = Depends(get_db)):
    job_id = uuid.uuid4()
    safe_name = os.path.basename(body.filename)          # strip any path tricks
    if body.webhook_url:
        try:
            validate_webhook_url(body.webhook_url) 
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    job = Job(
        id=job_id,
        filename=safe_name,
        content_type=body.content_type,
        object_key=f"uploads/{job_id}/{safe_name}",
        status=JobStatus.PENDING_UPLOAD.value,
        webhook_url=body.webhook_url,
        webhook_status="PENDING" if body.webhook_url else None,
    )
    db.add(job)
    db.commit()
    url = storage.presigned_upload_url(job.object_key, expires=URL_TTL)
    return JobCreated(job_id=job_id, upload_url=url, expires_in=URL_TTL)



@router.post("/{job_id}/start", status_code=202)
def start_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.PENDING_UPLOAD.value:
        raise HTTPException(409, f"Job already {job.status}")
    if not storage.object_exists(job.object_key):
        raise HTTPException(400, "File has not been uploaded yet")

    # Atomic: only one concurrent request can win this transition
    if not transition(db, job_id, JobStatus.QUEUED):
        raise HTTPException(409, "Job was started by another request")

    process_job.delay(str(job_id))
    return {"job_id": str(job_id), "status": JobStatus.QUEUED.value}


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(limit: int = 20, db: Session = Depends(get_db)):
    stmt = select(Job).order_by(Job.created_at.desc()).limit(min(limit, 100))
    return list(db.scalars(stmt))