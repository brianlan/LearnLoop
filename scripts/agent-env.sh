#!/usr/bin/env bash
#
# Reusable, worktree-isolated test environment for LearnLoop agents and reviewers.
#
# Usage: scripts/agent-env.sh <command> [args...]
#
# Commands:
#   build                       Build the lockfile-keyed tools image if absent.
#   shell                       Open an interactive shell with isolated MongoDB/RustFS.
#   test [backend|backend-real|frontend|e2e|all] [runner args...]
#                               Run tests. Defaults to "all" when no selector is given.
#                               Focused selectors forward trailing arguments to the
#                               underlying runner; "all" accepts no trailing arguments.
#                               "backend-real" provisions MongoDB, runs the guarded
#                               real_mongo suite, proves selective cleanup, and tears
#                               down volumes (see run_backend_real_tests).
#   down [--volumes]            Remove this worktree's agent stack.
#   help                        Show usage.
#
# See DEVELOPMENT.md for full documentation.

set -euo pipefail

# shellcheck disable=SC2120
repo_root() {
  if [[ -n "${AGENT_ENV_TEST_REPO:-}" ]]; then
    printf '%s' "$AGENT_ENV_TEST_REPO"
    return
  fi
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "$dir/.." && pwd
}

# Compute a deterministic, Compose-valid project name from the canonical worktree
# path. Format: learnloop-agent-<sanitized-basename>-<short-path-hash>.
project_name() {
  local path safe hash
  path="$(cd "$(repo_root)" && pwd -P)"
  safe="$(basename "$path" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/-\+/-/g' | sed 's/^-//;s/-$//' )"
  hash="$(printf '%s' "$path" | sha256sum | awk '{print substr($1,1,8)}')"
  printf 'learnloop-agent-%s-%s' "${safe:0:30}" "$hash"
}

# Compute a content fingerprint from the dependency-only inputs that determine
# the reusable tools image and the seeded frontend dependency volume.
compute_fingerprint() {
  local root
  root="$(repo_root)"
  # Hash only file contents in a deterministic order; the resulting
  # fingerprint is independent of the worktree path.
  cat \
    "$root/Dockerfile.agent" \
    "$root/backend/pyproject.toml" \
    "$root/backend/uv.lock" \
    "$root/frontend/package.json" \
    "$root/frontend/package-lock.json" \
    | sha256sum \
    | awk '{print $1}'
}

image_tag() {
  local fingerprint
  fingerprint="${1:-$(compute_fingerprint)}"
  printf 'learnloop-agent-tools:%s' "$fingerprint"
}

compose_files() {
  local root
  root="$(repo_root)"
  printf '%s' "-f $root/docker-compose.yml -f $root/docker-compose.agent.yml"
}

compose_cmd() {
  local project
  project="${AGENT_PROJECT:-$(project_name)}"
  # shellcheck disable=SC2046
  docker compose $(compose_files) -p "$project" "$@"
}

preflight() {
  if ! compose_cmd config > /dev/null; then
    printf 'Error: Docker Compose preflight failed. Ensure docker compose v2+ is installed and the override file syntax is supported.\n' >&2
    return 1
  fi
}

# Ensure the lockfile-keyed tools image exists locally, building it only when
# the requested tag is absent.
ensure_image() {
  local tag
  tag="$1"
  if docker image inspect "$tag" > /dev/null 2>&1; then
    printf 'Reusing existing tools image: %s\n' "$tag"
    return 0
  fi
  printf 'Building tools image: %s\n' "$tag"
  local root
  root="$(repo_root)"
  docker build -t "$tag" -f "$root/Dockerfile.agent" "$root"
}

run_tools() {
  compose_cmd run --rm tools "$@"
}

