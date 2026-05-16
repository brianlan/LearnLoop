# LearnLoop development and deployment runbook

## Prerequisites

1. Copy the shared environment template if you want to override defaults:
   - `cp .env.example .env`
2. Docker Desktop (or Docker Engine with Compose) is required for MongoDB/RustFS and full-stack compose mode.
3. Local backend parity uses Python 3.11 and `uv`.
4. Local frontend parity uses Node.js 20 and npm.

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
| `S3_ACCESS_KEY` | RustFS access key | `replace-me` |
| `S3_SECRET_KEY` | RustFS secret key | `replace-me` |
| `S3_BUCKET` | Media bucket name | `learnloop-media` |
| `S3_REGION` | S3 region | `us-east-1` |
| `S3_FORCE_PATH_STYLE` | Path-style S3 URLs | `true` |
| `RUSTFS_ENDPOINT` | RustFS API base URL | `http://localhost:9000` |
| `RUSTFS_CONSOLE_ENDPOINT` | RustFS console URL | `http://localhost:9001` |
| `VLM_ENDPOINT` | External VLM API endpoint | example placeholder |
| `VLM_MODEL` | External VLM model identifier | `replace-me` |
| `VLM_API_KEY` | External VLM credential | `replace-me` |
| `VLM_TIMEOUT_SECONDS` | VLM request timeout | `120` |
| `PREVIEW_EXTRACTING_WINDOW_SECONDS` | Stale preview recovery window | `150` |
| `SESSION_COOKIE_NAME` | Session cookie name | `ll_session` |
| `SESSION_SECURE` | HTTPS-only session cookie toggle | `false` |
| `SESSION_SAMESITE` | Session cookie same-site policy | `lax` |

## Health checks and parity notes

- `mongodb` is only healthy after replica-set initialization succeeds.
- `rustfs` is only healthy after both API and console endpoints respond.
- `app` waits for MongoDB and RustFS before launching and is only healthy when dependency probes and the API health endpoint succeed.
- `frontend` serves the same built SPA that Vite produces and proxies `/api` traffic to the `app` service for compose parity.
