#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/start-services.sh [--bootstrap]

Starts only the local MongoDB + RustFS services via Docker Compose.
The backend still runs outside Compose, either with:
  /Users/rlan/miniforge3/envs/mykik_py311/bin/python -m uvicorn app.main:app --reload --app-dir backend
or:
  uv run --directory backend uvicorn app.main:app --reload

Options:
  --bootstrap  Create the configured RustFS bucket after services become healthy.
EOF
}

bootstrap=false

for arg in "$@"; do
  case "$arg" in
    --bootstrap)
      bootstrap=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
done

docker compose -f "$repo_root/docker-compose.yml" up -d mongodb rustfs

if [[ "$bootstrap" == "true" ]]; then
  docker compose -f "$repo_root/docker-compose.yml" --profile bootstrap run --rm rustfs-bootstrap
fi

cat <<'EOF'
Services requested.

Local backend contract:
  MONGODB_URI=mongodb://localhost:27017/learnloop?replicaSet=rs0&directConnection=true
  S3_ENDPOINT=http://localhost:9000
  RUSTFS_ENDPOINT=http://localhost:9000
  RUSTFS_CONSOLE_ENDPOINT=http://localhost:9001

Run the backend outside Compose with either:
  /Users/rlan/miniforge3/envs/mykik_py311/bin/python -m uvicorn app.main:app --reload --app-dir backend
  uv run --directory backend uvicorn app.main:app --reload
EOF
