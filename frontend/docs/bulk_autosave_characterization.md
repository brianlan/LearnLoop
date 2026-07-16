# Bulk ingestion autosave failure characterization

This document records the current integrated behavior of `BulkIngestionWizard`
plus `BulkReviewStep` when a draft autosave fails. It is a characterization
artifact only: it pins observed behavior, names the ownership boundary where the
behavior changes, and deliberately stops before any correction.

## Scope

- `frontend/src/components/BulkIngestionWizard.autosave.test.tsx`
- `frontend/src/components/BulkReviewStep.test.tsx` (existing isolated coverage,
  unchanged)
- This document

## Out of scope

- Changing `handleUpdateItemDraft` to propagate rejections.
- Changing retry delays, caps, debounce timers, dirty/saving/failure state logic,
  or messages.
- Changing Continue/submit gating.
- Extracting an autosave hook or adding a state framework.
- Changing polling, focus, navigation, expiry handling, API contracts, or
  backend behavior.

## Ownership boundary

- `BulkReviewStep` owns local draft state, debounced autosave scheduling, dirty
  tracking, saving tracking, save-failure tracking, and bounded retry. When its
  `onUpdateDraft` prop rejects, it shows "Save failed, retrying...", increments
  the failure counter, and schedules another attempt with exponential backoff.
- `BulkIngestionWizard` owns the callback passed to `BulkReviewStep` as
  `onUpdateDraft`. That callback is `handleUpdateItemDraft`, which wraps the API
  call `updateItemDraft(batch.id, itemId, draft)`, then on success calls
  `setBatchAndStep(response.batch)`, and on failure calls
  `handleMutationError(err, "Failed to save draft")`. Crucially, it does **not**
  rethrow or return a rejecting promise.

Because the wizard swallows the API rejection, `BulkReviewStep` receives a
resolved promise and treats the operation as successful. The isolated component's
retry/backoff behavior is therefore **not exercised** at the integrated
boundary. This divergence is owned by `BulkIngestionWizard`.

## Observed scenarios

### 1. Pending save gates Continue

When the user edits a field while `updateItemDraft` is unresolved:

- `updateItemDraft` is called once after the debounce window (roughly 500 ms
  with no prior failures).
- `Continue to submit` becomes disabled while the save is pending.
- The edited field remains enabled and interactive during the save.
- After the promise resolves and `setBatchAndStep` updates the batch,
  `Continue to submit` becomes enabled again.

### 2. Rejected API save at the integrated boundary

When `updateItemDraft` rejects with `Error("network error")`:

- The wizard callback `handleUpdateItemDraft` catches the rejection.
- `handleMutationError` sets the wizard-level error to the error message
  (`"network error"`) because the error is an `Error` instance. The fallback
  message `"Failed to save draft"` is **not** used.
- The wizard renders its error view (`bulk-wizard-error`), replacing the entire
  review UI including `BulkReviewStep`.
- `BulkReviewStep` never sees the rejection, so it does **not**:
  - increment `saveFailures`,
  - show "Save failed, retrying...",
  - schedule a retry,
  - or keep `Continue to submit` disabled.
- The `updateItemDraft` API call is made exactly once. Advancing timers by 10
  seconds produces no additional calls.

### 3. Later successful save while wizard error is showing

After a failed save, if the user edits again and `updateItemDraft` succeeds:

- The underlying API call succeeds.
- `handleUpdateItemDraft` calls `setBatchAndStep(response.batch)`.
- However, the wizard-level error state is **not cleared** on the success path.
- The error view remains rendered, replacing the review UI, so the user cannot
  observe item-level recovery inside `BulkReviewStep`.

This means the integrated path does not recover visually from the wizard-level
error even when the next save succeeds.

## Invariants and divergences

| Concern | Isolated `BulkReviewStep` | Integrated `BulkIngestionWizard` + `BulkReviewStep` |
|---|---|---|
| Callback rejection | Sees `onUpdateDraft` reject | Wrapper swallows rejection and resolves |
| Retry after failure | Bounded exponential backoff | No retry |
| Item-level failure UI | "Save failed, retrying..." | Never shown |
| Failure message source | N/A (internal state) | Wizard shows raw API error message |
| Continue gating after failure | Disabled until retry succeeds | Not applicable because wizard error view replaces UI |
| Error recovery | Clears on next success | Wizard error persists on next success |

## Commands

Run the focused characterization tests:

```bash
./scripts/agent-env.sh test frontend src/components/BulkIngestionWizard.autosave.test.tsx
```

Run the broader wizard/review suites:

```bash
./scripts/agent-env.sh test frontend
```

Run E2E coverage:

```bash
./scripts/agent-env.sh test e2e
```

## Future approval gate

Any change to the rejection-propagation contract, retry behavior, timer values,
error clearing, or Continue gating is a runtime behavior change. It requires a new
human/council approval and a separate issue with a rollback plan. This document
must be updated if those decisions are made.
