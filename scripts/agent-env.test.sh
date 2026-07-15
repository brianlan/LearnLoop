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

test_cleanup_help_exits_zero() {
  printf '%s\n' "test_cleanup_help_exits_zero"
  local rc=0
  bash "$script_dir/agent-env-cleanup.sh" --help && rc=$? || rc=$?
  if [ "$rc" -eq 0 ]; then pass "cleanup --help exits zero"; else fail "cleanup --help exits zero (rc=$rc)"; fi
}

test_cleanup_invalid_args_nonzero() {
  printf '%s\n' "test_cleanup_invalid_args_nonzero"
  local rc=0
  bash "$script_dir/agent-env-cleanup.sh" --bad-option && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "cleanup with bad option exits non-zero"; else fail "cleanup with bad option exits non-zero (rc=$rc)"; fi

  rc=0
  bash "$script_dir/agent-env-cleanup.sh" --keep-images && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "cleanup --keep-images without value exits non-zero"; else fail "cleanup --keep-images without value exits non-zero (rc=$rc)"; fi

  rc=0
  bash "$script_dir/agent-env-cleanup.sh" --keep-images abc && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "cleanup --keep-images with non-numeric exits non-zero"; else fail "cleanup --keep-images with non-numeric exits non-zero (rc=$rc)"; fi
}

test_cleanup_dry_run_no_changes() {
  printf '%s\n' "test_cleanup_dry_run_no_changes"
  local output
  output="$(bash "$script_dir/agent-env-cleanup.sh" --dry-run 2>&1)" || true
  if grep -q '\[dry-run\]' <<< "$output"; then
    pass "dry-run output contains [dry-run] markers"
  else
    fail "dry-run output contains [dry-run] markers"
  fi
  if grep -q 'would remove\|would run' <<< "$output"; then
    pass "dry-run output shows intended actions"
  else
    fail "dry-run output shows intended actions"
  fi
}

test_cleanup_dry_run_help_text() {
  printf '%s\n' "test_cleanup_dry_run_help_text"
  local output
  output="$(bash "$script_dir/agent-env-cleanup.sh" --help 2>&1)" || true
  if grep -q 'dry-run' <<< "$output"; then
    pass "help text mentions --dry-run"
  else
    fail "help text mentions --dry-run"
  fi
  if grep -q 'keep-images' <<< "$output"; then
    pass "help text mentions --keep-images"
  else
    fail "help text mentions --keep-images"
  fi
}

test_cleanup_keep_images_logic() {
  printf '%s\n' "test_cleanup_keep_images_logic"
  local output
  output="$(bash "$script_dir/agent-env-cleanup.sh" --dry-run --keep-images 5 2>&1)" || true
  if grep -qi 'keep.*5' <<< "$output"; then
    pass "dry-run shows keep-images=5"
  else
    fail "dry-run shows keep-images=5"
  fi
}

# ---------------------------------------------------------------------------
# Deterministic stub-based regression tests for scripts/agent-env-cleanup.sh.
# These run the cleanup script with fake `docker`/`git` on PATH so they never
# touch the developer's real Docker daemon or git worktree metadata.
# ---------------------------------------------------------------------------

