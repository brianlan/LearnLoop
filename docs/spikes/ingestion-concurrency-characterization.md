# Ingestion repository concurrency characterization

## Summary

This artifact records a real-Mongo concurrency harness and the observed behavior
of six bulk-ingestion repository mutation functions under the current
read-modify-write pattern. No production code was changed.

## Scope

- `backend/tests/integration/test_ingestion_atomicity.py`
- This document

## Out of scope

- Editing `backend/app/infrastructure/ingestion/repository.py` or any other
  production module.
- Implementing positional updates, compare-and-swap, transactions, versioning,
  or any other atomic strategy.
- Changing schema, indexes, API behavior, return values, errors, status
  eligibility, timestamps, or idempotency.
- `claim_item` (already atomic via `find_one_and_update`).
- Preview durability, worker redesign, or single-image preview work.

## Current ownership

The repository functions under test live in
`backend/app/infrastructure/ingestion/repository.py`:

- `save_item_extraction_success`
- `save_item_extraction_failure`
- `reset_item_for_retry`
- `update_item_draft`
- `mark_item_deleted`
- `undo_item_deletion`

All six follow the same read-modify-write shape:

1. `_load_batch_for_update` calls `find_one` for the whole batch document.
2. The function mutates an in-memory copy of the embedded `items` array.
3. `_persist_batch` calls `update_one` with `$set: {items: [...], updatedAt: ...}`.

This shape is owned by the infrastructure/persistence layer. Any move toward an
atomic or domain-layer strategy requires a new human/council decision and a
separate issue with a rollback plan.

## Harness design

`backend/tests/integration/test_ingestion_atomicity.py` connects to the
isolated agent-environment MongoDB (no host port collisions, one replica set per
worktree).

Key components:

- `_SynchronizedCollection`: a delegating proxy that forwards every call to the
  real `ingestion_batches` collection but can insert `asyncio.Event` barriers
  around `find_one` and `update_one`.
- `_SynchronizedDatabase`: a delegating database proxy that returns the
  synchronized collection for `ingestion_batches` and delegates all other
  collections directly.
- Per-test fixture `real_database`: requires `MONGODB_URI`, creates a fresh
  `AsyncMongoClient` per test, ensures the collection/indexes exist, yields the
  real database, and cleans the `ingestion_batches` collection after the test.
  Test runs without `MONGODB_URI` skip this real-service module instead of
  waiting for an unavailable default host; an explicitly configured but
  unavailable Mongo server still fails the run.

The harness fails loudly if it accidentally receives a `FakeDatabase` because
`AsyncMongoClient` and real BSON round-tripping are required.

The recorded run used MongoDB 4.4.30 in the worktree-isolated replica set and
PyMongo 4.17.0's asynchronous client.

## Race reproduction

`test_concurrent_distinct_item_updates_lose_one_change` reproduces a
whole-array lost update deterministically:

1. Seed one batch with two queued items (`item-a`, `item-b`).
2. Route operations A and B through separate `_SynchronizedDatabase` proxies.
3. Release both real `find_one` calls, wait for both reads to complete, and
   assert that neither real `update_one` has completed.
4. Release B's real `update_one` and wait for it to complete while A remains
   held before persistence.
5. Release A's real `update_one`. A writes its original snapshot with only
   `item-a` updated, overwriting B's change to `item-b`.

Final observed document:

