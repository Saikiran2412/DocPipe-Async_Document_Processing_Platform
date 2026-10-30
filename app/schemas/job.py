import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

class JobCreate(BaseModel):
    filename: str
    content_type: str | None = None
    webhook_url: str | None = None

class JobCreated(BaseModel):
    job_id: uuid.UUID
    upload_url: str
    expires_in: int

class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: str
    stage: str | None
    progress: int
    attempts: int
    result: dict | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    webhook_status: str | None
    webhook_attempts: int