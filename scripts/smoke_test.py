"""End-to-end smoke test. Run from the project root:
   $env:PYTHONPATH = (Get-Location).Path; uv run python scripts\smoke_test.py
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import timedelta

from sqlalchemy import update

from app.db.models import Job, utcnow
from app.db.session import SessionLocal

BASE = "http://127.0.0.1:8000"
rows = []
state = {}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jcall(method, path, body=None):
    status, raw = call(method, path, body)
    try:
        return status, json.loads(raw)
    except Exception:
        return status, raw.decode(errors="replace")


def submit(path, start=True, webhook=None):
    payload = {"filename": os.path.basename(path)}
    if webhook:
        payload["webhook_url"] = webhook
    _, job = jcall("POST", "/jobs", payload)
    with open(path, "rb") as f:
        body = f.read()
    urllib.request.urlopen(urllib.request.Request(job["upload_url"], data=body, method="PUT"), timeout=30).read()
    if start:
        jcall("POST", f"/jobs/{job['job_id']}/start")
    return job["job_id"]


def wait(job_id, timeout=90, extra=None):
    end = time.time() + timeout
    j = {}
    while time.time() < end:
        _, j = jcall("GET", f"/jobs/{job_id}")
        if j.get("status") in ("COMPLETED", "FAILED") and (extra is None or extra(j)):
            return j
        time.sleep(1)
    return j


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    rows.append(("SKIP" if ok is None else "PASS" if ok else "FAIL", name, detail))


def t_health():
    return call("GET", "/healthz")[0] == 200, "liveness"


def t_ready():
    status, d = jcall("GET", "/readyz")
    return status == 200, json.dumps(d.get("checks") if isinstance(d, dict) else d)


def t_index():
    status, raw = call("GET", "/")
    return status == 200 and b"docpipe" in raw and b"tailwind" in raw.lower(), f"HTTP {status}, {len(raw)} bytes"


def t_invoice():
    jid = submit("samples/invoice.txt")
    state["invoice"] = jid
    j = wait(jid)
    ids = (j.get("result") or {}).get("fields", {}).get("invoice_numbers", [])
    return j["status"] == "COMPLETED" and "INV-2026-001" in ids, f"{j['status']}, method={j.get('result', {}).get('summary_method')}"


def t_sse():
    jid = submit("samples/scan.png")
    seen = []
    with urllib.request.urlopen(f"{BASE}/jobs/{jid}/events", timeout=90) as r:
        for line in r:
            line = line.decode().strip()
            if line.startswith("data:"):
                seen.append(json.loads(line[5:])["status"])
    return seen[-1:] == ["COMPLETED"], f"{len(seen)} events, last={seen[-1:]}"


def t_download():
    status, raw = call("GET", f"/jobs/{state['invoice']}/download/summary")
    return status == 200 and b"summary" in raw, f"HTTP {status}, {len(raw)} bytes"


def t_retry():
    jid = submit("samples/corrupt.pdf")
    first = wait(jid)
    status, _ = jcall("POST", f"/jobs/{jid}/retry")
    second = wait(jid)
    s409, _ = jcall("POST", f"/jobs/{state['invoice']}/retry")
    ok = (first["status"] == "FAILED" and status == 202 and second["status"] == "FAILED"
          and second["attempts"] == 1 and s409 == 409)
    return ok, f"retry={status}, attempts after retry={second['attempts']}, retry of completed={s409}"


def t_sweeper():
    jid = submit("samples/invoice.txt", start=False)
    db = SessionLocal()
    db.execute(update(Job).where(Job.id == uuid.UUID(jid))
               .values(status="QUEUED", updated_at=utcnow() - timedelta(minutes=5)))
    db.commit()
    db.close()
    _, out = jcall("POST", "/admin/sweep")
    j = wait(jid)
    return j["status"] == "COMPLETED" and out.get("requeued", 0) >= 1, f"sweep={out}, job={j['status']}"


def t_stats():
    status, d = jcall("GET", "/admin/stats")
    ok = status == 200 and "jobs" in d and isinstance(d["queues"].get("jobs.dead"), int)
    return ok, json.dumps(d.get("queues"))


def t_webhook():
    try:
        socket.create_connection(("127.0.0.1", 9100), timeout=1).close()
    except OSError:
        return None, "receiver not running on :9100"
    jid = submit("samples/invoice.txt", webhook="http://127.0.0.1:9100/ok")
    j = wait(jid, extra=lambda x: x.get("webhook_status") in ("DELIVERED", "FAILED"))
    return j.get("webhook_status") == "DELIVERED", f"webhook={j.get('webhook_status')}, attempts={j.get('webhook_attempts')}"


for name, fn in [("liveness", t_health), ("readiness", t_ready), ("dashboard served", t_index),
                 ("text job completes", t_invoice), ("SSE stream (OCR job)", t_sse),
                 ("download output", t_download), ("retry failed job", t_retry),
                 ("sweeper recovers lost message", t_sweeper), ("admin stats", t_stats),
                 ("signed webhook delivered", t_webhook)]:
    check(name, fn)

width = max(len(n) for _, n, _ in rows)
for s, n, d in rows:
    print(f"{s:<5} {n:<{width}}  {d}")
print(f"\n{sum(r[0] == 'PASS' for r in rows)} passed, {sum(r[0] == 'FAIL' for r in rows)} failed, {sum(r[0] == 'SKIP' for r in rows)} skipped")
sys.exit(1 if any(r[0] == "FAIL" for r in rows) else 0)