- `item-a.draft.text` == `"updated by A"` (A's write is last).
- `item-b.draft.text` is `None` (B's change is lost).

This is a deterministic event-driven interleaving, not scheduler luck or
arbitrary sleep.

## Sequential contracts observed

All sequential tests run against the real Mongo server and confirm the same
contracts as the existing fake-based `test_ingestion_persistence.py` tests,
with one round-trip note: Mongo returns stored datetimes as naive UTC
`datetime` objects; the tests compare them with `.replace(tzinfo=UTC)`.

| Function | Return | Eligibility / idempotency | Lease | Timestamp/shape |
|---|---|---|---|---|
| `save_item_extraction_success` | `None` | missing item also returns `None` and rewrites the unchanged items array; missing batch raises `ValueError` | clears to `None` for a matching item | status `ready`, stores crop/draft/extraction, sets item and batch `updatedAt` |
| `save_item_extraction_failure` | `None` | missing item also returns `None` and rewrites the unchanged items array; missing batch raises `ValueError` | clears to `None` for a matching item | status `failed`, stores extraction, sets item and batch `updatedAt` |
| `reset_item_for_retry` | `True` if changed | `failed` or `submit-failed` | cleared to `None` | status `queued`, `updatedAt` set |
| `reset_item_for_retry` | `TypeError` with aware `now` | `extracting` with an expired lease round-tripped from Mongo | unchanged | Mongo returns a naive UTC lease, which cannot be compared with the aware UTC `now`; no write occurs |
| `reset_item_for_retry` | `False` | ineligible statuses, missing item | n/a | no write |
| `reset_item_for_retry` | `ValueError` | missing batch | n/a | raises "Batch not found" |
| `update_item_draft` | updated item | item must exist | n/a | merges allowed keys (`text`, `problemType`, `graphDsl`, `correctAnswer`, `tags`, `subject`), ignores others, sets `updatedAt` |
| `update_item_draft` | `None` | missing item | n/a | no write; missing batch raises `ValueError` |
| `mark_item_deleted` | `True` | item exists and not already deleted/submitted | n/a | sets `previousStatus`, `deletedAt`, `updatedAt` |
| `mark_item_deleted` | `True` | already deleted/submitted | n/a | idempotent, no write |
| `mark_item_deleted` | `False` | missing item | n/a | no write; missing batch raises `ValueError` |
| `undo_item_deletion` | `True` | item is deleted and has `previousStatus` | n/a | restores `previousStatus`, removes `deletedAt` and `previousStatus`, sets `updatedAt` |
| `undo_item_deletion` | `False` | not deleted, missing `previousStatus`, or missing item | n/a | no write; missing batch raises `ValueError` |

## Shared race exposure

Each function reads the full batch before conditionally changing one item and
then replaces the full `items` array. Distinct-item operations are therefore
compatible race pairings whenever their sequential eligibility is satisfied:

| Function | Concurrent exposure |
|---|---|
| `save_item_extraction_success` | A later stale success write can erase another item's draft, delete, retry, or extraction result. |
| `save_item_extraction_failure` | A later stale failure write can erase the same distinct-item changes. |
| `reset_item_for_retry` | A successful retry writes the full array and can erase another item mutation; ineligible retries and the observed expired-lease `TypeError` return before writing. |
| `update_item_draft` | A matching-item draft update writes the full array; this is the deterministic pairing used by the harness. |
| `mark_item_deleted` | A new deletion writes the full array and is exposed; already deleted/submitted and missing-item paths return before writing. |
| `undo_item_deletion` | An eligible undo writes the full array and is exposed; ineligible and missing-item paths return before writing. |

The harness uses two draft updates because both are easy to make eligible on
one seeded batch without introducing lease or status setup into the operation
trace. It demonstrates the shared persistence mechanism, not every possible
pairwise business-state combination.

## Commands

Build the agent environment once per fingerprint:

```bash
./scripts/agent-env.sh build
```

Run the focused real-Mongo harness:

```bash
./scripts/agent-env.sh shell
# Inside the agent shell:
cd backend
uv run --frozen --active pytest tests/integration/test_ingestion_atomicity.py -q
```

Run the full backend suite:

```bash
./scripts/agent-env.sh test backend
```

The ordinary backend CI job does not start MongoDB or set `MONGODB_URI`, so it
collects and skips this real-service module. The isolated agent-environment
commands above set `MONGODB_URI` and execute the harness against MongoDB.

Recorded results:

- Focused real-Mongo harness: 14 passed.
- Full agent-environment backend suite: 980 passed, 1 skipped.
- Focused tools-image run with `MONGODB_URI` removed: 14 skipped in 0.01s.
- `git diff --check`: passed.
- Changed paths: this artifact and
  `backend/tests/integration/test_ingestion_atomicity.py`; no production file.

## Limitations

- The harness does not attempt to start transactions or compare-and-swap; those
  are production behavior changes and are out of scope.
- The lost-update reproduction uses `update_item_draft` because it is the only
  one of the six functions whose interleaving is easy to drive from a single
  user/batch without changing leases or statuses. The same read-modify-write
  risk applies to all six functions.
- Mongo returns stored datetimes as naive UTC; the tests handle this explicitly
  rather than changing the repository to normalize. For expired extracting
  leases, that round trip exposes a current `TypeError` when the repository
  compares the naive lease to an aware UTC `now`; the harness records the error
  and verifies that no write occurs.

## Future gate

Any atomic-mutation implementation (positional updates, transactions,
versioning, compare-and-swap, or schema change) requires:

1. A new human and Safety review.
2. A separate implementation issue.
3. A rollback plan.
4. Passing both this characterization harness and the existing fake-based
   sequential tests.
