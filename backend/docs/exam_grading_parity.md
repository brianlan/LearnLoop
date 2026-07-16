# Exam Grading Parity

This document records the behavior of the exam-grading execution paths and the
characterization tests that keep them aligned. It is a characterization artifact
only; no production behavior or module location was changed.

## Paths

| Path | Entry point | When it runs | Transaction boundary |
|------|-------------|--------------|----------------------|
| Synchronous | `app.presentation.exams._submit_exam_synchronous` | `POST /api/v1/exams/{id}/submit` when no item needs VLM grading | Single `with_transaction` covering exam state, items, summary, and problem tracking |
| Worker | `app.infrastructure.worker.exam_grading_worker.process_exam_grading_task` | Celery task `exam_grading_task` when at least one item needs VLM grading | Per-item updates outside a transaction; finalization uses `with_transaction` for exam state, tracking, and task completion |
| Self-report | `app.presentation.exams.self_report_exam_item` | `POST /api/v1/exams/{id}/items/{itemId}/self-report` for a `pending-review` item | Single `with_transaction` covering item grading, summary, and problem tracking |

All three paths use the same item-grading helpers in `app.presentation.exam_grading`:

- `grade_item`
- `grade_objective_item`
- `grade_short_answer_item`
- `build_tracking_update`

## Grading outcomes

A graded item stores:

```text
items[].grading:
  status                -> ungraded | correct | incorrect | pending-review
  method                -> normalized-match | vlm | self-report
  isCorrect             -> bool | None
  score                 -> 0..1 | None
  feedback              -> str | None
  providerModel         -> str | None
  rawProviderResponse   -> Any | None
  gradedAt              -> datetime | None
  retryCount            -> int
  selfReportedCorrect   -> bool | None
```

Terminal item statuses (`CORRECT`, `INCORRECT`, `PENDING_REVIEW`) are never
re-graded by the worker (`TERMINAL_GRADING_STATUSES` in
`app.presentation.exam_helpers`).

The exam summary is produced by `build_exam_summary` using `compute_summary`:

```text
summary:
  totalProblems
  answeredProblems
  gradedProblems
  pendingProblems
  correctProblems
  failedProblems
  score
```

Both the synchronous path and the worker path store this summary. The
synchronous path computes it once after all items are graded; the worker
recomputes it after every item and stores the final value at finalization.

## Tracking updates

All paths update `problems.tracking` with `build_tracking_update`, which
increments:

- `exposureCount`
- `correctCount` if the item is correct
- `failedCount` if the item is incorrect

and sets:

- `lastTestedAt`
- `lastAttemptCorrect`

There is one deliberate divergence:

- The synchronous path does **not** increment `exposureCount` during exam
creation. It increments only inside the submit transaction.
- The worker path operates on an already-created exam, so it also increments
tracking exactly once per graded item.

Consequently, a problem that starts with `exposureCount: 0` ends at `1` after one
synchronous submit or one worker task, not `2`.

## Objective-only parity

For an exam that contains only objective items (`single-choice`,
`multi-choice`, `fill-in-the-blank`), the synchronous submit and a single worker
run produce the same final item-level grading, summary, and problem tracking.

This is asserted by
`tests/integration/test_exam_grading_parity.py::test_parity_objective_only_sync_matches_worker`.

## Short-answer / essay flow

Items with `problemType: short-answer` require a VLM judge and are always routed
through the worker.

- `grade_item` calls `grade_short_answer_item`, which calls the VLM with a judge
prompt.
- If the VLM returns a parseable verdict, the item becomes `correct` or
`incorrect` with `method: vlm`.
- If the VLM raises a retryable `VLMError`, `grade_short_answer_item` performs
exactly **one** retry (hardcoded limit in `backend/app/presentation/exam_grading.py`).
- After the retry is exhausted, or on a non-retryable or unexpected error, the
item becomes `pending-review` with `method: vlm` and the error stored in
`feedback` / `rawProviderResponse`.

See
`tests/integration/test_exam_grading_parity.py::test_parity_short_answer_vlm_retry_then_success`
and
`test_parity_short_answer_retry_exhaustion_then_self_report_updates_tracking_once`.

## Self-report behavior

When an item is `pending-review`, a learner can submit a self-report via
`POST /api/v1/exams/{id}/items/{itemId}/self-report`.

- If `isCorrect: true`, the item becomes `correct`, `method: self-report`,
`score: 1`.
- If `isCorrect: false`, the item becomes `incorrect`, `method: self-report`,
`score: 0`.

The self-report endpoint rejects items whose status is not `pending-review`.
It updates the exam summary and the problem tracking exactly once in the same
transaction.

## Worker idempotency

The worker is designed to be safely re-run on the same exam:

- Already-terminal items are skipped.
- Each non-terminal item is graded and persisted exactly once per successful run.
- `_finalize_exam` only updates tracking for items whose status is not
`pending-review`, so a second run cannot double-count correct/failed counts.

See
`tests/integration/test_exam_grading_parity.py::test_parity_worker_idempotent_rerun_does_not_double_apply_tracking`.

## Failure / restart behavior

If the worker loses ownership or crashes between items, a later task claim
resumes at the first non-terminal item. Terminal items are left untouched.
This is asserted by
`test_parity_worker_restart_preserves_terminal_items_and_tracking`.

