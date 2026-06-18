# LearnLoop development and deployment runbook

## Prerequisites

1. Copy the shared environment template if you want to override defaults:
   - `cp .env.example .env`
2. Docker Desktop (or Docker Engine with Compose) is required for MongoDB/RustFS and full-stack compose mode.
3. Local backend parity uses Python 3.11 and `uv`.
4. Local frontend parity uses Node.js 20 and npm.

For a non-default local object-store credential, set `S3_ACCESS_KEY` and `S3_SECRET_KEY` in `.env` before first starting RustFS. Docker Compose uses these values for both the RustFS container credentials and the backend S3 client.

```bash
S3_ACCESS_KEY=learnloop-local
S3_SECRET_KEY="$(openssl rand -base64 32)"
```

## Startup paths

### Option 1: Docker Compose for MongoDB + RustFS only

Start the dependency services:

```bash
./scripts/start-local.sh deps --bootstrap
```

Equivalent direct commands:

```bash
docker compose up -d mongodb rustfs
docker compose --profile bootstrap run --rm rustfs-bootstrap
docker compose ps
```

This path starts:

- MongoDB replica set on `localhost:27017`
- RustFS API on `http://127.0.0.1:9000`
- RustFS console on `http://127.0.0.1:9001`

### Option 2: Full compose deployment mode

Start the portable deployment topology (`frontend`, `app`, `mongodb`, `rustfs`):

```bash
./scripts/start-local.sh full --bootstrap
```

Equivalent direct commands:

```bash
docker compose up -d mongodb rustfs app frontend
docker compose --profile bootstrap run --rm rustfs-bootstrap
docker compose ps
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8080/healthz
```

Published ports in full compose mode:

- Frontend SPA: `http://127.0.0.1:8080`
- Backend API: `http://127.0.0.1:8000`
- MongoDB: `localhost:27017`
- RustFS API: `http://127.0.0.1:9000`
- RustFS console: `http://127.0.0.1:9001`

### Option 3: Local Python + npm with external services

Start just the external dependencies, then run the app layers locally:

```bash
./scripts/start-local.sh hybrid --bootstrap
```

Backend:

```bash
/Users/rlan/miniforge3/envs/mykik_py311/bin/python -m uvicorn app.main:app --reload --app-dir backend
```

or:

```bash
uv run --directory backend uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend && npm install && npm run dev -- --host 0.0.0.0
```

The Vite dev server proxies `/api` requests to `http://localhost:8000`.

## Dependency-specific commands

### MongoDB replica set

Compose initializes MongoDB 4.4 in single-node replica-set mode automatically. To start it directly:

```bash
docker compose up -d mongodb
docker compose ps mongodb
```

Readiness is tied to replica-set status, not only process startup.

### RustFS

Start RustFS directly:

```bash
docker compose up -d rustfs
docker compose ps rustfs
```

Create the bucket used by LearnLoop:

```bash
docker compose --profile bootstrap run --rm rustfs-bootstrap
```

### Volume backup and restore

To move Docker-managed data to another machine, stop the stack and archive the named volumes:

```bash
docker compose down
./scripts/backup-volumes.sh
```

This creates `mongodb_data.tar.gz` and `rustfs_data_*.tar.gz` under `./volume-backups/<timestamp>`.

On the target machine, copy that directory over, then restore into the same Docker volume names:

```bash
docker compose down
./scripts/restore-volumes.sh --wipe ./volume-backups/<timestamp>
./scripts/start-local.sh full --bootstrap
```

Keep `S3_ACCESS_KEY`, `S3_SECRET_KEY`, and `S3_BUCKET` aligned with the source machine when restoring RustFS volumes directly.

## Running tests

Backend:

```bash
cd backend && uv run pytest
```

Frontend unit/component tests:

```bash
cd frontend && npm test -- --run
```

Frontend browser tests:

```bash
cd frontend && npm run test:ui
```

Docker Compose smoke validation tests (requires running Compose stack):

```bash
# Start the full stack in Docker Compose
docker compose up -d mongodb rustfs app frontend
docker compose --profile bootstrap run --rm rustfs-bootstrap

# Wait for health checks to pass
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8080/healthz

# Run the Compose smoke browser test suite
cd frontend && npm run test:smoke:compose

# Tear down the stack when finished
docker compose down
```

## Environment variable reference

The canonical template lives in `.env.example`.

