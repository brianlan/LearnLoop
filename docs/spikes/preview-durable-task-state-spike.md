# Spike: Durable Preview Task State

## Summary

This spike documents the current process-local preview task state,
compares it with a Mongo-backed durable state design, analyzes key
trade-off dimensions, and recommends a future implementation path.

No production code is changed by this spike.

## Current Architecture

Single-image ingestion previews use an **in-process asyncio task** to
run VLM extraction.  The task registry is a module-level dict:

```python
# ingestion_workflow.py
preview_tasks: dict[str, asyncio.Task[None]] = {}
```

### Lifecycle of a preview extraction

1. **`POST /ingestion/previews/{id}/extract`** (or `/retry`) calls
   `start_extraction()`.
2. `start_extraction` writes `status = "extracting"` and an
   `extraction` sub-document (containing `requestStartedAt`,
   `requestModel`, etc.) to the `ingestion_previews` MongoDB
   collection.
3. It then creates an `asyncio.Task` wrapping
   `_run_extraction_task` and registers it in `preview_tasks`.
4. The API endpoint calls `wait_for_preview_result(task, timeout=25s)`.
   - If the task finishes within 25 s, the endpoint refreshes the
     preview from Mongo and returns the result (200).
   - If it does not finish, the endpoint returns the current preview
     with `status = "extracting"` (202).  The client polls
     `GET /ingestion/previews/{id}`.
5. `_run_extraction_task` reads the source image from S3, calls the
   VLM, and writes either `READY` (success) or `VLM_FAILED` (error)
   back to Mongo.
6. On task completion, a done-callback removes the entry from
   `preview_tasks`.

### Stale-preview recovery

When `GET /ingestion/previews/{id}` sees `status = "extracting"`, it
calls `recover_preview_if_stale()`.  This function compares
`extraction.requestStartedAt` against the configured
`preview_extracting_window_seconds` (default 150 s).  If the window
has been exceeded, the preview is lazily transitioned to
`VLM_FAILED` with failure code `vlm-stale-preview-timeout`.

This is the **only** recovery mechanism.  It is triggered by the next
client poll, not proactively.

### Contrast: bulk ingestion extraction worker

The bulk ingestion flow already uses a **durable** pattern:

- `run_extraction_worker()` (started in `lifespan`) polls MongoDB for
  items whose status is claimable.
- `claim_item()` uses an atomic `find_one_and_update` with `$elemMatch`
  and the positional `$` operator, including a lease timeout.
- Each claimed item is processed by `process_item()`, which writes
  the result back to the batch document.
- If the process restarts, the worker resumes by re-polling the
  database.  Leased items whose lease has expired become claimable
  again.

This means bulk ingestion already survives restarts and supports
multiple workers, while single-preview ingestion does not.

## Failure Mode Analysis

### 1. Process restart / crash

**Current:** Every in-flight `preview_tasks` entry is lost.  The
preview remains in `extracting` status in Mongo until a client polls
`GET /{id}` and `recover_preview_if_stale` fires.  The VLM result (if
the VLM call had completed) is discarded.  The user must wait for the
full `preview_extracting_window_seconds` (150 s) before seeing a
recoverable `VLM_FAILED` state.

**Impact:** Up to 150 s of wasted user time per affected preview.
VLM API cost (the call may have completed but the result is lost) is
wasted.

### 2. Multiple workers

**Current:** `preview_tasks` is process-local.  Worker A cannot see
worker B's tasks.  If a load balancer routes a retry request to a
different worker, `_register_preview_task` on the new worker cannot
cancel the old worker's task.  Two extraction tasks may run
concurrently for the same preview.

**Mitigating factor:** `_run_extraction_task` re-checks
`_load_active_extracting_preview` before writing results.  It
compares `requestStartedAt` to ensure it only writes if the current
extraction is still the active one.  This prevents stale writes but
does not prevent wasted VLM API calls.

### 3. In-flight task eviction

**Current:** `_register_preview_task` cancels any existing task for
the same preview ID before registering a new one.  This works within
a single process.  If the old task is mid-VLM-call, the cancellation
propagates, but the VLM HTTP request itself may not be cancelled
(depends on the HTTP client implementation).

### 4. Graceful shutdown

**Current:** The FastAPI `lifespan` function starts worker tasks
(`_run_worker_with_logging`, `_run_extraction_worker_with_logging`)
and waits up to 5 s for them on shutdown.  However, `preview_tasks`
entries are **not** awaited on shutdown.  They are simply abandoned.

