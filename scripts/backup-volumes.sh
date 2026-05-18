#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

volumes=(
  "learnloop_mongodb_data"
  "learnloop_rustfs_data_0"
  "learnloop_rustfs_data_1"
  "learnloop_rustfs_data_2"
  "learnloop_rustfs_data_3"
)

usage() {
  cat <<'EOF'
Usage: scripts/backup-volumes.sh [backup-dir]

Create gzip-compressed tar backups for the LearnLoop Docker named volumes.
The Docker containers using these volumes must be stopped before backup.

Arguments:
  backup-dir  Optional output directory. Defaults to ./volume-backups/<timestamp>

Examples:
  scripts/backup-volumes.sh
  scripts/backup-volumes.sh ./volume-backups/machine-a
EOF
}

backup_dir="${1:-$repo_root/volume-backups/$(date +%Y%m%d-%H%M%S)}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 1
fi

ensure_volume_idle() {
  local volume="$1"
  if docker ps -q --filter "volume=$volume" | grep -q .; then
    echo "Volume $volume is currently attached to a running container. Stop the stack first." >&2
    exit 1
  fi
}

backup_volume() {
  local volume="$1"
  local archive_name="$2"

  if ! docker volume inspect "$volume" >/dev/null 2>&1; then
    echo "Missing Docker volume: $volume" >&2
    exit 1
  fi

  ensure_volume_idle "$volume"

  docker run --rm \
    -v "$volume:/source:ro" \
    -v "$backup_dir:/backup" \
    alpine:3.20 \
    sh -ec "cd /source && tar czf /backup/$archive_name ."
}

mkdir -p "$backup_dir"

backup_volume "learnloop_mongodb_data" "mongodb_data.tar.gz"
backup_volume "learnloop_rustfs_data_0" "rustfs_data_0.tar.gz"
backup_volume "learnloop_rustfs_data_1" "rustfs_data_1.tar.gz"
backup_volume "learnloop_rustfs_data_2" "rustfs_data_2.tar.gz"
backup_volume "learnloop_rustfs_data_3" "rustfs_data_3.tar.gz"

cat <<EOF
Backups created in:
  $backup_dir
EOF
