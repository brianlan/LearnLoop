#!/usr/bin/env bash
#
# Reusable, worktree-isolated test environment for LearnLoop agents and reviewers.
#
# Usage: scripts/agent-env.sh <command> [args...]
#
# Commands:
#   build                       Build the lockfile-keyed tools image if absent.
#   shell                       Open an interactive shell with isolated MongoDB/RustFS.
#   test [backend|frontend|e2e|all]
#                               Run tests. Defaults to "all" when no selector is given.
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
  run_tools bash -c 'cd /workspace/backend && uv run --frozen --active pytest'
}

run_frontend_tests() {
  run_tools bash -c 'cd /workspace/frontend && npm test -- --run'
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
  run_tools bash -c 'cd /workspace/frontend && npm run test:ui' || rc=$?
  compose_cmd down --volumes || true
  return "$rc"
}

cmd_test() {
  local selector
  selector="${1:-all}"
  ensure_image "$(image_tag)"
  preflight
  case "$selector" in
    backend)
      run_backend_tests
      ;;
    frontend)
      run_frontend_tests
      ;;
    e2e)
      run_e2e_tests
      ;;
    all)
      run_backend_tests
      run_frontend_tests
      run_e2e_tests
      ;;
    *)
      printf 'Error: unknown test selector "%s". Use backend, frontend, e2e, or all.\n' "$selector" >&2
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
  test [backend|frontend|e2e|all]
                              Run tests. Defaults to "all" when no selector is given.
  down [--volumes]            Remove this worktree's agent stack.
  help                        Show this message.

Examples:
  scripts/agent-env.sh build
  scripts/agent-env.sh shell
  scripts/agent-env.sh test backend
  scripts/agent-env.sh test frontend
  scripts/agent-env.sh test e2e
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