# Create a temp bin dir containing fake `docker` and `git`, set up env, and
# export globals: STUB_BIN_DIR, STUB_MUTATIONS. Caller is in repo root.
setup_stub_env() {
  STUB_BIN_DIR="$(mktemp -d)"
  STUB_MUTATIONS="$(mktemp)"
  : > "$STUB_MUTATIONS"

  cat > "$STUB_BIN_DIR/docker" <<'DOCKER_EOF'
#!/usr/bin/env bash
set -euo pipefail
MUT="${STUB_MUTATIONS:-/dev/null}"
case "${1:-}" in
  info)
    [[ "${STUB_DOCKER_INFO:-0}" == "1" ]] && exit 1
    exit 0
    ;;
  volume)
    case "${2:-}" in
      ls)
        [[ "${STUB_VOLUME_LS_FAIL:-0}" == "1" ]] && { printf 'volume ls boom\n' >&2; exit 1; }
        printf '%s\n' "${STUB_VOLUMES:-}" | sed '/^$/d'
        ;;
      rm) printf 'docker volume rm %s\n' "${3:-}" >> "$MUT" ;;
    esac
    ;;
  ps)
    [[ "${STUB_PS_FAIL:-0}" == "1" ]] && { printf 'ps boom\n' >&2; exit 1; }
    name=""
    for a in "$@"; do case "$a" in volume=*) name="${a#volume=}" ;; esac; done
    for v in ${STUB_INUSE_VOLUMES:-}; do
      [[ "$v" == "$name" ]] && { printf 'some-container\n'; exit 0; }
    done
    ;;
  images)
    [[ "${STUB_IMAGES_FAIL:-0}" == "1" ]] && { printf 'images boom\n' >&2; exit 1; }
    dangling=false
    for a in "$@"; do case "$a" in dangling=true) dangling=true ;; esac; done
    if [[ "$dangling" == true ]]; then
      printf '%s\n' "${STUB_DANGLING:-}" | sed '/^$/d'
    else
      printf '%s\n' "${STUB_IMAGES:-}" | sed '/^$/d'
    fi
    ;;
  rmi) printf 'docker rmi %s\n' "${2:-}" >> "$MUT" ;;
  image)
    printf 'docker image prune %s\n' "${2:-}" >> "$MUT"
    ;;
  buildx)
    case "${2:-}" in
      version) [[ "${STUB_BUILDX:-0}" == "0" ]] && exit 0 || exit 1 ;;
      prune)
        if [[ "${3:-}" == "--help" ]]; then
          [[ "${STUB_BUILDX:-0}" == "0" ]] && printf -- '--reserved-space\n'
          exit 0
        fi
        printf 'docker buildx prune %s\n' "${*:3}" >> "$MUT"
        ;;
      du) printf 'RECLAIMABLE 1.0GB\n' ;;
    esac
    ;;
  builder)
    case "${2:-}" in
      prune)
        if [[ "${3:-}" == "--help" ]]; then
          [[ "${STUB_BUILDER_KEEPSTORAGE:-0}" == "0" ]] && printf -- '--keep-storage\n'
          exit 0
        fi
        printf 'docker builder prune %s\n' "${*:3}" >> "$MUT"
        ;;
    esac
    ;;
esac
exit 0
DOCKER_EOF
  chmod +x "$STUB_BIN_DIR/docker"

  cat > "$STUB_BIN_DIR/git" <<'GIT_EOF'
#!/usr/bin/env bash
set -euo pipefail
MUT="${STUB_MUTATIONS:-/dev/null}"
case "${1:-}" in
  worktree)
    case "${2:-}" in
      prune)
        if [[ "${3:-}" == "--dry-run" ]]; then
          printf 'would prune: /tmp/stale-worktree\n'
        else
          printf 'git worktree prune\n' >> "$MUT"
        fi
        ;;
    esac
    ;;
esac
exit 0
GIT_EOF
  chmod +x "$STUB_BIN_DIR/git"

  export PATH="$STUB_BIN_DIR:$PATH"
  export STUB_MUTATIONS STUB_DOCKER_INFO=0 STUB_VOLUMES="" STUB_INUSE_VOLUMES=""
  export STUB_IMAGES="" STUB_DANGLING="" STUB_BUILDX=0 STUB_BUILDER_KEEPSTORAGE=0
  export STUB_VOLUME_LS_FAIL=0 STUB_PS_FAIL=0 STUB_IMAGES_FAIL=0
}

teardown_stub_env() {
  [ -n "${STUB_BIN_DIR:-}" ] && rm -rf "$STUB_BIN_DIR"
  [ -n "${STUB_MUTATIONS:-}" ] && rm -f "$STUB_MUTATIONS"
  unset STUB_BIN_DIR STUB_MUTATIONS STUB_DOCKER_INFO STUB_VOLUMES \
    STUB_INUSE_VOLUMES STUB_IMAGES STUB_DANGLING STUB_BUILDX STUB_BUILDER_KEEPSTORAGE \
    STUB_VOLUME_LS_FAIL STUB_PS_FAIL STUB_IMAGES_FAIL
}

# Run the cleanup script under the stubbed PATH. Args passed through.
run_cleanup_stubbed() {
  STUB_MUTATIONS="$STUB_MUTATIONS" \
    env PATH="$STUB_BIN_DIR:$PATH" \
    bash "$script_dir/agent-env-cleanup.sh" "$@"
}