## Candidate Approaches

### Approach A: Keep process-local, improve lazy recovery

Reduce `preview_extracting_window_seconds` and tighten the stale
recovery window.  No architectural change.

**Pros:** Zero code change to extraction logic.  Smallest diff.
**Cons:** Does not solve multi-worker safety.  Does not recover VLM
results on restart.  Users still wait for the recovery window.

### Approach B: Mongo-backed extraction state + background worker

Mirror the bulk ingestion pattern: store extraction state in Mongo,
run a background worker that claims and processes extraction tasks.

**Design sketch:**

```python
# Worker loop (similar to run_extraction_worker)
async def run_preview_extraction_worker(database, storage, settings, ...):
    while not stop_event.is_set():
        preview = await claim_next_extracting_preview(database, settings)
        if preview is None:
            await asyncio.sleep(poll_interval)
            continue
        await _run_extraction_task(database, preview["_id"], ...)

# Claim function (atomic, like claim_item)
async def claim_next_extracting_preview(database, settings):
    result = await database["ingestion_previews"].find_one_and_update(
        {
            "status": "extracting",
            "extraction.requestStartedAt": {"$lt": utc_now() - timedelta(...)},
            "extraction.workerId": None,  # unclaimed
        },
        {"$set": {
            "extraction.workerId": worker_id,
            "extraction.leaseExpiresAt": utc_now() + timedelta(seconds=lease),
        }},
        return_document=ReturnDocument.AFTER,
    )
    return result
```

**Pros:** Durable.  Survives restarts.  Multi-worker safe.  VLM
results not lost.  Consistent with existing bulk ingestion pattern.
**Cons:** Adds a background worker.  Requires lease management.
Changes the sync-wait pattern (the API endpoint would poll Mongo
instead of awaiting an asyncio task).

### Approach C: External job queue (Celery / Dramatiq)

Replace `preview_tasks` with a Celery or Dramatiq task queue backed
by Redis or RabbitMQ.

**Pros:** Battle-tested durability.  Built-in retry, monitoring, and
concurrency control.
**Cons:** Introduces a new infrastructure dependency (Redis/RabbitMQ
+ worker process).  Significant operational complexity for a
single-server app.  Overkill if the app runs on one process.

### Approach D: Hybrid - Mongo state + in-process execution

Keep the asyncio task pattern but make the **state** durable:
- Write `extraction.workerId` to Mongo when starting a task.
- On `GET /{id}`, if `extraction.workerId` is set and the worker is
  alive (check a heartbeat timestamp), trust the in-flight task.
- If the heartbeat is stale, mark as `VLM_FAILED` and allow retry.

**Pros:** Minimal change to the happy path.  No background worker
needed.
**Cons:** Does not solve multi-worker safety (each worker still
runs its own tasks).  Heartbeat logic adds complexity.  VLM results
still lost on restart.

## Dimension Analysis

### Durability

| Approach | VLM result on restart | Extraction state on restart |
|---|---|---|
| A (current) | Lost | Recovered lazily after window |
| B (Mongo worker) | Preserved | Preserved (claimed from Mongo) |
| C (Celery) | Preserved | Preserved (queued in Redis) |
| D (hybrid) | Lost | Recovered via heartbeat |

### Restart recovery

| Approach | Recovery trigger | User-visible delay |
|---|---|---|
| A | Client polls GET | Up to 150 s |
| B | Worker polls Mongo | Poll interval (5 s default) |
| C | Worker picks up from queue | Near-instant |
| D | Client polls GET | Heartbeat timeout |

### Concurrency (multi-worker)

| Approach | Multi-worker safe | Wasted VLM calls |
|---|---|---|
| A | No (task not visible cross-process) | Yes (duplicate extractions) |
| B | Yes (atomic claim) | No |
| C | Yes (queue dedup) | No |
| D | No | Yes |

### Latency

| Approach | Sync-wait behavior | Poll latency |
|---|---|---|
| A | asyncio.wait_for(task, 25s) | N/A (task-local) |
| B | Poll Mongo (replace task await) | 5 s poll interval |
| C | Poll Celery result | Near-instant |
| D | asyncio.wait_for(task, 25s) + heartbeat | N/A |

**Note:** Approach B changes the sync-wait from awaiting an asyncio
task to polling Mongo.  This adds up to one poll-interval of latency
for the sync-wait completion path.  This can be mitigated by using a
shorter poll interval (1-2 s) during the sync-wait window.

### Operational complexity

