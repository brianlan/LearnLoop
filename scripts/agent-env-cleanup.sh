#!/usr/bin/env bash
#
# Disk cleanup for agent environment artifacts left by scripts/agent-env.sh.
#
# By default this script is SCOPED to LearnLoop agent resources only:
# unused volumes whose names start with "learnloop-agent-", old
# learnloop-agent-tools images, and stale git worktree metadata in this repo.
#
# Daemon-wide Docker cleanup (dangling images and shared build-cache pruning)
# is opt-in via --global-prune and may affect OTHER projects on this machine.
#
# Usage: scripts/agent-env-cleanup.sh [options]
#
# Options:
#   --dry-run            Preview actions without removing anything.
#   --keep-images N      Keep the N most recent learnloop-agent-tools images (default: 2).
#   --global-prune       Also run daemon-wide docker image prune and build-cache
#                        pruning (30-day age, 10 GB reservation). Affects other
#                        projects. Off by default.
#   --help, -h           Show usage. Works without a Docker daemon.
#
# See DEVELOPMENT.md for full documentation.

set -euo pipefail

DRY_RUN=false
KEEP_IMAGES=2
HELP_REQUESTED=false
GLOBAL_PRUNE=false

VOLUME_PREFIX="learnloop-agent-"
IMAGE_REF="learnloop-agent-tools"
# Fixed policy for opt-in build-cache pruning: 30 days, 10 GB reserved.
CACHE_UNTIL="720h"
CACHE_RESERVE="10GB"

usage() {
  cat <<'EOF'
Usage: scripts/agent-env-cleanup.sh [options]

By default only LearnLoop agent resources are cleaned up:
unused learnloop-agent-* volumes, old learnloop-agent-tools images,
and stale git worktree metadata in this repository.

Options:
  --dry-run            Preview actions without removing anything.
  --keep-images N      Keep the N most recent learnloop-agent-tools images (default: 2).
  --global-prune       ALSO run daemon-wide docker image prune and build-cache
                       pruning. Affects OTHER projects on this Docker daemon.
                       Uses a fixed 30-day age filter and a 10 GB cache reservation.
  --help, -h           Show this message. Does not require a working Docker daemon.

CAUTION: --global-prune removes dangling images and build cache from the whole
Docker daemon, including images/cache belonging to other projects. It is off
by default and must be requested explicitly.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        DRY_RUN=true
        ;;
      --keep-images)
        if [[ $# -lt 2 || ! "$2" =~ ^[0-9]+$ ]]; then
          printf 'Error: --keep-images requires a non-negative integer.\n' >&2
          return 1
        fi
        KEEP_IMAGES="$2"
        shift
        ;;
      --global-prune)
        GLOBAL_PRUNE=true
        ;;
      --help|-h)
        HELP_REQUESTED=true
        ;;
      *)
        printf 'Error: unknown option "%s".\n' "$1" >&2
        usage >&2
        return 1
        ;;
    esac
    shift
  done
}

# Fail fast if the Docker daemon is not reachable. Called after help/arg
# parsing and before any resource discovery or removal.
docker_preflight() {
  if ! docker info >/dev/null 2>&1; then
    printf 'Error: Docker daemon is not available. Aborting before any cleanup.\n' >&2
    return 1
  fi
}

# Check if a Docker volume is in use by any container (running or stopped).
# Returns 0 (in use), 1 (not in use), or 2 (docker ps -a failed). Callers must
# distinguish a genuine "not in use" result (1) from a discovery failure (2)
# so a failed lookup aborts planning instead of being treated as unused and
# silently deleted. Safe under set -e: callers capture status explicitly.
volume_in_use() {
  local names rc
  names="$(docker ps -a --filter "volume=$1" --format '{{.Names}}' 2>&1)" || {
    rc=$?
    printf 'Error: docker ps -a failed for volume %s: %s\n' "$1" "$names" >&2
    return 2
  }
  [[ -n "$names" ]]
}

# Discover unused agent volumes with a strict prefix check. Docker's volume
# name filter is a substring match, so we re-validate each name in shell to
# avoid deleting unrelated volumes whose names merely contain the prefix
# text. Output and status are captured by the caller BEFORE any mutation:
# the result is consumed through `output="$(plan_unused_agent_volumes)" || …`,
# never through process substitution, which would hide the exit status.
plan_unused_agent_volumes() {
  local raw rc vol vrc
  raw="$(docker volume ls --filter "name=${VOLUME_PREFIX}" --format '{{.Name}}' 2>&1)" || {
    rc=$?
    printf 'Error: docker volume ls failed: %s\n' "$raw" >&2
    return "$rc"
  }
  while IFS= read -r vol; do
    [[ -z "$vol" ]] && continue
    # Strict prefix check; Docker's --filter is a substring match.
    case "$vol" in
      "${VOLUME_PREFIX}"*) ;;
      *) continue ;;
    esac
    volume_in_use "$vol" && vrc=0 || vrc=$?
    case $vrc in
      0) continue ;;       # in use
      1) ;;                # not in use, keep as candidate
      *) return "$vrc" ;;  # discovery failure, abort planning
    esac
    printf '%s\n' "$vol"
  done <<< "$raw"
}

# Discover learnloop-agent-tools images, newest first by creation time.
# Output and status are captured by the caller BEFORE any mutation. Never
# consume this through process substitution; the exit status would not
# propagate.
plan_agent_tools_images() {
  local raw rc
  raw="$(docker images --filter "reference=${IMAGE_REF}:*" \
    --format '{{.CreatedAt}}\t{{.Repository}}:{{.Tag}}' 2>&1)" || {
    rc=$?
    printf 'Error: docker images failed: %s\n' "$raw" >&2
    return "$rc"
  }
  printf '%s' "$raw" | sort -t$'\t' -k1,1 -r | cut -f2
}

# Remove planned volumes. Args: <newline-separated volume list>.
# A zero-length list prints the empty-resource success message.
prune_agent_volumes() {
  local vol count=0
  while IFS= read -r vol; do
    [[ -z "$vol" ]] && continue
    if [[ "$DRY_RUN" == true ]]; then
      printf '  [dry-run] would remove volume: %s\n' "$vol"
    else
      docker volume rm "$vol" >/dev/null
      printf '  Removed volume: %s\n' "$vol"
    fi
    count=$((count + 1))
  done <<< "$1"
  if [[ $count -eq 0 ]]; then
    printf '  No unused agent volumes found.\n'
  fi
}

# Remove old planned images. Args: <newline-separated image list>.
prune_old_images() {
  local images=() i line total
  while IFS= read -r line; do
    [[ -n "$line" ]] && images+=("$line")
  done <<< "$1"

  total=${#images[@]}
  if [[ $total -le $KEEP_IMAGES ]]; then
    printf '  Keeping all %d agent tools images (keep-images=%d).\n' "$total" "$KEEP_IMAGES"
    return 0
  fi

  for ((i = KEEP_IMAGES; i < total; i++)); do
    if [[ "$DRY_RUN" == true ]]; then
      printf '  [dry-run] would remove image: %s\n' "${images[$i]}"
    else
      if docker rmi "${images[$i]}" >/dev/null 2>&1; then
        printf '  Removed image: %s\n' "${images[$i]}"
      else
        printf '  Skipped image (in use or error): %s\n' "${images[$i]}"
      fi
    fi
  done
}

# Detect whether a Docker CLI subcommand supports a given flag by scanning its
# --help output for an exact flag token. Args: <flag> <command...>.
# Returns 0 if supported, 1 otherwise.
supports_flag() {
  local flag="$1"; shift
  local help_out
  help_out="$("$@" --help 2>&1)" || return 1
  printf '%s\n' "$help_out" | grep -Eq "(^|[[:space:]])${flag}([[:space:]]|=|\$)"
}

# Choose the guarded build-cache prune command by capability, not by OS.
# Prefer buildx --reserved-space; fall back to builder --keep-storage; fail
# if neither capacity flag is available. Emits the chosen command text.
select_cache_prune_cmd() {
  local cmd
  if docker buildx version >/dev/null 2>&1 \
    && supports_flag --reserved-space docker buildx prune; then
    cmd="docker buildx prune -f --filter 'until=${CACHE_UNTIL}' --reserved-space ${CACHE_RESERVE}"
  elif supports_flag --keep-storage docker builder prune; then
    cmd="docker builder prune -f --filter 'until=${CACHE_UNTIL}' --keep-storage ${CACHE_RESERVE}"
  else
    printf 'Error: --global-prune requires a Docker/buildx CLI that supports either\n' >&2
    printf '       "--reserved-space" (buildx prune) or "--keep-storage" (builder prune)\n' >&2
    printf '       for bounded build-cache pruning. No unbounded fallback is allowed.\n' >&2
    return 1
  fi
  printf '%s\n' "$cmd"
}

# Daemon-wide dangling-image and build-cache pruning. Args: <chosen cache cmd>.
# The cache command was validated during planning; passing it in guarantees a
# discovery-time capability failure aborts before any mutation.
prune_dangling_and_cache() {
  local cache_cmd="$1"

  printf '  WARNING: --global-prune removes dangling images and build cache from the\n'
  printf '           ENTIRE Docker daemon, including resources from OTHER projects.\n'

  if [[ "$DRY_RUN" == true ]]; then
    printf '  [dry-run] would run: docker image prune -f\n'
    # Inventory of dangling images for dry-run visibility.
    local dangling
    if dangling="$(docker images --filter 'dangling=true' \
        --format '{{.ID}}\t{{.Repository}}:{{.Tag}}\t{{.Size}}' 2>/dev/null)"; then
      local dcount=0 line
      while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        if [[ $dcount -eq 0 ]]; then
          printf '  [dry-run] dangling image candidates (may belong to other projects):\n'
        fi
        printf '    %s\n' "$line"
        dcount=$((dcount + 1))
      done <<< "$dangling"
      printf '  [dry-run] dangling image candidate count: %d\n' "$dcount"
    else
      printf '  [dry-run] could not enumerate dangling images (docker images failed).\n'
    fi
    printf '  [dry-run] would run: %s\n' "$cache_cmd"
    printf '  [dry-run] note: Docker has no exact dry-run for builder prune; cache info is an inventory.\n'
    if docker buildx du >/dev/null 2>&1; then
      docker buildx du 2>/dev/null | sed 's/^/    /' || true
    elif docker system df 2>/dev/null | grep -i build >/dev/null; then
      docker system df 2>/dev/null | grep -i build | sed 's/^/    /' || true
    fi
  else
    docker image prune -f
    # shellcheck disable=SC2086
    eval "$cache_cmd"
  fi
}

prune_worktrees() {
  if [[ "$DRY_RUN" == true ]]; then
    printf '  [dry-run] stale git worktree preview:\n'
    git worktree prune --dry-run --verbose 2>/dev/null | sed 's/^/    /' || \
      printf '    (no stale worktree metadata or git unavailable)\n'
  else
    git worktree prune
    printf '  Pruned stale git worktrees.\n'
  fi
}

main() {
  parse_args "$@" || return $?
  if [[ "$HELP_REQUESTED" == true ]]; then
    usage
    return 0
  fi

  docker_preflight || return $?

  # --- Discovery / planning phase -----------------------------------------
  # Gather all scoped candidates and validate optional --global-prune
  # capability BEFORE any Docker or Git mutation. A failure here aborts with
  # zero mutations and no misleading success output. Outputs are captured with
  # `output="$(producer)" || return $?` because Bash does not propagate the
  # exit status of process substitution (`< <(...)`) to the outer loop.
  local planned_volumes planned_images cache_cmd=""
  if ! planned_volumes="$(plan_unused_agent_volumes)"; then
    printf 'Error: volume discovery failed; aborting before any cleanup.\n' >&2
    return 1
  fi
  if ! planned_images="$(plan_agent_tools_images)"; then
    printf 'Error: image discovery failed; aborting before any cleanup.\n' >&2
    return 1
  fi
  if [[ "$GLOBAL_PRUNE" == true ]]; then
    if ! cache_cmd="$(select_cache_prune_cmd)"; then
      return 1
    fi
  fi

  # --- Mutation phase -----------------------------------------------------
  # All discovery/planning succeeded; it is now safe to remove resources.
  printf '=== Agent environment disk cleanup ===\n'
  [[ "$DRY_RUN" == true ]] && printf '(dry-run mode — no changes will be made)\n'
  if [[ "$GLOBAL_PRUNE" == true ]]; then
    printf '(--global-prune enabled — includes daemon-wide Docker cleanup)\n'
  else
    printf '(scoped to LearnLoop agent resources only)\n'
  fi

  printf '\n--- Unused agent volumes ---\n'
  prune_agent_volumes "$planned_volumes"

  printf '\n--- Old agent tools images (keeping %s most recent) ---\n' "$KEEP_IMAGES"
  prune_old_images "$planned_images"

  if [[ "$GLOBAL_PRUNE" == true ]]; then
    printf '\n--- Daemon-wide dangling images and build cache (--global-prune) ---\n'
    prune_dangling_and_cache "$cache_cmd" || return $?
  fi

  printf '\n--- Stale git worktrees ---\n'
  prune_worktrees

  printf '\n=== Done ===\n'
}

# Only execute main when run directly, not when sourced for tests.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi