import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models import Job, JobStatus
from app.db.session import SessionLocal
from app.main import app
from app.services.job_service import transition

pytestmark = pytest.mark.integration
client = TestClient(app)      # not used as a context manager: no lifespan, so no sweeper


@pytest.fixture
def cleanup():
    ids = []
    yield ids
    db = SessionLocal()
    db.execute(delete(Job).where(Job.id.in_([uuid.UUID(str(i)) for i in ids])))
    db.commit()
    db.close()


def _new_job(status):
    db = SessionLocal()
    job = Job(id=uuid.uuid4(), filename="race.txt", object_key="uploads/x/race.txt", status=status)
    db.add(job)
    db.commit()
    jid = job.id
    db.close()
    return jid


def test_unknown_job_is_404_and_malformed_id_is_422():
    assert client.get(f"/jobs/{uuid.uuid4()}").status_code == 404
    assert client.get("/jobs/not-a-uuid").status_code == 422


def test_bad_webhook_url_is_rejected():
    r = client.post("/jobs", json={"filename": "a.txt", "webhook_url": "ftp://x/y"})
    assert r.status_code == 400


def test_filename_is_sanitised_and_start_requires_an_upload(cleanup):
    r = client.post("/jobs", json={"filename": "../../evil.txt"})
    assert r.status_code == 201
    body = r.json()
    cleanup.append(body["job_id"])
    assert "evil.txt" in body["upload_url"] and ".." not in body["upload_url"]
    assert client.post(f"/jobs/{body['job_id']}/start").status_code == 400
    job = client.get(f"/jobs/{body['job_id']}").json()
    assert job["status"] == "PENDING_UPLOAD" and job["filename"] == "evil.txt"


def test_retry_only_applies_to_failed_jobs(cleanup):
    created = client.post("/jobs", json={"filename": "a.txt"}).json()
    cleanup.append(created["job_id"])
    assert client.post(f"/jobs/{created['job_id']}/retry").status_code == 409
    assert client.post(f"/jobs/{uuid.uuid4()}/retry").status_code == 404


def test_readiness_reports_every_dependency():
    r = client.get("/readyz")
    assert set(r.json()["checks"]) == {"database", "redis", "storage", "broker"}


def test_only_one_concurrent_start_wins(cleanup):
    jid = _new_job(JobStatus.PENDING_UPLOAD.value)
    cleanup.append(jid)
    results = []
    barrier = threading.Barrier(8)

    def racer():
        db = SessionLocal()
        try:
            barrier.wait()
            results.append(transition(db, jid, JobStatus.QUEUED))
        finally:
            db.close()

    threads = [threading.Thread(target=racer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and results.count(False) == 7


def test_illegal_transition_is_rejected(cleanup):
    jid = _new_job(JobStatus.COMPLETED.value)
    cleanup.append(jid)
    db = SessionLocal()
    assert transition(db, jid, JobStatus.PROCESSING) is False
    db.close()
