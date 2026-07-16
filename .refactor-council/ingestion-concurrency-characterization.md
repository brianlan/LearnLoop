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
- Per-test fixture `real_database`: creates a fresh `AsyncMongoClient` per test,
  ensures the collection/indexes exist, yields the real database, and cleans the
  `ingestion_batches` collection after the test.

The harness fails loudly if it accidentally receives a `FakeDatabase` because
`AsyncMongoClient` and real BSON round-tripping are required.

## Race reproduction

`test_concurrent_distinct_item_updates_lose_one_change` reproduces a
whole-array lost update deterministically:

1. Seed one batch with two queued items (`item-a`, `item-b`).
2. Route operation A through `_SynchronizedDatabase`. Enable sync events so
   the test observes when A finishes its `find_one` and controls when A's
   `update_one` runs.
3. Wait until A has finished `find_one`; at this point A holds the initial
   document in memory and is paused before `update_one`.
4. Run operation B directly against the real database. B calls `find_one`,
   reads the same initial document, mutates `item-b`, and writes.
5. Release A's `update_one`. A writes the original document with only `item-a`
   updated, overwriting B's change to `item-b`.

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
| `save_item_extraction_success` | `None` | item must exist | clears to `None` | status `ready`, stores crop/draft/extraction, sets `updatedAt` |
| `save_item_extraction_failure` | `None` | item must exist | clears to `None` | status `failed`, stores extraction, sets `updatedAt` |
| `reset_item_for_retry` | `True` if changed | `failed`, `submit-failed`, or `extracting` with expired lease | cleared to `None` | status `queued`, `updatedAt` set |
| `reset_item_for_retry` | `False` | ineligible statuses, missing item | n/a | no change |
| `reset_item_for_retry` | `ValueError` | missing batch | n/a | raises "Batch not found" |
| `update_item_draft` | updated item | item must exist | n/a | merges allowed keys (`text`, `problemType`, `graphDsl`, `correctAnswer`, `tags`, `subject`), ignores others, sets `updatedAt` |
| `update_item_draft` | `None` | missing item | n/a | no change |
| `mark_item_deleted` | `True` | item exists and not already deleted/submitted | n/a | sets `previousStatus`, `deletedAt`, `updatedAt` |
| `mark_item_deleted` | `True` | already deleted/submitted | n/a | idempotent, no state change |
| `undo_item_deletion` | `True` | item is deleted and has `previousStatus` | n/a | restores `previousStatus`, removes `deletedAt` and `previousStatus`, sets `updatedAt` |
| `undo_item_deletion` | `False` | not deleted, or missing `previousStatus` | n/a | no change |

## Commands

Build the agent environment once per fingerprint:

```bash
./scripts/agent-env.sh build
```

Run the focused real-Mongo harness:

```bash
./scripts/agent-env.sh test backend tests/integration/test_ingestion_atomicity.py
```

Run the full backend suite:

```bash
./scripts/agent-env.sh test backend
```

## Limitations

- The harness does not attempt to start transactions or compare-and-swap; those
  are production behavior changes and are out of scope.
- The lost-update reproduction uses `update_item_draft` because it is the only
  one of the six functions whose interleaving is easy to drive from a single
  user/batch without changing leases or statuses. The same read-modify-write
  risk applies to all six functions.
- Mongo returns stored datetimes as naive UTC; the tests handle this explicitly
  rather than changing the repository to normalize.

## Future gate

Any atomic-mutation implementation (positional updates, transactions,
versioning, compare-and-swap, or schema change) requires:

1. A new human and Safety review.
2. A separate implementation issue.
3. A rollback plan.
4. Passing both this characterization harness and the existing fake-based
   sequential tests.
