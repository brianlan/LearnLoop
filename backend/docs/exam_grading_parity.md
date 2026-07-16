# Exam Grading Parity

This document records the behavior of the two exam-grading execution paths and the
characterization tests that keep them aligned.

## Paths

| Path | Entry point | When it runs | Transaction boundary |
|------|-------------|--------------|----------------------|
| Synchronous | `app.presentation.exams._submit_exam_synchronous` | `POST /api/v1/exams/{id}/submit` for objective-only exams | Single function, writes exam + problems + task inside the call |
| Worker | `app.infrastructure.worker.exam_grading_worker.process_exam_grading_task` | Celery task (`exam_grading_task`) for exams containing short-answer / essay items | Per-task function; idempotent re-runs are safe |

Both paths use the same item-grading helpers in `app.presentation.exam_grading`:

- `grade_item`
- `build_tracking_update`
- `_compute_objective_grade`

## Grading outcomes

A graded item is stored as:

```text
items[].grading:
  status                -> graded | pending_review | needs_review
  method                -> exact_match | llm_judge | self_report | system_error
  isCorrect             -> bool | None
  score                 -> 0..1
  retryCount            -> int
  selfReportedCorrect   -> bool | None
```

The worker additionally writes:

```text
summary:
  autoGradedCount
  pendingReviewCount
  needsReviewCount
  completedCount
```

The synchronous path does not populate `summary` for objective-only exams
because the result is returned immediately; the fields are not required by the
API contract for that endpoint.

## Tracking updates

Both paths update `problems.tracking` with `build_tracking_update`, which
increments:

- `exposureCount`
- `correctCount` if `isCorrect`
- `failedCount` if not `isCorrect`
- sets `lastTestedAt` and `lastAttemptCorrect`

There is one intentional divergence:

- The synchronous path does **not** increment `exposureCount` during exam
  creation; it only increments during the grading transaction.
- The worker path also does not select problems, so it matches the sync
  end-state after one grading transaction.

Consequently, a problem that starts with `exposureCount: 0` ends at `1` after
one synchronous submit or one worker task, not `2`.

## Objective-only parity

For an exam that contains only objective items (`single-choice`, `multiple-choice`,
`fill-in-the-blank`), the synchronous submit and a single worker run produce the
same final item-level grading and problem tracking.

This is asserted by
`tests/integration/test_exam_grading_parity.py::test_parity_objective_only_sync_matches_worker`.

## Short-answer / essay flow

Items with `problemType: short-answer` or `problemType: essay` require a VLM
judge and are always routed through the worker.

- `grade_item` calls the VLM with a judge prompt.
- If the VLM returns a parseable verdict, the item is `graded` with
  `method: llm_judge`.
- If the VLM fails or returns an unparsable verdict, the worker retries up to
  `MAX_VLM_RETRIES` (configured per deployment, default `3`).
- After retries are exhausted the item becomes `pending_review` with
  `method: system_error`.

See `tests/integration/test_exam_grading_parity.py::test_parity_short_answer_retry_then_success`
and `test_parity_short_answer_retry_exhaustion_pending_review_then_self_report`.

## Self-report tracking

When an item is `pending_review` or `needs_review`, a learner (or reviewer) can
submit a self-report via `POST /api/v1/exams/{id}/items/{itemId}/self-report`.

- If `selfReportedCorrect: true`, the item becomes `graded`,
  `method: self_report`, `isCorrect: true`, `score: 1`.
- If `selfReportedCorrect: false`, the item becomes `graded`,
  `method: self_report`, `isCorrect: false`, `score: 0`.

The self-report endpoint updates problem tracking in the same way as an
auto-graded item.

## Worker idempotency

The worker is designed to be safely re-run on the same exam:

- Already-graded items are skipped.
- `pending_review` items are re-evaluated if the VLM becomes available.
- Tracking is only mutated for items that change state, so a no-op re-run does
  not double-count correct/failed counts.

See `tests/integration/test_exam_grading_parity.py::test_parity_worker_rerun_is_idempotent`.

## Failure / restart behavior

If the worker crashes between items, a re-delivery resumes at the ungraded
items. Terminal items (`graded`, `pending_review`, `needs_review`) are left
untouched. This is asserted by
`test_parity_worker_restart_preserves_terminal_items_and_tracking`.

## What parity does **not** cover

- **Response payloads**: the synchronous submit returns a JSON response; the
  worker writes to the database and emits nothing to the caller.
- **Timing / ordering**: the worker is asynchronous and may interleave with
  other operations.
- **VLM availability edge cases**: transient VLM failures are retried but not
  guaranteed to converge if the service is permanently down.
- **Exam creation side effects**: creation may log analytics or update selection
  metadata that the worker path does not reproduce.

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

## Human review gate

Changes that alter `app.presentation.exam_grading`,
`app.presentation.exams._submit_exam_synchronous`, or
`app.infrastructure.worker.exam_grading_worker` must update this document and
keep the parity tests green before merge.
