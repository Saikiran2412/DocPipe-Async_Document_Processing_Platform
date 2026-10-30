import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.events import router as events_router
from app.api.jobs import router as jobs_router
from app.api.ops import router as ops_router
from app.config import settings
from app.services.maintenance import sweeper_loop
from app.services.storage import ensure_bucket

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_bucket()
    task = asyncio.create_task(sweeper_loop()) if settings.sweeper_enabled else None
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(title="docpipe", lifespan=lifespan)
app.include_router(jobs_router)
app.include_router(events_router)
app.include_router(ops_router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