mutations_log() { cat "$STUB_MUTATIONS" 2>/dev/null || true; }
mutations_count() { wc -l < "$STUB_MUTATIONS" 2>/dev/null | tr -d ' ' || printf '0'; }

test_cleanup_default_never_prunes() {
  printf '%s\n' "test_cleanup_default_never_prunes"
  setup_stub_env
  STUB_VOLUMES="learnloop-agent-abc
learnloop-agent-def" STUB_IMAGES="2026-07-01 00:00:00 +0000	learnloop-agent-tools:old
2026-07-10 00:00:00 +0000	learnloop-agent-tools:new" \
    run_cleanup_stubbed >/dev/null 2>&1
  local log
  log="$(mutations_log)"
  if printf '%s' "$log" | grep -q 'image prune\|buildx prune\|builder prune'; then
    fail "default run must not invoke any daemon-wide prune"
  else
    pass "default run never invokes daemon-wide prune"
  fi
  teardown_stub_env
}

test_cleanup_global_prune_invokes_commands() {
  printf '%s\n' "test_cleanup_global_prune_invokes_commands"
  setup_stub_env
  STUB_BUILDX=0 STUB_IMAGES="2026-07-01 00:00:00 +0000	learnloop-agent-tools:old
2026-07-10 00:00:00 +0000	learnloop-agent-tools:new" \
    run_cleanup_stubbed --global-prune >/dev/null 2>&1
  local log
  log="$(mutations_log)"
  if printf '%s' "$log" | grep -q 'docker image prune'; then
    pass "--global-prune invokes docker image prune"
  else
    fail "--global-prune invokes docker image prune"
  fi
  if printf '%s' "$log" | grep -q 'buildx prune.*--reserved-space 10GB'; then
    pass "--global-prune uses buildx prune with --reserved-space 10GB"
  else
    fail "--global-prune uses buildx prune with --reserved-space 10GB"
  fi
  teardown_stub_env
}

test_cleanup_dry_run_global_no_mutations() {
  printf '%s\n' "test_cleanup_dry_run_global_no_mutations"
  setup_stub_env
  export STUB_BUILDX=0 STUB_VOLUMES="learnloop-agent-abc"
  export STUB_DANGLING="deadbeef	other-project:latest	100MB"
  export STUB_IMAGES="2026-07-01 00:00:00 +0000	learnloop-agent-tools:old
2026-07-05 00:00:00 +0000	learnloop-agent-tools:mid
2026-07-10 00:00:00 +0000	learnloop-agent-tools:new"
  local out rc
  out="$(run_cleanup_stubbed --dry-run --global-prune 2>&1)" && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "dry-run --global-prune should exit zero (rc=$rc)"
  else
    pass "dry-run --global-prune exits zero"
  fi
  if [ "$(mutations_count)" -ne 0 ]; then
    fail "dry-run --global-prune must not mutate (log: $(mutations_log))"
  else
    pass "dry-run --global-prune makes no mutations"
  fi
  if printf '%s' "$out" | grep -q 'other-project:latest'; then
    pass "dry-run --global-prune lists dangling image candidates"
  else
    fail "dry-run --global-prune lists dangling image candidates"
  fi
  if printf '%s' "$out" | grep -q 'reserved-space 10GB'; then
    pass "dry-run --global-prune shows selected guarded command"
  else
    fail "dry-run --global-prune shows selected guarded command"
  fi
  if printf '%s' "$out" | grep -q 'WARNING'; then
    pass "dry-run --global-prune prints cross-project warning"
  else
    fail "dry-run --global-prune prints cross-project warning"
  fi
  teardown_stub_env
}

