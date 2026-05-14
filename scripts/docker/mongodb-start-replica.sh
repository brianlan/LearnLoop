#!/usr/bin/env bash

set -euo pipefail

mongod --bind_ip_all --replSet rs0 --port 27017 --dbpath /data/db &
mongo_pid=$!

shutdown() {
  if kill -0 "$mongo_pid" >/dev/null 2>&1; then
    kill "$mongo_pid"
    wait "$mongo_pid"
  fi
}

trap shutdown INT TERM

until mongo --quiet --host 127.0.0.1 --port 27017 --eval 'db.adminCommand({ ping: 1 }).ok' >/dev/null 2>&1; do
  sleep 1
done

mongo --quiet --host 127.0.0.1 --port 27017 /scripts/mongodb-init-replica.js

wait "$mongo_pid"