cmd_build() {
  if [[ $# -ne 0 ]]; then
    printf 'Error: build takes no arguments.\n' >&2
    return 1
  fi
  ensure_image "$(image_tag)"
  preflight
}

cmd_shell() {
  ensure_image "$(image_tag)"
  preflight
  compose_cmd up -d mongodb rustfs
  compose_cmd --profile bootstrap run --rm rustfs-bootstrap
  printf 'Opening agent shell. Run "scripts/agent-env.sh down" to tear down when finished.\n'
  run_tools bash
}

run_backend_tests() {
  compose_cmd up -d mongodb
  run_tools bash -c 'cd /workspace/backend && uv run --frozen --active pytest "$@"' _ "$@"
}

# Poll the mongodb service until its replica set reports ready, with a bounded
# number of attempts. Returns nonzero on timeout.
wait_mongodb_ready() {
  local attempts="${1:-30}"
  local i
  for ((i = 1; i <= attempts; i++)); do
    if compose_cmd exec -T mongodb mongo --quiet --host 127.0.0.1 --port 27017 \
        --eval 'try { rs.status().ok === 1 ? quit(0) : quit(1) } catch (error) { quit(1) }' \
        >/dev/null 2>&1; then
      return 0
    fi
    printf '  waiting for MongoDB replica set... (%d/%d)\n' "$i" "$attempts"
    sleep 2
  done
  printf 'Error: MongoDB replica set did not become ready in time.\n' >&2
  return 1
}

# Run the guarded real-Mongo atomicity suite with a full, ordered, first-failure
# lifecycle: startup -> readiness -> control seed -> marked execution ->
# target/control probe -> volume teardown. Trailing pytest arguments are
# forwarded after the required -m/--require-real-mongo selection.
#
# Phase exit-code precedence: the first nonzero phase result wins; teardown
# overrides only an otherwise successful run. The per-run target database name
# is generated here and injected into the tools container so the T-12 (#552)
# validator accepts it.
run_backend_real_tests() {
  local first_rc=0
  local reached_control=0
  local target_db control_db control_marker
  target_db="learnloop_test_$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)"
  control_db="learnloop_test_control_$(printf '%s' "$target_db" | sed 's/^learnloop_test_//')"
  control_marker="__real_mongo_control_probe__"

  printf '[backend-real] target database: %s\n' "$target_db"
  printf '[backend-real] control database: %s\n' "$control_db"

  # Phase 1: startup.
  printf '[backend-real] phase 1/6: starting MongoDB\n'
  if [[ $first_rc -eq 0 ]]; then
    compose_cmd up -d mongodb || first_rc=$?
  fi

  # Phase 2: readiness.
  if [[ $first_rc -eq 0 ]]; then
    printf '[backend-real] phase 2/6: waiting for replica-set readiness\n'
    wait_mongodb_ready || first_rc=$?
  fi

  # Phase 3: control seed. Insert a marker document into a distinct control
  # database so the post-test probe can prove selective cleanup (target dropped,
  # control preserved). Uses the tools container's pymongo via python -c.
  if [[ $first_rc -eq 0 ]]; then
    printf '[backend-real] phase 3/6: seeding control database %s\n' "$control_db"
    local seed_rc=0
    compose_cmd run --rm \
        -e LEARNLOOP_REAL_MONGO_DATABASE="$target_db" \
        tools bash -c 'cd /workspace/backend && uv run --frozen --active python -c "
import os, sys
from pymongo import MongoClient
client = MongoClient(os.environ[\"MONGODB_URI\"], serverSelectionTimeoutMS=5000)
client[sys.argv[1]][\"__real_mongo_control__\"].insert_one({\"marker\": sys.argv[2]})
client.close()
" "$@"' _ "$control_db" "$control_marker" >/dev/null 2>&1 || seed_rc=$?
    first_rc=$seed_rc
    if [[ $seed_rc -eq 0 ]]; then
      reached_control=1
    fi
  fi

  # Phase 4: marked execution. Run every real_mongo node with --require-real-mongo
  # so zero-selection or any skip fails the session. Forward trailing pytest args.
  if [[ $first_rc -eq 0 ]]; then
    printf '[backend-real] phase 4/6: running real_mongo suite (target=%s)\n' "$target_db"
    local pytest_rc=0
    compose_cmd run --rm \
        -e LEARNLOOP_REAL_MONGO_DATABASE="$target_db" \
        tools bash -c 'cd /workspace/backend && uv run --frozen --active pytest -m real_mongo --require-real-mongo "$@"' _ "$@" || pytest_rc=$?
    first_rc=$pytest_rc
  fi

  # Phase 5: target/control probe. The target database must be absent (the
  # fixture drops it on teardown) and the control database must still contain
  # the marker document, proving selective cleanup. Runs only after the control
  # seed succeeded (otherwise there is nothing to probe), including after a
  # pytest failure so a cleanup regression does not hide behind a test failure.
  if [[ $reached_control -eq 1 ]]; then
    if [[ $first_rc -eq 0 ]]; then
      printf '[backend-real] phase 5/6: probing target absence and control presence\n'
    else
      printf '[backend-real] phase 5/6: probing target absence and control presence (after earlier failure rc=%d)\n' "$first_rc"
    fi
    local probe_rc=0
    compose_cmd run --rm \
        -e LEARNLOOP_REAL_MONGO_DATABASE="$target_db" \
        tools bash -c 'cd /workspace/backend && uv run --frozen --active python -c "
import os, sys
from pymongo import MongoClient
client = MongoClient(os.environ[\"MONGODB_URI\"], serverSelectionTimeoutMS=5000)
target_present = sys.argv[1] in client.list_database_names()
control_doc = client[sys.argv[2]][\"__real_mongo_control__\"].find_one({\"marker\": sys.argv[3]})
client.close()
if target_present:
    sys.exit(\"PROBE FAIL: target database still present after teardown\")
if control_doc is None:
    sys.exit(\"PROBE FAIL: control database marker missing\")
" "$@"' _ "$target_db" "$control_db" "$control_marker" >/dev/null 2>&1 || probe_rc=$?
    if [[ $first_rc -eq 0 ]]; then
      first_rc=$probe_rc
    fi
  fi

  # Phase 6: volume teardown. Always attempted. Only overrides the result when
  # every earlier phase passed; otherwise the first failure is preserved and a
  # teardown failure is logged but not fatal.
  printf '[backend-real] phase 6/6: tearing down volumes\n'
  local teardown_rc=0
  compose_cmd down --volumes >/dev/null 2>&1 || teardown_rc=$?
  if [[ $teardown_rc -ne 0 ]]; then
    printf '[backend-real] warning: teardown failed (rc=%d); logged but not overriding first failure.\n' "$teardown_rc" >&2
    if [[ $first_rc -eq 0 ]]; then
      first_rc=$teardown_rc
    fi
  fi

  return "$first_rc"
}

run_frontend_tests() {
  run_tools bash -c 'cd /workspace/frontend && npm test -- --run "$@"' _ "$@"
}

run_e2e_tests() {
  local rc
  rc=0
  compose_cmd up -d mongodb rustfs || rc=$?
  if [[ $rc -ne 0 ]]; then
    compose_cmd down --volumes || true
    return "$rc"
  fi
  compose_cmd --profile bootstrap run --rm rustfs-bootstrap || rc=$?
  if [[ $rc -ne 0 ]]; then
    compose_cmd down --volumes || true
    return "$rc"
  fi
  run_tools bash -c 'cd /workspace/frontend && npm run test:ui "$@"' _ "$@" || rc=$?
  compose_cmd down --volumes || true
  return "$rc"
}

cmd_test() {
  local selector
  selector="${1:-all}"
  if [[ $# -gt 0 ]]; then
    shift
  fi
  case "$selector" in
    backend)
      ensure_image "$(image_tag)"
      preflight
      run_backend_tests "$@"
      ;;
    backend-real)
      ensure_image "$(image_tag)"
      preflight
      run_backend_real_tests "$@"
      ;;
    frontend)
      ensure_image "$(image_tag)"
      preflight
      run_frontend_tests "$@"
      ;;
    e2e)
      ensure_image "$(image_tag)"
      preflight
      run_e2e_tests "$@"
      ;;
    all)
      if [[ $# -gt 0 ]]; then
        printf 'Error: "test all" accepts no trailing arguments (got: %s). Use a focused selector (backend, frontend, e2e) to forward arguments.\n' "$*" >&2
        return 1
      fi
      ensure_image "$(image_tag)"
      preflight
      run_backend_tests
      run_frontend_tests
      run_e2e_tests
      ;;
    *)
      printf 'Error: unknown test selector "%s". Use backend, backend-real, frontend, e2e, or all.\n' "$selector" >&2
      return 1
      ;;
  esac
}

cmd_down() {
  local volumes=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --volumes)
        volumes="--volumes"
        ;;
      *)
        printf 'Error: unknown down option "%s". Use --volumes.\n' "$1" >&2
        return 1
        ;;
    esac
    shift
  done
  preflight
  if [[ -n "$volumes" ]]; then
    compose_cmd down --volumes
  else
    compose_cmd down
  fi
}

usage() {
  cat <<'EOF'
Usage: scripts/agent-env.sh <command> [args...]

Commands:
  build                       Build the lockfile-keyed tools image if absent.
  shell                       Open an interactive shell with isolated MongoDB/RustFS.
  test [backend|backend-real|frontend|e2e|all] [runner args...]
                              Run tests. Defaults to "all" when no selector is given.
                              Focused selectors forward trailing arguments to the
                              underlying runner; "all" accepts no trailing arguments.
                              "backend-real" provisions MongoDB, runs the guarded
                              real_mongo suite, proves selective cleanup, and tears
                              down volumes.
  down [--volumes]            Remove this worktree's agent stack.
  help                        Show this message.

Examples:
  scripts/agent-env.sh build
  scripts/agent-env.sh shell
  scripts/agent-env.sh test backend
  scripts/agent-env.sh test backend tests/api/test_practice.py
  scripts/agent-env.sh test backend-real
  scripts/agent-env.sh test backend-real tests/integration/test_ingestion_atomicity.py -x
  scripts/agent-env.sh test frontend --reporter verbose
  scripts/agent-env.sh test e2e tests/login.spec.ts
  scripts/agent-env.sh test all
  scripts/agent-env.sh down
  scripts/agent-env.sh down --volumes
EOF
}

main() {
  local root fingerprint tag
  root="$(repo_root)"
  fingerprint="$(compute_fingerprint)"
  tag="$(image_tag "$fingerprint")"

  export AGENT_WORKTREE="$root"
  export AGENT_FINGERPRINT="$fingerprint"
  export AGENT_IMAGE="$tag"
  export AGENT_PROJECT="$(project_name)"
  export HOST_UID HOST_GID
  HOST_UID="$(id -u)"
  HOST_GID="$(id -g)"
  export S3_ACCESS_KEY="${S3_ACCESS_KEY:-learnloop-local}"
  export S3_SECRET_KEY="${S3_SECRET_KEY:-learnloop-secret}"
  export S3_BUCKET="${S3_BUCKET:-learnloop-media}"
  export S3_REGION="${S3_REGION:-us-east-1}"

  local command
  command="${1:-help}"
  if [[ $# -gt 0 ]]; then
    shift
  fi

  case "$command" in
    build)
      cmd_build "$@"
      ;;
    shell)
      cmd_shell "$@"
      ;;
    test)
      cmd_test "$@"
      ;;
    down)
      cmd_down "$@"
      ;;
    help|--help|-h)
      usage
      ;;
    *)
      printf 'Error: unknown command "%s".\n' "$command" >&2
      usage >&2
      return 1
      ;;
  esac
}

# Only execute main when this script is run directly, not when sourced for
# shell-level regression tests.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