Ownership is guarded by a per-task `claimToken` and `leaseUntil` timestamp:

- `_claim_task` atomically claims a pending task or reclaims a stale-leased task.
- `_refresh_lease` keeps the lease alive while grading runs.
- `_verify_ownership` is checked before every item persistence and before
finalization.
- `_release_task` returns the task to `pending` if processing fails.

## Transaction rollback / all-or-nothing behavior

The synchronous path and the self-report path both wrap exam/item/summary and
problem-tracking writes in `session.with_transaction`. The worker's
`_finalize_exam` also uses `session.with_transaction` when a real Mongo adapter
is available.

**Why a dedicated rollback characterization test is not included:**

The existing integration test harness (`tests/integration/conftest.py`) uses
`FakeDatabase` and `FakeMongoAdapter`, which are in-memory fakes without
multi-document transaction support. `FakeSession.with_transaction` simply runs
the callback with a dummy session and never aborts. Exercising a real
`with_transaction` abort requires a real MongoDB replica set, a test-specific
adapter, and a deterministic way to trigger a failure inside the callback
without adding production seams solely for testability.

The strongest available evidence is therefore:

1. The production code uses `session.with_transaction` in the three mutating
paths listed above.
2. The worker's per-item grading loop is intentionally **outside** a
transaction: each item is persisted as soon as it is graded so that a crash
can resume from the last completed item rather than losing all progress.
3. The worker's finalization step groups the exam-state transition,
problem-tracking updates, and task completion into one transaction when the
adapter supports sessions.

A real-Mongo rollback test would require a new test harness that is not part of
the current fake-based integration seam. That work is out of scope for this
characterization issue.

## VLM and S3/storage lifecycle ownership

### Current ownership

**Synchronous path:**

- `get_grading_vlm_client` (in `app.presentation.deps`) is a FastAPI dependency
that constructs a grading VLM client per request and closes it with
`aclose()` in a `finally` block after the endpoint returns.
- `get_s3_storage` constructs an `S3StorageAdapter` per request. The adapter is a
thin wrapper around `boto3.client("s3", ...)`; the boto3 client is created
lazily on first use and is not explicitly closed per request. It lives for the
request duration and is garbage collected afterward.
- The sync endpoint calls `grade_item` with the request-scoped VLM client and
storage adapter, then runs the result-persistence transaction.

**Worker path:**

- `run_exam_grading_worker` constructs one `build_grading_vlm_client(settings)`
per claimed task in its `try` block and calls `await vlm_client.aclose()` in
the matching `finally` block. So VLM client lifecycle is one client per task,
closed after `process_exam_grading_task` returns or raises.
- The storage adapter is constructed once at worker startup (or passed in) and
shared across all tasks. It is not closed per task.
- `process_exam_grading_task` receives the already-built VLM client and storage
adapter as arguments; it does not construct or close them itself.

### Candidate later boundary

A possible later refactor is to extract pure grading rules (score computation,
answer normalization, summary computation, tracking deltas) into
`app/domain/exam` while keeping concrete VLM and S3 coordination outside the
domain layer. That boundary is **not approved here**.

- Current ownership: orchestration lives in `app.presentation.exams` and
`app.infrastructure.worker.exam_grading_worker`; shared grading logic lives in
`app.presentation.exam_grading`; domain models live in `app.domain.models`.
- Candidate ownership: pure, I/O-free grading rules under `app/domain/exam`;
VLM client construction/close and S3 adapter lifecycle remain in presentation
and infrastructure code.
- This document recommends that shape, but implementing it requires a new
council/human review and its own issue/rollback plan per R-001.

## Running the parity tests

```bash
cd backend
uv run --frozen --active pytest tests/integration/test_exam_grading_parity.py -q
```

To run the broader exam-grading related suites:

```bash
uv run --frozen --active pytest \
  tests/presentation/test_exam_grading.py \
  tests/worker/test_exam_grading_worker.py \
  tests/api/test_exams.py \
  tests/integration/test_exam_workflow.py \
  tests/integration/test_exam_grading_parity.py \
  -q
```

## What parity does **not** cover

- **Response payloads:** the synchronous submit returns a JSON response; the
worker writes to the database and emits nothing to the caller.
- **Timing / ordering:** the worker is asynchronous and may interleave with
other operations.
- **VLM availability edge cases:** transient VLM failures are retried once but
not guaranteed to converge if the service is permanently down.
- **Real-Mongo rollback aborts:** the current fake test seam cannot reproduce a
`with_transaction` abort; only the code path is documented above.
- **Exam creation side effects:** creation may log analytics or update selection
metadata that the worker path does not reproduce.

## Human / council review gate

Changes that alter `app.presentation.exam_grading`,
`app.presentation.exams._submit_exam_synchronous`,
`app.presentation.exams.self_report_exam_item`, or
`app.infrastructure.worker.exam_grading_worker` must:

1. Keep `backend/docs/exam_grading_parity.md` accurate and up to date.
2. Keep the parity tests in
`backend/tests/integration/test_exam_grading_parity.py` green.

Any move of grading rules into `app/domain/exam`, any service-layer
extraction, any retry/transaction/ownership redesign, or any change to the VLM
or S3 lifecycle is out of scope for this issue. Such work requires a new
council/human decision under R-001 and its own follow-up issue with a rollback
plan.