test_cleanup_buildx_capability_selection() {
  printf '%s\n' "test_cleanup_buildx_capability_selection"
  setup_stub_env
  # Modern buildx with --reserved-space.
  STUB_BUILDX=0 run_cleanup_stubbed --global-prune >/dev/null 2>&1
  if mutations_log | grep -q 'buildx prune.*--reserved-space 10GB'; then
    pass "modern buildx selects --reserved-space 10GB"
  else
    fail "modern buildx selects --reserved-space 10GB"
  fi
  teardown_stub_env

  setup_stub_env
  # No buildx, but legacy builder has --keep-storage.
  STUB_BUILDX=1 STUB_BUILDER_KEEPSTORAGE=0 run_cleanup_stubbed --global-prune >/dev/null 2>&1
  if mutations_log | grep -q 'builder prune.*--keep-storage 10GB'; then
    pass "legacy builder selects --keep-storage 10GB"
  else
    fail "legacy builder selects --keep-storage 10GB"
  fi
  teardown_stub_env

  setup_stub_env
  # Neither capacity flag available: must fail during planning before any
  # global or scoped mutation, including docker image prune.
  local rc=0
  STUB_BUILDX=1 STUB_BUILDER_KEEPSTORAGE=1 run_cleanup_stubbed --global-prune >/dev/null 2>&1 && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then
    pass "unsupported CLI fails non-zero without pruning cache"
  else
    fail "unsupported CLI fails non-zero without pruning cache (rc=$rc)"
  fi
  if mutations_log | grep -q 'buildx prune\|builder prune'; then
    fail "unsupported CLI must not run any cache prune"
  else
    pass "unsupported CLI runs no cache prune"
  fi
  # With two-phase planning, capability selection happens BEFORE mutation, so
  # docker image prune must NOT run when global cache capability is unsupported.
  if mutations_log | grep -q 'docker image prune'; then
    fail "unsupported CLI must not run docker image prune before failing (planning aborts first)"
  else
    pass "unsupported CLI runs no docker image prune (planning aborts before mutation)"
  fi
  teardown_stub_env
}

test_cleanup_default_dry_run_scoped_nonmutating() {
  printf '%s\n' "test_cleanup_default_dry_run_scoped_nonmutating"
  setup_stub_env
  export STUB_VOLUMES="learnloop-agent-abc
learnloop-agent-def"
  export STUB_IMAGES="2026-07-01 00:00:00 +0000	learnloop-agent-tools:old
2026-07-05 00:00:00 +0000	learnloop-agent-tools:mid
2026-07-10 00:00:00 +0000	learnloop-agent-tools:new"
  local out rc
  out="$(run_cleanup_stubbed --dry-run 2>&1)" && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then fail "default dry-run exits zero (rc=$rc)"; else pass "default dry-run exits zero"; fi
  if [ "$(mutations_count)" -ne 0 ]; then
    fail "default dry-run must not mutate (log: $(mutations_log))"
  else
    pass "default dry-run makes no mutations"
  fi
  if printf '%s' "$out" | grep -q 'learnloop-agent-abc'; then
    pass "default dry-run lists scoped volume candidates"
  else
    fail "default dry-run lists scoped volume candidates"
  fi
  if printf '%s' "$out" | grep -q 'learnloop-agent-tools:old'; then
    pass "default dry-run lists scoped image candidates"
  else
    fail "default dry-run lists scoped image candidates"
  fi
  teardown_stub_env
}

test_cleanup_help_without_docker() {
  printf '%s\n' "test_cleanup_help_without_docker"
  setup_stub_env
  STUB_DOCKER_INFO=1
  local out rc
  out="$(run_cleanup_stubbed --help 2>&1)" && rc=$? || rc=$?
  if [ "$rc" -eq 0 ]; then pass "--help exits zero without Docker"; else fail "--help exits zero without Docker (rc=$rc)"; fi
  if printf '%s' "$out" | grep -q 'global-prune'; then
    pass "--help documents --global-prune"
  else
    fail "--help documents --global-prune"
  fi
  teardown_stub_env
}

test_cleanup_docker_unavailable_nonzero() {
  printf '%s\n' "test_cleanup_docker_unavailable_nonzero"
  setup_stub_env
  STUB_DOCKER_INFO=1
  local out rc
  out="$(run_cleanup_stubbed --dry-run 2>&1)" && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "Docker unavailable exits non-zero"; else fail "Docker unavailable exits non-zero (rc=$rc)"; fi
  if printf '%s' "$out" | grep -qi 'Docker daemon is not available'; then
    pass "Docker unavailable prints clear error"
  else
    fail "Docker unavailable prints clear error"
  fi
  # Must not claim zero resources were found.
  if printf '%s' "$out" | grep -q 'No unused agent volumes found'; then
    fail "Docker unavailable must not print misleading zero-resource success"
  else
    pass "Docker unavailable does not print misleading zero-resource success"
  fi
  teardown_stub_env
}

