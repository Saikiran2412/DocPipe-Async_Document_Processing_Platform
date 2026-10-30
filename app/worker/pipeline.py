import json
import logging
import uuid
from dataclasses import dataclass
from typing import Callable

from app.db.models import Job, JobStatus, utcnow
from app.db.session import SessionLocal
from app.services import storage
from app.services.job_service import transition, update_progress
from app.worker.errors import PermanentError, is_transient
from app.worker.processors.extract import extract_fields
from app.worker.processors.summarize import summarize
from app.worker.processors.text import MAX_BYTES, detect_kind, extract_text

log = logging.getLogger(__name__)


def _store(ctx: dict, name: str, data: bytes, content_type: str) -> str:
    key = f"results/{ctx['job_id']}/{name}"          # deterministic: reruns overwrite
    storage.put_bytes(key, data, content_type)
    return key


def _validate(ctx: dict) -> dict:
    if not storage.object_exists(ctx["object_key"]):
        raise FileNotFoundError(f"object missing: {ctx['object_key']}")
    size = storage.object_size(ctx["object_key"])
    if size == 0:
        raise PermanentError("empty file")
    if size > MAX_BYTES:
        raise PermanentError(f"file too large: {size} bytes (limit {MAX_BYTES})")
    return {"size": size}


def _parse(ctx: dict) -> dict:
    data = storage.get_bytes(ctx["object_key"])
    kind = detect_kind(ctx["filename"], data)
    parsed = extract_text(kind, data)
    key = _store(ctx, "text.txt", parsed["text"].encode("utf-8"), "text/plain; charset=utf-8")
    return {"kind": kind, "text": parsed["text"], "stats": parsed["stats"], "text_key": key}


def _extract(ctx: dict) -> dict:
    fields = extract_fields(ctx["text"])
    key = _store(ctx, "fields.json", json.dumps(fields).encode("utf-8"), "application/json")
    return {"fields": fields, "fields_key": key}


def _summarize(ctx: dict) -> dict:
    out = summarize(ctx["text"], ctx["kind"], ctx["stats"])
    key = _store(ctx, "summary.json", json.dumps(out).encode("utf-8"), "application/json")
    return {"summary": out["summary"], "summary_method": out["method"], "summary_key": key}


@dataclass(frozen=True)
class Stage:
    name: str
    progress_after: int
    fn: Callable[[dict], dict]


STAGES = [
    Stage("validate", 10, _validate),
    Stage("parse", 40, _parse),
    Stage("extract", 70, _extract),
    Stage("summarize", 100, _summarize),
]


def run_pipeline(job_id: str, redelivered: bool = False) -> None:
    jid = uuid.UUID(job_id)
    db = SessionLocal()
    try:
        job = db.get(Job, jid)
        if job is None:
            log.warning("job %s not found, skipping", job_id)
            return

        if redelivered and job.status == JobStatus.PROCESSING.value:
            log.warning("job %s redelivered while PROCESSING: previous worker lost", job_id)
            transition(db, jid, JobStatus.RETRYING, error="worker lost; redelivered")

        claimed = transition(
            db, jid, JobStatus.PROCESSING,
            attempts=Job.attempts + 1, started_at=utcnow(), stage=None, progress=0,
        )
        if not claimed:
            log.info("job %s not claimable (duplicate or finished), skipping", job_id)
            return

        db.refresh(job)
        ctx = {"job_id": job_id, "object_key": job.object_key,
               "filename": job.filename, "attempt": job.attempts}

        current, done = None, 0
        try:
            for stage in STAGES:
                current = stage.name
                update_progress(db, jid, stage.name, done)
                ctx.update(stage.fn(ctx))
                done = stage.progress_after
                update_progress(db, jid, stage.name, done)

            transition(
                db, jid, JobStatus.COMPLETED,
                result={
                    "kind": ctx["kind"], "size_bytes": ctx["size"], "stats": ctx["stats"],
                    "fields": ctx["fields"], "summary": ctx["summary"],
                    "summary_method": ctx["summary_method"],
                    "outputs": {"text": ctx["text_key"], "fields": ctx["fields_key"],
                                "summary": ctx["summary_key"]},
                },
                progress=100, error=None, finished_at=utcnow(),
            )
        except Exception as exc:
            db.rollback()
            msg = f"{current}: {exc}"
            if is_transient(exc):
                transition(db, jid, JobStatus.RETRYING, error=msg)
                raise
            transition(db, jid, JobStatus.FAILED, error=msg, finished_at=utcnow())
            log.error("job %s failed permanently: %s", job_id, msg)
    finally:
        db.close()
