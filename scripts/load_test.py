"""Load test. Run from the project root while the API and at least one worker are up:
   uv run python scripts\load_test.py 30
Only talks to the HTTP API, so no PYTHONPATH is needed. N is capped at 100.
"""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

BASE = "http://127.0.0.1:8000"
N = min(int(sys.argv[1]) if len(sys.argv) > 1 else 30, 100)
FILES = ["samples/invoice.txt", "samples/people.csv", "samples/invoice.pdf",
         "samples/invoice.txt", "samples/people.csv", "samples/scan.png"]   # 1 in 6 needs OCR


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read() or b"null")


def submit(i):
    path = FILES[i % len(FILES)]
    job = req("POST", "/jobs", {"filename": os.path.basename(path)})
    with open(path, "rb") as f:
        data = f.read()
    urllib.request.urlopen(urllib.request.Request(job["upload_url"], data=data, method="PUT"),
                           timeout=60).read()
    req("POST", f"/jobs/{job['job_id']}/start")
    return job["job_id"]


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, int(p * (len(s) - 1) + 0.5))] if s else float("nan")


print(f"submitting {N} jobs (8 at a time)...")
t0 = time.time()
with ThreadPoolExecutor(max_workers=8) as pool:
    ids = list(pool.map(submit, range(N)))
t_submit = time.time() - t0

pending, done = set(ids), {}
deadline = time.time() + 600
while pending and time.time() < deadline:
    for j in req("GET", "/jobs?limit=100"):
        if j["id"] in pending and j["status"] in ("COMPLETED", "FAILED"):
            done[j["id"]] = j
            pending.discard(j["id"])
    if pending:
        time.sleep(1)
wall = time.time() - t0

jobs = list(done.values())
lat = [(ts(j["finished_at"]) - ts(j["created_at"])).total_seconds() for j in jobs]
wait = [(ts(j["started_at"]) - ts(j["created_at"])).total_seconds() for j in jobs if j["started_at"]]
failed = [j for j in jobs if j["status"] != "COMPLETED"]
retried = [j for j in jobs if j["attempts"] > 1]
methods = {}
for j in jobs:
    m = (j.get("result") or {}).get("summary_method", "-")
    methods[m] = methods.get(m, 0) + 1
depth = req("GET", "/admin/stats")["queues"].get("jobs")
ok = not pending and not failed and not retried and depth in (0, None)

print()
print(f"jobs accepted        {N} in {t_submit:.1f}s  ({N / t_submit:.1f} jobs/s accepted)")
print(f"completed / failed   {len(jobs) - len(failed)} / {len(failed)}   (still pending: {len(pending)})")
print(f"all finished in      {wall:.1f}s  ({len(jobs) / wall:.2f} jobs/s end to end)")
print(f"latency p50/p95/max  {pct(lat, .5):.1f} / {pct(lat, .95):.1f} / {max(lat, default=0):.1f} s")
print(f"wait before start    {pct(wait, .5):.1f} / {pct(wait, .95):.1f} / {max(wait, default=0):.1f} s")
print(f"retried (attempts>1) {len(retried)}")
print(f"summary methods      {methods}")
print(f"queue depth after    {depth}")
print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