test_cleanup_volume_prefix_strict() {
  printf '%s\n' "test_cleanup_volume_prefix_strict"
  setup_stub_env
  # Substring trap: "backup-learnloop-agent-data" must NEVER be selected.
  STUB_VOLUMES="learnloop-agent-keep
backup-learnloop-agent-data
learnloop-agent-drop" \
    run_cleanup_stubbed >/dev/null 2>&1
  local log
  log="$(mutations_log)"
  if printf '%s' "$log" | grep -q 'docker volume rm backup-learnloop-agent-data'; then
    fail "substring volume must never be removed"
  else
    pass "substring volume is never removed"
  fi
  if printf '%s' "$log" | grep -q 'docker volume rm learnloop-agent-keep'; then
    pass "true-prefix volume learnloop-agent-keep is removed"
  else
    fail "true-prefix volume learnloop-agent-keep is removed"
  fi
  if printf '%s' "$log" | grep -q 'docker volume rm learnloop-agent-drop'; then
    pass "true-prefix volume learnloop-agent-drop is removed"
  else
    fail "true-prefix volume learnloop-agent-drop is removed"
  fi
  teardown_stub_env
}

test_cleanup_volume_inuse_protected() {
  printf '%s\n' "test_cleanup_volume_inuse_protected"
  setup_stub_env
  STUB_VOLUMES="learnloop-agent-free
learnloop-agent-used" \
    STUB_INUSE_VOLUMES="learnloop-agent-used" \
    run_cleanup_stubbed >/dev/null 2>&1
  local log
  log="$(mutations_log)"
  if printf '%s' "$log" | grep -q 'docker volume rm learnloop-agent-used'; then
    fail "in-use volume must be protected"
  else
    pass "in-use volume is protected"
  fi
  if printf '%s' "$log" | grep -q 'docker volume rm learnloop-agent-free'; then
    pass "free volume is removed"
  else
    fail "free volume is removed"
  fi
  teardown_stub_env
}

test_cleanup_dry_run_worktree_preview() {
  printf '%s\n' "test_cleanup_dry_run_worktree_preview"
  setup_stub_env
  local out rc
  out="$(run_cleanup_stubbed --dry-run 2>&1)" && rc=$? || rc=$?
  if [ "$rc" -eq 0 ]; then pass "dry-run worktree preview exits zero"; else fail "dry-run worktree preview exits zero (rc=$rc)"; fi
  if printf '%s' "$out" | grep -q 'would prune'; then
    pass "dry-run uses git worktree prune --dry-run --verbose output"
  else
    fail "dry-run uses git worktree prune --dry-run --verbose output"
  fi
  if [ "$(mutations_count)" -ne 0 ]; then
    fail "dry-run must not mutate worktree metadata"
  else
    pass "dry-run does not mutate worktree metadata"
  fi
  teardown_stub_env
}

test_cleanup_normal_mode_worktree_prune() {
  printf '%s\n' "test_cleanup_normal_mode_worktree_prune"
  setup_stub_env
  run_cleanup_stubbed >/dev/null 2>&1
  if mutations_log | grep -q 'git worktree prune'; then
    pass "normal mode runs git worktree prune"
  else
    fail "normal mode runs git worktree prune"
  fi
  teardown_stub_env
}

test_cleanup_help_global_prune_documented() {
  printf '%s\n' "test_cleanup_help_global_prune_documented"
  local out
  out="$(bash "$script_dir/agent-env-cleanup.sh" --help 2>&1)" || true
  if printf '%s' "$out" | grep -qi 'global-prune'; then
    pass "help text mentions --global-prune"
  else
    fail "help text mentions --global-prune"
  fi
}

