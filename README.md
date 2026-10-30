@'
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
*.log
helpers.ps1
samples/
/*.txt
'@ | Set-Content .gitignore

@'
DATABASE_URL=postgresql+psycopg://docpipe:docpipe@127.0.0.1:5433/docpipe
BROKER_URL=amqp://guest:guest@127.0.0.1:5672//
RESULT_BACKEND=redis://127.0.0.1:6380/1
REDIS_URL=redis://127.0.0.1:6380/0
S3_ENDPOINT=http://127.0.0.1:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=uploads
WEBHOOK_SECRET=change-me
# false blocks private and loopback webhook targets. Set true only to test against a local receiver.
WEBHOOK_ALLOW_PRIVATE=false
# Optional. Leave empty to use the extractive summary only.
GROQ_API_KEY=
LLM_ENABLED=true
LLM_MODEL=openai/gpt-oss-20b
'@ | Set-Content .env.example

@'
# docpipe

An asynchronous document-processing platform. Users upload PDFs, images, CSVs or text files; the system processes them in the background (parsing, OCR, field extraction, LLM summary), reports progress live, and can notify a customer's server with a signed webhook.

The idea it demonstrates: **long-running work must never block an HTTP request.** The API accepts a job, returns a `job_id` immediately, and workers do the slow part.

## Features

- Direct-to-storage uploads with presigned URLs (file bytes never pass through the API)
- Background processing with Celery and RabbitMQ, scalable by adding workers
- Staged pipeline: validate, parse (text, CSV, PDF, OCR for images and scanned PDFs), extract fields, summarize
- Atomic state machine in PostgreSQL (the source of truth for job state)
- Retries with exponential backoff and jitter, dead-letter queue, crash recovery with late acknowledgements
- Live progress over Server-Sent Events (Redis pub/sub)
- HMAC-signed webhooks with retries and a stable delivery ID
- LLM summary (Groq) with an extractive fallback
- Sweeper that repairs lost messages, manual retry, dead-letter replay
- Health and readiness endpoints, admin stats
- Tailwind dashboard served at `/`
- Unit, integration, smoke and load tests

## Architecture

```mermaid
flowchart LR
  Browser -->|1 create job| API[FastAPI]
  API --> PG[(PostgreSQL)]
  Browser -->|2 presigned PUT| S3[(S3-compatible storage)]
  Browser -->|3 start| API
  API -->|enqueue| MQ[[RabbitMQ]]
  MQ --> W[Celery workers]
  W --> S3
  W --> PG
  W -->|events| R[(Redis pub/sub)]
  R --> API
  API -->|SSE| Browser
  W -->|signed webhook| Customer[Customer server]
  MQ -. dead letters .-> DLQ[[jobs.dead]]
```

### Job lifecycle

```mermaid
stateDiagram-v2
  [*] --> PENDING_UPLOAD
  PENDING_UPLOAD --> QUEUED
  QUEUED --> PROCESSING
  PROCESSING --> COMPLETED
  PROCESSING --> RETRYING
  RETRYING --> PROCESSING
  PROCESSING --> FAILED
  RETRYING --> FAILED
  QUEUED --> FAILED
  FAILED --> QUEUED : manual retry
```

Every transition is one conditional `UPDATE ... WHERE status IN (allowed sources)`, so concurrent requests cannot both win and illegal jumps are impossible.

### Request flow

1. `POST /jobs` creates a row and returns a presigned upload URL.
2. The client `PUT`s the file straight to object storage.
3. `POST /jobs/{id}/start` verifies the upload, atomically moves the job to `QUEUED`, publishes the task, and returns `202`.
4. A worker claims the job (`QUEUED` or `RETRYING` to `PROCESSING`) and runs the stages, writing progress and publishing an event after each committed change.
5. On success or permanent failure the job reaches a terminal state, and an optional webhook delivery task is enqueued.

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI, Uvicorn, Pydantic |
| Queue | RabbitMQ with Celery (late acks, dead-letter exchange) |
| Database | PostgreSQL, SQLAlchemy 2, Alembic |
| Events | Redis pub/sub, Server-Sent Events |
| Storage | Any S3-compatible store through boto3 (the compose file uses a MinIO-compatible image) |
| Parsing and OCR | pypdf, Tesseract with pytesseract, pypdfium2, Pillow |
| LLM | Groq API (`openai/gpt-oss-20b` by default) |
| Frontend | One HTML file with Tailwind (CDN) and vanilla JavaScript |
| Tooling | uv, Docker Compose, pytest |

## Project structure

```
docker-compose.yml  pyproject.toml  pytest.ini  alembic.ini  .env.example
alembic/versions/                 database migrations
app/
  main.py  config.py              app entry point, typed settings
  api/       jobs.py events.py ops.py        HTTP layer only
  db/        models.py session.py
  schemas/   job.py                          API contracts
  services/  storage.py job_service.py events.py webhooks.py maintenance.py
  static/    index.html                      the dashboard
  worker/    celery_app.py tasks.py pipeline.py errors.py
             processors/ text.py ocr.py extract.py summarize.py
scripts/     webhook_receiver.py smoke_test.py load_test.py
             make_samples.py make_ocr_samples.py llm_probe.py
tests/       test_unit.py test_integration.py
```

Dependency direction: `api` depends on `services`, `db` and `worker.tasks`; `worker` depends on `services` and `db`; nothing in `services` or `db` imports upward.

## Setup (Windows, PowerShell)

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), and optionally [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) for OCR.

```powershell
docker compose up -d
uv sync
Copy-Item .env.example .env        # then edit .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

In a second terminal, start one worker:

```powershell
uv run celery -A app.worker.celery_app worker --loglevel=info --pool=solo -Q jobs
```

Open http://127.0.0.1:8000/ for the dashboard, or http://127.0.0.1:8000/docs for the API docs.

Notes:

- `--pool=solo` is required on Windows. On Linux use `--pool=prefork --concurrency 4`.
- The worker does not reload on code changes. Restart it, and make sure only one is running.
- Ports 5433 (Postgres) and 6380 (Redis) avoid clashes with local instances. Change them in `docker-compose.yml` and `.env` if you like.
- OCR looks for Tesseract at `C:\Program Files\Tesseract-OCR\tesseract.exe`. Set `TESSERACT_CMD` to override.

## Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL`, `BROKER_URL`, `RESULT_BACKEND`, `REDIS_URL` | Connection strings |
| `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` | Object storage |
| `WEBHOOK_SECRET` | Shared secret for signing webhooks |
| `WEBHOOK_ALLOW_PRIVATE` | `false` in production (blocks private and loopback targets) |
| `GROQ_API_KEY`, `LLM_ENABLED`, `LLM_MODEL` | LLM summary. Without a key the extractive summary is used |
| `SWEEPER_ENABLED` | Optional, defaults to true |

## API

| Method and path | Purpose |
|---|---|
| `POST /jobs` | Create a job (`filename`, optional `webhook_url`). Returns `201` with a presigned upload URL |
| `POST /jobs/{id}/start` | Verify the upload and queue the job. `202`, or `400`, `404`, `409` |
| `GET /jobs/{id}` | Job state, progress, result |
| `GET /jobs?limit=` | Newest jobs first |
| `GET /jobs/{id}/events` | Live progress (SSE). Sends a database snapshot first, then updates |
| `POST /jobs/{id}/retry` | Re-queue a `FAILED` job |
| `GET /jobs/{id}/download/{text,fields,summary}` | Redirect to a short-lived signed link |
| `GET /healthz`, `GET /readyz` | Liveness, and readiness (database, Redis, storage, broker) |
| `GET /admin/stats` | Job counts and queue depths |
| `POST /admin/sweep` | Run the recovery pass now |
| `POST /admin/dead-letters/replay` | Retry the jobs behind dead-lettered messages |

The admin endpoints have no authentication. Put them behind auth before exposing the service.

### Webhooks

A job with a `webhook_url` triggers a `POST` on completion or failure with these headers:

| Header | Value |
|---|---|
| `X-Docpipe-Event` | `job.completed` or `job.failed` |
| `X-Docpipe-Delivery` | `<job_id>.<event>`, stable across retries (deduplicate on it) |
| `X-Docpipe-Timestamp` | Unix seconds, fresh on every attempt |
| `X-Docpipe-Signature` | `sha256=` plus the HMAC-SHA256 of `timestamp + "." + raw_body` with the shared secret |

Receivers should compare signatures in constant time and reject old timestamps. `5xx`, `408`, `429`, timeouts and connection errors retry with jittered backoff (up to 5 retries); other `4xx` stop immediately. A webhook failure never changes the job's status. `scripts/webhook_receiver.py` is a reference receiver.

## Testing

```powershell
uv run pytest -q                                    # unit tests, plus integration tests (needs the stack)
uv run pytest -q -m "not integration"               # unit tests only, no services needed
$env:PYTHONPATH = (Get-Location).Path
uv run python scripts\smoke_test.py                 # end-to-end checks, one pass/fail table
uv run python scripts\load_test.py 30               # N mixed jobs: latency, retries, queue drain
```

Create sample files first with `uv run --with reportlab python scripts\make_samples.py` and `uv run python scripts\make_ocr_samples.py`.

## Design decisions

| Decision | Why |
|---|---|
| PostgreSQL is the source of truth; the broker only delivers | Messages can be lost or duplicated, rows cannot |
| Atomic conditional updates for every state change | Concurrent starts have exactly one winner, and duplicate deliveries lose the claim |
| Late acknowledgements plus an idempotent claim | A crashed worker's message is redelivered and harmless if repeated |
| Errors classified as transient or permanent (allow-list of transient types) | Network faults retry; bad files fail fast; unknown errors do not retry forever |
| Presigned uploads | Large transfers stay off the API |
| Deterministic output keys (`results/<job_id>/...`) | Reruns overwrite instead of duplicating |
| Events are best-effort, with a database snapshot on connect | Pub/sub never replays, so late clients read the truth from PostgreSQL |
| Webhooks in their own task | A broken receiver cannot affect a job |
| LLM summary with an extractive fallback and a `finish_reason` guard | A model outage or a truncated reply must not fail or corrupt a job |
| Exact values from regex, prose from the model | An LLM paraphrases (it dropped the cents in testing) |
| Sweeper instead of a transactional outbox | Simpler. It repairs lost messages after the fact, and duplicates are harmless |

## Known limitations

- No authentication or per-user ownership
- One shared webhook secret (per-customer secrets with rotation would be next)
- Webhook DNS is validated once, so DNS rebinding is not covered
- The whole file is read into memory (20 MB limit); OCR is English only and capped at 10 pages
- A crash mid-pipeline reruns every stage (checkpointing would fix it)
- Document text is sent to a third party when the LLM is enabled
- The sweeper re-publishes after the fact; a transactional outbox would be the stricter fix
- The dashboard lists the newest 50 jobs, without search or delete
- Tailwind loads from its CDN; a production build would compile the CSS
'@ | Set-Content README.md

# clean-up: stray test files from earlier parts
'sample.txt','flaky.txt','poison.txt','poison2.txt','bad.txt','slow.txt' | ForEach-Object { Remove-Item $_ -ErrorAction SilentlyContinue }

# document the probe script
if (-not ((Get-Content scripts\llm_probe.py -TotalCount 1) -like '"""*')) {
  $body = Get-Content scripts\llm_probe.py
  @('"""Diagnostic: makes real LLM calls and prints finish_reason and reasoning tokens.', 'Run with PYTHONPATH set to the project root."""') + $body | Set-Content scripts\llm_probe.py
}

# git and secret scan
if (Get-Command git -ErrorAction SilentlyContinue) {
  if (-not (Test-Path .git)) { git init -q; "git repository created" }
  "--- .env ignored by git?"
  git check-ignore -v .env
  "--- scanning files git would track for key-like strings (names and line numbers only)"
  $found = $false
  foreach ($f in (git ls-files --cached --others --exclude-standard)) {
    foreach ($m in (Select-String -Path $f -Pattern 'gsk_[A-Za-z0-9]{10,}' -ErrorAction SilentlyContinue)) {
      "FOUND in {0}:{1}" -f $m.Path, $m.LineNumber; $found = $true
    }
  }
  if (-not $found) { "no key-like strings found" }
  "--- files that would be committed: " + @(git ls-files --cached --others --exclude-standard).Count
} else { "git is not installed, so skipping the git checks" }