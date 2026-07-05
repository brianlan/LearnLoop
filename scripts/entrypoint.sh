#!/bin/bash
set -e

echo "=== Starting MongoDB replica set rs0 ==="
pkill -9 mongod || true
rm -f /tmp/mongodb-27017.sock || true
mkdir -p /data/db
rm -f /data/db/mongod.lock || true
mongod --fork --logpath /var/log/mongodb.log --bind_ip_all --replSet rs0 --port 27017

# Wait for MongoDB to become ready
echo "Waiting for MongoDB..."
until mongo --port 27017 --eval "db.adminCommand({ping:1})" >/dev/null 2>&1; do
    sleep 1
done

# Initialize the replica set using legacy mongo shell
echo "Initializing replica set rs0..."
mongo --port 27017 --eval '
var status = rs.status();
if (status.ok === 1) {
    print("Replica set already initialized.");
} else {
    var res = rs.initiate({
        _id: "rs0",
        members: [{ _id: 0, host: "127.0.0.1:27017" }]
    });
    print("rs.initiate status: " + JSON.stringify(res));
}
'

# Wait for replica set primary selection
echo "Waiting for replica set to elect primary..."
until mongo --port 27017 --quiet --eval '
var status = rs.status();
if (status.ok === 1 && status.members && status.members[0] && status.members[0].state === 1) {
    quit(0);
} else {
    quit(1);
}
' >/dev/null 2>&1; do
    sleep 1
done

echo "=== Starting RustFS ==="
export RUSTFS_VOLUMES="/data/rustfs{0...3}"
mkdir -p /data/rustfs{0..3}

export RUSTFS_ADDRESS=127.0.0.1:9000
export RUSTFS_CONSOLE_ADDRESS=127.0.0.1:9001
export RUSTFS_CONSOLE_ENABLE=true
export RUSTFS_ACCESS_KEY=${S3_ACCESS_KEY:-learnloop-local}
export RUSTFS_SECRET_KEY=${S3_SECRET_KEY:-learnloop-secret}
export RUSTFS_UNSAFE_BYPASS_DISK_CHECK=true

rustfs > /var/log/rustfs.log 2>&1 &

# Wait for RustFS API and Console healthchecks
echo "Waiting for RustFS..."
until curl -fsS http://127.0.0.1:9000/health >/dev/null 2>&1; do
    sleep 1
done

# Bootstrap the RustFS bucket
echo "Bootstrapping RustFS S3 bucket..."
mc alias set learnloop http://127.0.0.1:9000 "$RUSTFS_ACCESS_KEY" "$RUSTFS_SECRET_KEY" >/dev/null
mc mb --ignore-existing learnloop/${S3_BUCKET:-learnloop-media}

echo "=== Services successfully started & configured! ==="

# Execute the agent command (e.g., shell, test runner, git agent execution)
exec "$@"