test_cleanup_keep_images_stubbed() {
  printf '%s\n' "test_cleanup_keep_images_stubbed"
  setup_stub_env
  STUB_IMAGES="2026-07-01 00:00:00 +0000	learnloop-agent-tools:a
2026-07-02 00:00:00 +0000	learnloop-agent-tools:b
2026-07-03 00:00:00 +0000	learnloop-agent-tools:c
2026-07-04 00:00:00 +0000	learnloop-agent-tools:d" \
    run_cleanup_stubbed --keep-images 2 >/dev/null 2>&1
  local log removed
  log="$(mutations_log)"
  removed="$(printf '%s\n' "$log" | grep -c 'docker rmi' || true)"
  # 4 images, keep 2 newest (c,d), remove a,b -> 2 rmi calls.
  if [ "$removed" -eq 2 ]; then
    pass "keep-images=2 removes 2 of 4 agent tools images"
  else
    fail "keep-images=2 removes 2 of 4 agent tools images (got $removed)"
  fi
  if printf '%s' "$log" | grep -q 'docker rmi learnloop-agent-tools:a'; then
    pass "oldest image a is removed"
  else
    fail "oldest image a is removed"
  fi
  if ! printf '%s' "$log" | grep -q 'docker rmi learnloop-agent-tools:d'; then
    pass "newest image d is kept"
  else
    fail "newest image d is kept"
  fi
  teardown_stub_env
}

# Regression tests for post-preflight discovery failures. Each must exit
# non-zero, perform zero Docker/Git mutations, and print no misleading
# empty-resource or completion message. These would pass on the new
# two-phase implementation and fail on the PR #495 process-substitution code.

test_cleanup_volume_ls_failure_aborts() {
  printf '%s\n' "test_cleanup_volume_ls_failure_aborts"
  setup_stub_env
  STUB_VOLUME_LS_FAIL=1
  STUB_VOLUMES="learnloop-agent-abc"
  local out rc
  out="$(run_cleanup_stubbed 2>&1)" && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "volume ls failure exits non-zero"; else fail "volume ls failure exits non-zero (rc=$rc)"; fi
  if [ "$(mutations_count)" -ne 0 ]; then
    fail "volume ls failure must perform zero mutations (log: $(mutations_log))"
  else
    pass "volume ls failure performs zero mutations"
  fi
  if printf '%s' "$out" | grep -q 'No unused agent volumes found'; then
    fail "volume ls failure must not print misleading empty-resource success"
  else
    pass "volume ls failure prints no misleading empty-resource success"
  fi
  if printf '%s' "$out" | grep -q '=== Done ==='; then
    fail "volume ls failure must not print completion marker"
  else
    pass "volume ls failure prints no completion marker"
  fi
  teardown_stub_env
}

test_cleanup_images_failure_aborts_after_volume_discovery() {
  printf '%s\n' "test_cleanup_images_failure_aborts_after_volume_discovery"
  setup_stub_env
  # Removable volume candidates are present and discoverable, but image
  # discovery fails. No volumes may be removed and no later cleanup may run.
  STUB_VOLUMES="learnloop-agent-abc"
  STUB_IMAGES_FAIL=1
  local out rc
  out="$(run_cleanup_stubbed 2>&1)" && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "images failure exits non-zero"; else fail "images failure exits non-zero (rc=$rc)"; fi
  if [ "$(mutations_count)" -ne 0 ]; then
    fail "images failure must perform zero mutations (log: $(mutations_log))"
  else
    pass "images failure performs zero mutations"
  fi
  if printf '%s' "$out" | grep -q 'docker volume rm learnloop-agent-abc'; then
    fail "images failure must not remove discovered volumes"
  else
    pass "images failure does not remove discovered volumes"
  fi
  if printf '%s' "$out" | grep -q '=== Done ==='; then
    fail "images failure must not print completion marker"
  else
    pass "images failure prints no completion marker"
  fi
  teardown_stub_env
}

