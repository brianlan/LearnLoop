#!/usr/bin/env bash
#
# Reclaim disk space left behind by agent-env.sh sessions.
#
# Removes:
#   1. Volumes from agent Compose projects that are no longer in use.
#   2. Dangling Docker images (untagged layers from rebuilds).
#   3. Old learnloop-agent-tools images, keeping only the N most recent.
#   4. Docker build cache.
#   5. Stale Git worktrees whose directories no longer exist.
#
# Usage: scripts/agent-env-cleanup.sh [--dry-run] [--keep-images N]
#
#   --dry-run        Print what would be removed without removing anything.
#   --keep-images N  Number of learnloop-agent-tools images to keep (default 2).
#
# See DEVELOPMENT.md for full documentation.

set -euo pipefail

dry_run=false
keep_images=2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      dry_run=true
      ;;
    --keep-images)
      shift
      keep_images="${1:?--keep-images requires a value}"
      ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/agent-env-cleanup.sh [--dry-run] [--keep-images N]

  --dry-run        Print what would be removed without removing anything.
  --keep-images N  Number of learnloop-agent-tools images to keep (default 2).
EOF
      exit 0
      ;;
    *)
      printf 'Error: unknown option "%s"\n' "$1" >&2
      exit 1
      ;;
  esac
  shift
done

run() {
  if $dry_run; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

section() {
  printf '\n== %s ==\n' "$1"
}

# ---------------------------------------------------------------------------
# 1. Agent volumes not used by any running container
# ---------------------------------------------------------------------------
section "Agent volumes (not in use by any container)"

agent_volumes=()
while IFS= read -r vol; do
  [[ -n "$vol" ]] && agent_volumes+=("$vol")
done < <(docker volume ls --filter "name=learnloop-agent-" --format "{{.Name}}")

if [[ ${#agent_volumes[@]} -eq 0 ]]; then
  printf 'No agent volumes found.\n'
else
  for vol in "${agent_volumes[@]}"; do
    # Check if any container is using this volume
    if docker ps -a --filter "volume=$vol" --format "{{.ID}}" | grep -q .; then
      printf '  [skip] %s (in use by a container)\n' "$vol"
    else
      printf '  [remove] %s\n' "$vol"
      run docker volume rm "$vol"
    fi
  done
fi

# ---------------------------------------------------------------------------
# 2. Dangling images
# ---------------------------------------------------------------------------
section "Dangling images"

dangling=()
while IFS= read -r img; do
  [[ -n "$img" ]] && dangling+=("$img")
done < <(docker images --filter "dangling=true" --format "{{.ID}}")

if [[ ${#dangling[@]} -eq 0 ]]; then
  printf 'No dangling images found.\n'
else
  printf '  %d dangling image(s) found.\n' "${#dangling[@]}"
  run docker image prune -f
fi

# ---------------------------------------------------------------------------
# 3. Old learnloop-agent-tools images (keep N most recent)
# ---------------------------------------------------------------------------
section "Old learnloop-agent-tools images (keeping ${keep_images} most recent)"

tools_images=()
while IFS= read -r img; do
  [[ -n "$img" ]] && tools_images+=("$img")
done < <(docker images --filter "reference=learnloop-agent-tools:*" --format "{{.Repository}}:{{.Tag}}")

if [[ ${#tools_images[@]} -le $keep_images ]]; then
  printf 'Only %d image(s) found, keeping all.\n' "${#tools_images[@]}"
else
  remove_count=$(( ${#tools_images[@]} - keep_images ))
  printf '  %d image(s) total, removing %d oldest.\n' "${#tools_images[@]}" "$remove_count"

  kept=0
  while IFS=$'\t' read -r tag created; do
    if [[ $kept -ge $keep_images ]]; then
      printf '  [remove] %s (created %s)\n' "$tag" "$created"
      run docker rmi "$tag" 2>/dev/null || true
    else
      printf '  [keep]   %s (created %s)\n' "$tag" "$created"
    fi
    kept=$((kept + 1))
  done < <(docker images --filter "reference=learnloop-agent-tools:*" --format "{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}" | sort -t$'\t' -k2 -r)
fi

# ---------------------------------------------------------------------------
# 4. Docker build cache
# ---------------------------------------------------------------------------
section "Docker build cache"

run docker builder prune -f

# ---------------------------------------------------------------------------
# 5. Stale Git worktrees
# ---------------------------------------------------------------------------
section "Stale Git worktrees"

# git worktree prune removes worktree metadata for directories that no longer exist
run git worktree prune

# List remaining worktrees for visibility
if git worktree list --porcelain 2>/dev/null | grep -q "^worktree "; then
  printf '  Remaining worktrees:\n'
  git worktree list --porcelain 2>/dev/null | grep "^worktree " | while IFS= read -r line; do
    printf '    %s\n' "${line#worktree }"
  done
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
section "Summary"
if $dry_run; then
  printf 'Dry run complete. No changes were made. Re-run without --dry-run to apply.\n'
else
  printf 'Cleanup complete.\n'
  docker system df
fi