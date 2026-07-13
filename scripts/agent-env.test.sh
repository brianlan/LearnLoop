#!/usr/bin/env bash
#
# Shell-level regression tests for scripts/agent-env.sh.
# Run from the repository root: bash scripts/agent-env.test.sh

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

# Source the implementation functions without running main.
AGENT_ENV_TEST_REPO="$repo_root"
# shellcheck source=agent-env.sh
source "$script_dir/agent-env.sh"

passed=0
failed=0

pass() {
  printf '  PASS: %s\n' "$1"
  passed=$((passed + 1))
}

fail() {
  printf '  FAIL: %s\n' "$1" >&2
  failed=$((failed + 1))
}

assert_eq() {
  if [ "$2" = "$3" ]; then
    pass "$1"
  else
    fail "$1 (expected $3, got $2)"
  fi
}

assert_neq() {
  if [ "$2" != "$3" ]; then
    pass "$1"
  else
    fail "$1 (expected different values, both $2)"
  fi
}

reset_repo_root() {
  AGENT_ENV_TEST_REPO="$repo_root"
}

setup_env() {
  reset_repo_root
  export AGENT_WORKTREE="$repo_root"
  export AGENT_FINGERPRINT="$(compute_fingerprint)"
  export AGENT_IMAGE="$(image_tag)"
  export AGENT_PROJECT="$(project_name)"
  export HOST_UID="$(id -u)"
  export HOST_GID="$(id -g)"
}

run_agent_env() {
  "$script_dir/agent-env.sh" "$@"
}

test_project_name() {
  printf '%s\n' "test_project_name"
  local names=() p1 p2
  for dir in /tmp/agent-env-test-normal \
             "/tmp/agent-env test spaces" \
             "/tmp/agent-env_test!special@chars#123" \
             "/tmp/UPPERCASE_WorkTree"; do
    rm -rf "$dir"
    mkdir -p "$dir"
    AGENT_ENV_TEST_REPO="$dir"
    p1="$(project_name)"
    p2="$(project_name)"
    assert_eq "project name is deterministic for $dir" "$p1" "$p2"
    if printf '%s' "$p1" | grep -Eq '^learnloop-agent-[a-z0-9-]+-[a-f0-9]{8}$'; then
      pass "project name matches expected pattern: $p1"
    else
      fail "project name matches expected pattern: $p1"
    fi
    if [ "${#p1}" -le 63 ]; then
      pass "project name length is valid for $p1"
    else
      fail "project name length is valid for $p1"
    fi
    names+=("$p1")
    rm -rf "$dir"
  done
  reset_repo_root
  # All generated names must be distinct.
  local unique
  unique="$(printf '%s\n' "${names[@]}" | sort -u | wc -l | tr -d ' ')"
  assert_eq "project names are unique across sample paths" "$unique" "${#names[@]}"
}

test_fingerprint_stable() {
  printf '%s\n' "test_fingerprint_stable"
  reset_repo_root
  local a b
  a="$(compute_fingerprint)"
  b="$(compute_fingerprint)"
  assert_eq "fingerprint is deterministic" "$a" "$b"
  if [ "${#a}" -eq 64 ] && printf '%s' "$a" | grep -Eq '^[a-f0-9]+$'; then
    pass "fingerprint is a 64-char hex sha256"
  else
    fail "fingerprint is a 64-char hex sha256"
  fi
}

test_fingerprint_changes() {
  printf '%s\n' "test_fingerprint_changes"
  local tmpdir original changed after
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"; reset_repo_root' RETURN
  cp -a "$repo_root/backend" "$repo_root/frontend" "$repo_root/Dockerfile.agent" "$tmpdir/"
  reset_repo_root
  original="$(compute_fingerprint)"
  AGENT_ENV_TEST_REPO="$tmpdir"
  changed="$(compute_fingerprint)"
  assert_eq "fingerprint unchanged when inputs copied" "$original" "$changed"
  printf '\n# changed\n' >> "$tmpdir/Dockerfile.agent"
  after="$(compute_fingerprint)"
  assert_neq "fingerprint changes when Dockerfile.agent changes" "$changed" "$after"
  assert_neq "fingerprint differs from original repo" "$original" "$after"
  rm -rf "$tmpdir"
  reset_repo_root
  trap - RETURN
}

test_invalid_args() {
  printf '%s\n' "test_invalid_args"
  reset_repo_root
  local rc=0
  run_agent_env not-a-command && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "unknown command exits non-zero"; else fail "unknown command exits non-zero (rc=$rc)"; fi

  rc=0
  run_agent_env test invalid-selector && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "invalid test selector exits non-zero"; else fail "invalid test selector exits non-zero (rc=$rc)"; fi

  rc=0
  run_agent_env down --bad-option && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "invalid down option exits non-zero"; else fail "invalid down option exits non-zero (rc=$rc)"; fi

  rc=0
  run_agent_env build extra-arg && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "build with extra args exits non-zero"; else fail "build with extra args exits non-zero (rc=$rc)"; fi

  rc=0
  run_agent_env help && rc=$? || rc=$?
  if [ "$rc" -eq 0 ]; then pass "help exits zero"; else fail "help exits zero (rc=$rc)"; fi
}