test_cleanup_ps_failure_aborts() {
  printf '%s\n' "test_cleanup_ps_failure_aborts"
  setup_stub_env
  # volume_in_use calls docker ps -a; a failure there must abort planning
  # rather than being treated as "in use" and continuing.
  STUB_VOLUMES="learnloop-agent-abc"
  STUB_PS_FAIL=1
  local out rc
  out="$(run_cleanup_stubbed 2>&1)" && rc=$? || rc=$?
  if [ "$rc" -ne 0 ]; then pass "ps failure exits non-zero"; else fail "ps failure exits non-zero (rc=$rc)"; fi
  if [ "$(mutations_count)" -ne 0 ]; then
    fail "ps failure must perform zero mutations (log: $(mutations_log))"
  else
    pass "ps failure performs zero mutations"
  fi
  if printf '%s' "$out" | grep -q 'docker volume rm learnloop-agent-abc'; then
    fail "ps failure must not remove volumes"
  else
    pass "ps failure does not remove volumes"
  fi
  if printf '%s' "$out" | grep -q '=== Done ==='; then
    fail "ps failure must not print completion marker"
  else
    pass "ps failure prints no completion marker"
  fi
  teardown_stub_env
}

test_cleanup_empty_discovery_success() {
  printf '%s\n' "test_cleanup_empty_discovery_success"
  setup_stub_env
  # Successful empty discovery still prints the correct empty-resource message.
  STUB_VOLUMES="" STUB_IMAGES=""
  local out rc
  out="$(run_cleanup_stubbed 2>&1)" && rc=$? || rc=$?
  if [ "$rc" -eq 0 ]; then pass "empty discovery exits zero"; else fail "empty discovery exits zero (rc=$rc)"; fi
  if printf '%s' "$out" | grep -q 'No unused agent volumes found'; then
    pass "empty discovery prints empty-resource message"
  else
    fail "empty discovery prints empty-resource message"
  fi
  if printf '%s' "$out" | grep -q '=== Done ==='; then
    pass "empty discovery prints completion marker"
  else
    fail "empty discovery prints completion marker"
  fi
  # Empty discovery removes no Docker resources; git worktree prune is the
  # only expected mutation in normal mode.
  local log
  log="$(mutations_log)"
  if printf '%s' "$log" | grep -q 'docker volume rm\|docker rmi\|image prune\|buildx prune\|builder prune'; then
    fail "empty discovery must not remove any Docker resources (log: $log)"
  else
    pass "empty discovery removes no Docker resources"
  fi
  teardown_stub_env
}

test_cleanup_dry_run_empty_discovery_success() {
  printf '%s\n' "test_cleanup_dry_run_empty_discovery_success"
  setup_stub_env
  STUB_VOLUMES="" STUB_IMAGES=""
  local out rc
  out="$(run_cleanup_stubbed --dry-run 2>&1)" && rc=$? || rc=$?
  if [ "$rc" -eq 0 ]; then pass "dry-run empty discovery exits zero"; else fail "dry-run empty discovery exits zero (rc=$rc)"; fi
  if printf '%s' "$out" | grep -q 'No unused agent volumes found'; then
    pass "dry-run empty discovery prints empty-resource message"
  else
    fail "dry-run empty discovery prints empty-resource message"
  fi
  if [ "$(mutations_count)" -ne 0 ]; then
    fail "dry-run empty discovery must not mutate (log: $(mutations_log))"
  else
    pass "dry-run empty discovery makes no mutations"
  fi
  teardown_stub_env
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
  test_cleanup_help_exits_zero
  test_cleanup_invalid_args_nonzero
  test_cleanup_dry_run_no_changes
  test_cleanup_dry_run_help_text
  test_cleanup_keep_images_logic
  test_cleanup_default_never_prunes
  test_cleanup_global_prune_invokes_commands
  test_cleanup_dry_run_global_no_mutations
  test_cleanup_buildx_capability_selection
  test_cleanup_default_dry_run_scoped_nonmutating
  test_cleanup_help_without_docker
  test_cleanup_docker_unavailable_nonzero
  test_cleanup_volume_prefix_strict
  test_cleanup_volume_inuse_protected
  test_cleanup_dry_run_worktree_preview
  test_cleanup_normal_mode_worktree_prune
  test_cleanup_help_global_prune_documented
  test_cleanup_keep_images_stubbed
  test_cleanup_volume_ls_failure_aborts
  test_cleanup_images_failure_aborts_after_volume_discovery
  test_cleanup_ps_failure_aborts
  test_cleanup_empty_discovery_success
  test_cleanup_dry_run_empty_discovery_success

  printf '\nResults: %d passed, %d failed\n' "$passed" "$failed"
  if [ "$failed" -gt 0 ]; then
    return 1
  fi
}

main "$@"