| Approach | New infra | New worker process | Monitoring |
|---|---|---|---|
| A | None | None | None |
| B | None (uses existing Mongo) | None (runs in FastAPI lifespan) | Existing |
| C | Redis/RabbitMQ | Yes (Celery worker) | New |
| D | None | None | Heartbeat |

### Migration path

| Approach | Migration difficulty | Risk | Reversibility |
|---|---|---|---|
| A | Trivial | Low | N/A (no change) |
| B | Moderate (new worker + claim logic) | Medium | High (can fall back to lazy recovery) |
| C | High (new infra + worker + config) | High | Low (new dependency) |
| D | Low-Moderate | Medium | High |

## Required Tests for Future Migration

If Approach B is implemented, the following tests are needed:

### Concurrency tests

- Two workers claim the same preview: only one succeeds, the other
  gets `None`.
- A worker crashes mid-extraction: another worker claims the preview
  after the lease expires.
- A worker completes extraction: the preview transitions to `READY`.

### Regression tests

- `start_extraction` still writes `extracting` status + extraction
  metadata to Mongo.
- `_run_extraction_task` still writes `READY` or `VLM_FAILED`.
- `recover_preview_if_stale` still works as a fallback.
- The sync-wait path still returns 202 when extraction is in-flight.
- The retry endpoint still works after a `VLM_FAILED`.

### FakeDatabase support gaps

- `find_one_and_update` with conditional `extraction.workerId` check
  may need FakeDatabase support (the existing `claim_item` test
  already exercises `find_one_and_update` with `$elemMatch`).

## Recommendation

**Recommended approach: B (Mongo-backed extraction state + background
worker).**

### Rationale

1. **Consistency:** The bulk ingestion flow already uses this
   pattern successfully (`run_extraction_worker` +
   `claim_item`).  Applying the same pattern to single-preview
   ingestion reduces cognitive load and reuses proven code.
2. **No new infrastructure:** Unlike Approach C, no Redis/RabbitMQ
   or separate worker process is needed.  The worker runs in the
   existing FastAPI `lifespan`, next to the bulk ingestion worker.
3. **Solves the core problems:** Durability (VLM results preserved),
   multi-worker safety (atomic claim), and restart recovery (worker
   re-polls Mongo) are all addressed.
4. **Acceptable trade-off:** The sync-wait path changes from
   awaiting an asyncio task to polling Mongo.  This adds a small
   latency increase (1-2 s poll interval during the 25 s sync-wait
   window) but is well within the existing 202-poll UX.

### Not recommended

- **Approach A** does not solve multi-worker safety or VLM result
  loss.
- **Approach C** adds infrastructure complexity disproportionate to
  the app's current scale (single server, low concurrency).
- **Approach D** does not solve multi-worker safety or VLM result
  loss, and adds heartbeat complexity.

## Phased Implementation Outline

### Phase 1: Add Mongo-backed extraction worker (no API change)

**Scope:** Add a `run_preview_extraction_worker` that polls
`ingestion_previews` for claimable `extracting` previews and
processes them.  Run it in `lifespan` alongside the existing workers.
Keep `start_extraction` creating asyncio tasks for the sync-wait
path.  The worker acts as a safety net: if the asyncio task is lost
(restart), the worker picks up the preview.

**Estimated effort:** New worker loop + claim function + tests.

### Phase 2: Migrate sync-wait to poll Mongo (optional)

**Scope:** Replace `wait_for_preview_result(task, timeout)` with a
Mongo poll loop that checks if the preview has transitioned out of
`extracting`.  Remove `preview_tasks` dict.

**Estimated effort:** Change 2 API endpoints (extract, retry) +
remove `preview_tasks` + update tests.

### Phase 3: Remove stale-preview lazy recovery (optional)

**Scope:** Once the worker reliably processes all `extracting`
previews, `recover_preview_if_stale` becomes redundant.  Evaluate
whether it can be removed or kept as a belt-and-suspenders safety
net.

**Estimated effort:** Remove or simplify `recover_preview_if_stale`
+ update tests.

## Conclusion

The current process-local `preview_tasks` dict works for a
single-process deployment but loses VLM results on restart and is
unsafe with multiple workers.  The existing bulk ingestion worker
pattern (`run_extraction_worker` + atomic `claim_item`) already
solves these problems and can be adapted to single-preview
ingestion without introducing new infrastructure.

A phased migration starting with a safety-net worker (Phase 1)
would provide the most safety improvement with the least risk.
Each phase should include concurrency tests and preserve all
existing regression tests.