test_config_no_fixed_names_no_host_ports() {
  printf '%s\n' "test_config_no_fixed_names_no_host_ports"
  reset_repo_root
  setup_env
  local config
  config="$(compose_cmd config)"
  if grep -q 'container_name:' <<< "$config"; then
    fail "config contains no container_name directives"
  else
    pass "config contains no container_name directives"
  fi
  # The base file's host-published infrastructure ports must not appear.
  local pattern
  for pattern in 'published: "27017"' 'published: "9000"' 'published: "9001"' 'published: "8000"' 'published: "8080"'; do
    if grep -E "$pattern" <<< "$config" >/dev/null; then
      fail "config has no host-published port matching $pattern"
    else
      pass "config has no host-published port matching $pattern"
    fi
  done
}

test_image_build_and_exit_code() {
  printf '%s\n' "test_image_build_and_exit_code"
  reset_repo_root
  setup_env
  run_agent_env build
  local rc=0
  compose_cmd run --rm tools bash -c 'exit 42' && rc=$? || rc=$?
  assert_eq "failing wrapped command preserves exit code 42" "$rc" "42"

  rc=0
  compose_cmd run --rm tools bash -c 'exit 0' && rc=$? || rc=$?
  assert_eq "successful wrapped command exits 0" "$rc" "0"
}

test_cleanup_scoped_to_worktree() {
  printf '%s\n' "test_cleanup_scoped_to_worktree"
  local tmpdir p1 p2
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"; reset_repo_root' RETURN
  cp -a "$repo_root/backend" "$repo_root/frontend" "$repo_root/docker-compose.yml" "$repo_root/docker-compose.agent.yml" "$repo_root/scripts" "$repo_root/Dockerfile.agent" "$tmpdir/"

  # Use the same dependency inputs, so the image is reused.
  AGENT_ENV_TEST_REPO="$tmpdir"
  export AGENT_WORKTREE="$tmpdir"
  export AGENT_FINGERPRINT="$(compute_fingerprint)"
  export AGENT_IMAGE="$(image_tag)"
  p1="$(project_name)"

  reset_repo_root
  export AGENT_WORKTREE="$repo_root"
  export AGENT_FINGERPRINT="$(compute_fingerprint)"
  export AGENT_IMAGE="$(image_tag)"
  p2="$(project_name)"

  assert_neq "different worktrees produce different project names" "$p1" "$p2"

  # Start isolated MongoDB containers for each project and confirm that
  # tearing down one does not affect the other.
  AGENT_WORKTREE="$tmpdir" AGENT_PROJECT="$p1" AGENT_FINGERPRINT="$AGENT_FINGERPRINT" \
    AGENT_IMAGE="$AGENT_IMAGE" HOST_UID="$(id -u)" HOST_GID="$(id -g)" \
    docker compose $(compose_files) -p "$p1" up -d mongodb > /dev/null
  AGENT_WORKTREE="$repo_root" AGENT_PROJECT="$p2" AGENT_FINGERPRINT="$(compute_fingerprint)" \
    AGENT_IMAGE="$(image_tag)" HOST_UID="$(id -u)" HOST_GID="$(id -g)" \
    docker compose $(compose_files) -p "$p2" up -d mongodb > /dev/null

  if [ "$(docker ps -aq --filter "name=^/${p1}-" | wc -l | tr -d ' ')" -ge 1 ]; then
    pass "project $p1 has a running container"
  else
    fail "project $p1 has a running container"
  fi
  if [ "$(docker ps -aq --filter "name=^/${p2}-" | wc -l | tr -d ' ')" -ge 1 ]; then
    pass "project $p2 has a running container"
  else
    fail "project $p2 has a running container"
  fi

  # Tear down only p1.
  AGENT_WORKTREE="$tmpdir" AGENT_PROJECT="$p1" AGENT_FINGERPRINT="$AGENT_FINGERPRINT" \
    AGENT_IMAGE="$AGENT_IMAGE" HOST_UID="$(id -u)" HOST_GID="$(id -g)" \
    docker compose $(compose_files) -p "$p1" down > /dev/null

  if [ "$(docker ps -aq --filter "name=^/${p1}-" | wc -l | tr -d ' ')" -eq 0 ]; then
    pass "project $p1 has no remaining containers"
  else
    fail "project $p1 has no remaining containers"
  fi
  if [ "$(docker ps -aq --filter "name=^/${p2}-" | wc -l | tr -d ' ')" -ge 1 ]; then
    pass "project $p2 still has containers after $p1 was torn down"
  else
    fail "project $p2 still has containers after $p1 was torn down"
  fi

  # Cleanup p2 as well.
  AGENT_WORKTREE="$repo_root" AGENT_PROJECT="$p2" AGENT_FINGERPRINT="$(compute_fingerprint)" \
    AGENT_IMAGE="$(image_tag)" HOST_UID="$(id -u)" HOST_GID="$(id -g)" \
    docker compose $(compose_files) -p "$p2" down --volumes > /dev/null
  rm -rf "$tmpdir"
  reset_repo_root
  trap - RETURN
}

main() {
  printf 'Running agent-env.sh regression tests in %s\n' "$repo_root"
  reset_repo_root
  test_project_name
  test_fingerprint_stable
  test_fingerprint_changes
  test_invalid_args
  test_config_no_fixed_names_no_host_ports
  test_image_build_and_exit_code
  test_cleanup_scoped_to_worktree

  printf '\nResults: %d passed, %d failed\n' "$passed" "$failed"
  if [ "$failed" -gt 0 ]; then
    return 1
  fi
}

main "$@"