| Variable | Purpose | Local dev default |
| --- | --- | --- |
| `APP_ENV` | FastAPI runtime mode | `development` |
| `APP_HOST` | Backend bind host | `0.0.0.0` |
| `APP_PORT` | Backend bind port | `8000` |
| `APP_LOG_LEVEL` | Backend log verbosity | `INFO` |
| `MONGODB_URI` | Mongo replica-set connection string | `mongodb://localhost:27017/learnloop?replicaSet=rs0&directConnection=true` |
| `MONGODB_DATABASE` | Mongo database name | `learnloop` |
| `S3_ENDPOINT` | S3-compatible storage endpoint | `http://localhost:9000` |
| `S3_ACCESS_KEY` | S3-compatible storage access key | `replace-me` |
| `S3_SECRET_KEY` | S3-compatible storage secret key | `replace-me` |
| `S3_BUCKET` | Media bucket name | `learnloop-media` |
| `S3_REGION` | S3 region | `us-east-1` |
| `S3_FORCE_PATH_STYLE` | Path-style S3 URLs | `true` |
| `HELPER_VLM_ENDPOINT` | Helper VLM endpoint for subject classification | example placeholder |
| `HELPER_VLM_MODEL` | Helper VLM model identifier | `replace-me` |
| `HELPER_VLM_API_KEY` | Helper VLM credential | `replace-me` |
| `HELPER_VLM_TIMEOUT_SECONDS` | Helper VLM request timeout | `60` |
| `MATH_INGESTION_VLM_ENDPOINT` | Math ingestion VLM endpoint for math problem extraction | example placeholder |
| `MATH_INGESTION_VLM_MODEL` | Math ingestion VLM model identifier | `replace-me` |
| `MATH_INGESTION_VLM_API_KEY` | Math ingestion VLM credential | `replace-me` |
| `MATH_INGESTION_VLM_TIMEOUT_SECONDS` | Math ingestion VLM request timeout | `120` |
| `ENGLISH_INGESTION_VLM_ENDPOINT` | English ingestion VLM endpoint for English problem extraction | example placeholder |
| `ENGLISH_INGESTION_VLM_MODEL` | English ingestion VLM model identifier | `replace-me` |
| `ENGLISH_INGESTION_VLM_API_KEY` | English ingestion VLM credential | `replace-me` |
| `ENGLISH_INGESTION_VLM_TIMEOUT_SECONDS` | English ingestion VLM request timeout | `120` |
| `GRADING_VLM_ENDPOINT` | Grading VLM endpoint for short-answer judging in practice and exams | example placeholder |
| `GRADING_VLM_MODEL` | Grading VLM model identifier | `replace-me` |
| `GRADING_VLM_API_KEY` | Grading VLM credential | `replace-me` |
| `GRADING_VLM_TIMEOUT_SECONDS` | Grading VLM request timeout | `60` |
| `PREVIEW_EXTRACTING_WINDOW_SECONDS` | Stale preview recovery window | `150` |
| `MATH_SOLUTION_VLM_ENDPOINT` | Math solution generation VLM endpoint | example placeholder |
| `MATH_SOLUTION_VLM_MODEL` | Math solution generation VLM model identifier | `replace-me` |
| `MATH_SOLUTION_VLM_API_KEY` | Math solution generation VLM credential | `replace-me` |
| `MATH_SOLUTION_VLM_TIMEOUT_SECONDS` | Math solution generation VLM request timeout | `120` |
| `ENGLISH_SOLUTION_VLM_ENDPOINT` | English solution generation VLM endpoint | example placeholder |
| `ENGLISH_SOLUTION_VLM_MODEL` | English solution generation VLM model identifier | `replace-me` |
| `ENGLISH_SOLUTION_VLM_API_KEY` | English solution generation VLM credential | `replace-me` |
| `ENGLISH_SOLUTION_VLM_TIMEOUT_SECONDS` | English solution generation VLM request timeout | `120` |
| `MATH_COACHING_VLM_ENDPOINT` | Math coaching VLM endpoint | example placeholder |
| `MATH_COACHING_VLM_MODEL` | Math coaching VLM model identifier | `replace-me` |
| `MATH_COACHING_VLM_API_KEY` | Math coaching VLM credential | `replace-me` |
| `MATH_COACHING_VLM_TIMEOUT_SECONDS` | Math coaching VLM request timeout | `60` |
| `ENGLISH_COACHING_VLM_ENDPOINT` | English coaching VLM endpoint | example placeholder |
| `ENGLISH_COACHING_VLM_MODEL` | English coaching VLM model identifier | `replace-me` |
| `ENGLISH_COACHING_VLM_API_KEY` | English coaching VLM credential | `replace-me` |
| `ENGLISH_COACHING_VLM_TIMEOUT_SECONDS` | English coaching VLM request timeout | `60` |
| `SOLUTION_WORKER_POLL_INTERVAL_SECONDS` | Solution worker poll interval | `5` |
| `SOLUTION_TASK_TIMEOUT_MINUTES` | Solution task timeout | `10` |
| `SOLUTION_MAX_RETRIES` | Solution max retries | `3` |
| `SESSION_COOKIE_NAME` | Session cookie name | `ll_session` |
| `SESSION_SECURE` | HTTPS-only session cookie toggle | `false` |
| `SESSION_SAMESITE` | Session cookie same-site policy | `lax` |
| `PROBLEM_SELECTION_COOLDOWN_DAYS` | Days before revisiting a correctly answered problem | `7` |
| `PROBLEM_SELECTION_LAST_WRONG_WEIGHT` | Weight for problems last answered incorrectly | `1.0` |
| `PROBLEM_SELECTION_FAILURE_RATE_WEIGHT` | Weight for problems with high failure rates | `1.0` |
| `PROBLEM_SELECTION_RECENCY_WEIGHT` | Weight for recently tested problems | `1.0` |
| `PROBLEM_SELECTION_MIN_AGE_DAYS` | Minimum age (days) before a problem appears in practice or exams | `3` |

### AI tutoring VLM notes

- All `*_ENDPOINT` VLM variables must be OpenAI-compatible base URLs. The backend posts to `/chat/completions` relative to the base.
- Helper VLM classifies uploaded images as math or English and routes to the matching ingestion VLM.
- Math and English ingestion VLMs handle subject-specific image extraction and problem structuring.
- Grading VLM is used for short-answer correctness judgement in practice and exams (generic, not subject-specific).
- Math and English solution VLMs generate background canonical solutions per subject.
- Math and English coaching VLMs provide live tutoring responses per subject.

## Health checks and parity notes

- `mongodb` is only healthy after replica-set initialization succeeds.
- `rustfs` is only healthy after both API and console endpoints respond.
- `app` waits for MongoDB and RustFS before launching and is only healthy when dependency probes and the API health endpoint succeed.
- `frontend` serves the same built SPA that Vite produces and proxies `/api` traffic to the `app` service for compose parity.
