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
| `VLM_ENDPOINT` | External VLM API endpoint | example placeholder |
| `VLM_MODEL` | External VLM model identifier | `replace-me` |
| `VLM_API_KEY` | External VLM credential | `replace-me` |
| `VLM_TIMEOUT_SECONDS` | VLM request timeout | `120` |
| `PREVIEW_EXTRACTING_WINDOW_SECONDS` | Stale preview recovery window | `150` |
| `SOLUTION_LLM_ENDPOINT` | Solution generation LLM endpoint (OpenAI-compatible base URL) | example placeholder |
| `SOLUTION_LLM_MODEL` | Solution generation LLM model identifier | `replace-me` |
| `SOLUTION_LLM_API_KEY` | Solution generation LLM credential | `replace-me` |
| `COACHING_LLM_ENDPOINT` | Coaching LLM endpoint (OpenAI-compatible base URL) | example placeholder |
| `COACHING_LLM_MODEL` | Coaching LLM model identifier | `replace-me` |
| `COACHING_LLM_API_KEY` | Coaching LLM credential | `replace-me` |
| `SOLUTION_WORKER_POLL_INTERVAL_SECONDS` | Solution worker poll interval | `5` |
| `SOLUTION_TASK_TIMEOUT_MINUTES` | Solution task timeout | `10` |
| `SOLUTION_MAX_RETRIES` | Solution max retries | `3` |
| `SESSION_COOKIE_NAME` | Session cookie name | `ll_session` |
| `SESSION_SECURE` | HTTPS-only session cookie toggle | `false` |
| `SESSION_SAMESITE` | Session cookie same-site policy | `lax` |
| `PRACTICE_COOLDOWN_DAYS` | Days before revisiting a correctly answered problem | `7` |
| `PRACTICE_LAST_WRONG_WEIGHT` | Weight for problems last answered incorrectly | `1.0` |
| `PRACTICE_FAILURE_RATE_WEIGHT` | Weight for problems with high failure rates | `1.0` |
| `PRACTICE_RECENCY_WEIGHT` | Weight for recently tested problems | `1.0` |

### AI tutoring LLM notes

- `SOLUTION_LLM_ENDPOINT` and `COACHING_LLM_ENDPOINT` must be OpenAI-compatible base URLs. The backend posts to `/chat/completions` relative to the base.
- Solution generation and coaching use separate provider configuration. This allows using different providers, models, or API keys for each feature.

## Health checks and parity notes

- `mongodb` is only healthy after replica-set initialization succeeds.
- `rustfs` is only healthy after both API and console endpoints respond.
- `app` waits for MongoDB and RustFS before launching and is only healthy when dependency probes and the API health endpoint succeed.
- `frontend` serves the same built SPA that Vite produces and proxies `/api` traffic to the `app` service for compose parity.
