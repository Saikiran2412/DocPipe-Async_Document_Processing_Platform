import json
import time
import uuid
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.config import settings
from app.db.session import SessionLocal
from app.services.events import TERMINAL, channel_for, snapshot

router = APIRouter(prefix="/jobs", tags=["events"])

HEARTBEAT_SECONDS = 15


def _load_snapshot(job_id: uuid.UUID) -> dict | None:
    db = SessionLocal()
    try:
        return snapshot(db, job_id)
    finally:
        db.close()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


@router.get("/{job_id}/events")
async def stream_job_events(job_id: uuid.UUID, request: Request):
    if await run_in_threadpool(_load_snapshot, job_id) is None:
        raise HTTPException(404, "Job not found")

    async def event_stream():
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = client.pubsub()
        try:
            # 1. Subscribe FIRST, so no event can fall into the gap...
            await pubsub.subscribe(channel_for(job_id))
            # 2. ...then read the current state from the source of truth.
            snap = await run_in_threadpool(_load_snapshot, job_id)
            yield _sse("status", snap)
            if snap["status"] in TERMINAL:
                return

            last_sent = time.monotonic()
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is not None:
                    data = json.loads(msg["data"])
                    if _ts(data["updated_at"]) < _ts(snap["updated_at"]):
                        continue                      # older than the snapshot: drop it
                    yield _sse("status", data)
                    last_sent = time.monotonic()
                    if data["status"] in TERMINAL:
                        break
                elif time.monotonic() - last_sent > HEARTBEAT_SECONDS:
                    yield ": keep-alive\n\n"
                    last_sent = time.monotonic()
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()
            await client.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )