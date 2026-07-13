#!/usr/bin/env bash
#
# Disk cleanup for agent environment artifacts left by scripts/agent-env.sh.
#
# Usage: scripts/agent-env-cleanup.sh [options]
#
# Options:
#   --dry-run          Preview actions without removing anything.
#   --keep-images N    Keep the N most recent learnloop-agent-tools images (default: 2).
#   --help, -h         Show usage.
#
# See DEVELOPMENT.md for full documentation.

set -euo pipefail

DRY_RUN=false
KEEP_IMAGES=2
HELP_REQUESTED=false

usage() {
  cat <<'EOF'
Usage: scripts/agent-env-cleanup.sh [options]

Options:
  --dry-run          Preview actions without removing anything.
  --keep-images N    Keep the N most recent learnloop-agent-tools images (default: 2).
  --help, -h         Show this message.
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

# Check if a Docker volume is in use by any container (running or stopped).
# Returns 0 (true) if in use, 1 (false) if not. Safe under set -e.
volume_in_use() {
  local names
  names="$(docker ps -a --filter "volume=$1" --format '{{.Names}}')" || return 0
  [[ -n "$names" ]]
}

# List agent volumes (matching learnloop-agent-) not in use by any container.
list_unused_agent_volumes() {
  local vol in_use
  while IFS= read -r vol; do
    [[ -z "$vol" ]] && continue
    if volume_in_use "$vol"; then
      continue
    fi
    printf '%s\n' "$vol"
  done < <(docker volume ls --filter "name=learnloop-agent-" --format '{{.Name}}')
}

# List learnloop-agent-tools images, newest first by creation time.
list_agent_tools_images() {
  docker images --filter "reference=learnloop-agent-tools:*" \
    --format '{{.CreatedAt}}\t{{.Repository}}:{{.Tag}}' \
    | sort -t$'\t' -k1,1 -r \
    | cut -f2
}

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
  done < <(list_unused_agent_volumes)
  if [[ $count -eq 0 ]]; then
    printf '  No unused agent volumes found.\n'
  fi
}

prune_old_images() {
  local images=() i
  while IFS= read -r line; do
    [[ -n "$line" ]] && images+=("$line")
  done < <(list_agent_tools_images)

  local total=${#images[@]}
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

prune_dangling_and_cache() {
  if [[ "$DRY_RUN" == true ]]; then
    printf '  [dry-run] would run: docker image prune -f\n'
    printf '  [dry-run] would run: docker builder prune -f\n'
  else
    docker image prune -f
    docker builder prune -f
  fi
}

prune_worktrees() {
  if [[ "$DRY_RUN" == true ]]; then
    printf '  [dry-run] would run: git worktree prune\n'
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

  printf '=== Agent environment disk cleanup ===\n'
  [[ "$DRY_RUN" == true ]] && printf '(dry-run mode — no changes will be made)\n'

  printf '\n--- Unused agent volumes ---\n'
  prune_agent_volumes

  printf '\n--- Old agent tools images (keeping %s most recent) ---\n' "$KEEP_IMAGES"
  prune_old_images

  printf '\n--- Dangling images and build cache ---\n'
  prune_dangling_and_cache

  printf '\n--- Stale git worktrees ---\n'
  prune_worktrees

  printf '\n=== Done ===\n'
}

# Only execute main when run directly, not when sourced for tests.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi