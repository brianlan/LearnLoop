#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/start-local.sh <mode> [--bootstrap]

Modes:
  deps       Start only MongoDB + RustFS with Docker Compose.
  full       Start MongoDB + RustFS + app + frontend with Docker Compose.
  hybrid     Start Dockerized MongoDB + RustFS, then print local backend/frontend commands.
  help       Show this runbook summary.

Options:
  --bootstrap  Create the configured RustFS bucket after services become healthy.

Examples:
  scripts/start-local.sh deps --bootstrap
  scripts/start-local.sh full --bootstrap
  scripts/start-local.sh hybrid --bootstrap
EOF
}

mode="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi

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

bootstrap_bucket() {
  docker compose -f "$repo_root/docker-compose.yml" --profile bootstrap run --rm rustfs-bootstrap
}

print_hybrid_commands() {
  cat <<'EOF'

Hybrid local-dev contract
=========================

1. Backend (choose one):
   /Users/rlan/miniforge3/envs/mykik_py311/bin/python -m uvicorn app.main:app --reload --app-dir backend
   uv run --directory backend uvicorn app.main:app --reload

2. Frontend:
   cd frontend && npm install && npm run dev -- --host 0.0.0.0

3. Local service endpoints:
   MONGODB_URI=mongodb://localhost:27017/learnloop?replicaSet=rs0&directConnection=true
   S3_ENDPOINT=http://localhost:9000
   RustFS console: http://localhost:9001

4. Tests:
   cd backend && uv run pytest
   cd frontend && npm test -- --run
EOF
}

case "$mode" in
  deps)
    docker compose -f "$repo_root/docker-compose.yml" up -d mongodb rustfs
    if [[ "$bootstrap" == "true" ]]; then
      bootstrap_bucket
    fi
    ;;
  full)
    docker compose -f "$repo_root/docker-compose.yml" up -d mongodb rustfs app frontend
    if [[ "$bootstrap" == "true" ]]; then
      bootstrap_bucket
    fi
    docker compose -f "$repo_root/docker-compose.yml" ps
    ;;
  hybrid)
    docker compose -f "$repo_root/docker-compose.yml" up -d mongodb rustfs
    if [[ "$bootstrap" == "true" ]]; then
      bootstrap_bucket
    fi
    print_hybrid_commands
    ;;
  help)
    usage
    print_hybrid_commands
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
