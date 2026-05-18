#!/usr/bin/env bash

set -euo pipefail

required_archives=(
  "mongodb_data.tar.gz"
  "rustfs_data_0.tar.gz"
  "rustfs_data_1.tar.gz"
  "rustfs_data_2.tar.gz"
  "rustfs_data_3.tar.gz"
)

usage() {
  cat <<'EOF'
Usage: scripts/restore-volumes.sh [--wipe] <backup-dir>

Restore LearnLoop Docker named volumes from gzip-compressed tar backups.
The Docker containers using these volumes must be stopped before restore.

Options:
  --wipe   Remove existing files from each target volume before restore.

Arguments:
  backup-dir  Directory containing the volume backup tar.gz files.

Examples:
  scripts/restore-volumes.sh ./volume-backups/machine-a
  scripts/restore-volumes.sh --wipe ./volume-backups/machine-a
EOF
}

wipe=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wipe)
      wipe=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -ne 1 ]]; then
  usage >&2
  exit 1
fi

backup_dir="$1"

if [[ ! -d "$backup_dir" ]]; then
  echo "Backup directory not found: $backup_dir" >&2
  exit 1
fi

ensure_archive_present() {
  local archive_name="$1"
  if [[ ! -f "$backup_dir/$archive_name" ]]; then
    echo "Missing backup archive: $backup_dir/$archive_name" >&2
    exit 1
  fi
}

ensure_volume_idle() {
  local volume="$1"
  if docker ps -q --filter "volume=$volume" | grep -q .; then
    echo "Volume $volume is currently attached to a running container. Stop the stack first." >&2
    exit 1
  fi
}

restore_volume() {
  local volume="$1"
  local archive_name="$2"

  docker volume create "$volume" >/dev/null
  ensure_volume_idle "$volume"

  docker run --rm \
    -v "$volume:/target" \
    -v "$backup_dir:/backup:ro" \
    alpine:3.20 \
    sh -ec '
      if [ "$1" = "true" ]; then
        find /target -mindepth 1 -maxdepth 1 -exec rm -rf {} +
      fi
      tar xzf "/backup/$2" -C /target
    ' -- "$wipe" "$archive_name"
}

for archive_name in "${required_archives[@]}"; do
  ensure_archive_present "$archive_name"
done

restore_volume "learnloop_mongodb_data" "mongodb_data.tar.gz"
restore_volume "learnloop_rustfs_data_0" "rustfs_data_0.tar.gz"
restore_volume "learnloop_rustfs_data_1" "rustfs_data_1.tar.gz"
restore_volume "learnloop_rustfs_data_2" "rustfs_data_2.tar.gz"
restore_volume "learnloop_rustfs_data_3" "rustfs_data_3.tar.gz"

cat <<EOF
Volumes restored from:
  $backup_dir
EOF
