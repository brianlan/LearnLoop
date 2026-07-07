# Bulk Ingestion Repository Mutation Catalog

Investigation/enabler for LL-B2 (issue #416). This catalog maps the bulk
ingestion repository mutation surface in
`backend/app/infrastructure/ingestion/repository.py`, so a future helper
extraction can be verified to preserve behavior. It documents each mutation
function's purpose, current write pattern, existing coverage, remaining test
gaps, and recommended future test names.

**No production code was changed to produce this catalog.** Helper extraction
is intentionally postponed to a future issue.

## Scope

Cataloged: every data-writing function in `repository.py` that targets the
`ingestion_batches` collection, plus the index-creation helper.

Out of scope (per issue #416):

- `claim_item` — atomic `find_one_and_update` path.
- `find_one_and_update` and other atomic paths.
- Concurrency / atomicity changes.
- Durable preview task redesign, worker redesign.
- Helper extraction (future issue).

Observed but **not cataloged** (different collection / legacy single-image
flow): direct `ingestion_previews` writes in
`app/presentation/ingestion_workflow.py` — see
[Out-of-Scope: direct single-image preview DB writes](#out-of-scope-direct-single-image-preview-db-writes)
below.

## Write-pattern summary

All non-atomic mutation functions use the same **read-modify-write** pattern:

1. `find_one({"_id": batch_id, "userId": user_id})` → raise `ValueError("Batch not found")` if `None`.
2. Mutate the in-memory `images` and/or `items` arrays (replace matching
   element's fields in Python).
3. `update_one({"_id": batch_id, "userId": user_id}, {"$set": {...}})` — the
   `$set` **replaces the entire `images` and/or `items` array** plus
   `updatedAt` (and conditionally `status`).

This full-array-replacement is the regression-prone surface a future helper
extraction must preserve exactly (field set, array identity, `updatedAt`).

## Mutation function catalog

| Function | Purpose | Existing coverage | Current write pattern (`$set`) | Test gaps | Recommended future test names |
|---|---|---|---|---|---|
| `create_batch` | Insert a new active batch document. | Direct: `test_create_batch_persists_and_loads` | `insert_one(build_batch_document(...))` | None significant. | — |
| `add_source_image` | Append an uploaded image to a batch. | Direct: `test_add_source_image_round_trips` | `$set: {images, updatedAt}` (full array append) | None significant. | — |
| `add_items_for_image` | Create queued items for an image and commit the image. | Direct: `test_add_items_for_image_commits_image_and_creates_items` | `$set: {images, items, updatedAt}` | Multi-item ordering continuation. | `test_add_items_for_image_continues_ordering_across_calls` |
| `start_image_detection` | Mark an image `detecting`. | Direct (new): `test_start_image_detection_sets_image_to_detecting` | `$set: {images, updatedAt}` | "Batch not found" `ValueError`; image-not-found leaves array unchanged silently. | `test_start_image_detection_raises_when_batch_missing` |
| `save_image_detection_success` | Mark image `ready` with subject/boxes/detection. | Direct (new): `test_save_image_detection_success_sets_ready_with_detection_payload` | `$set: {images, updatedAt}` (detection subdoc: `{model, rawProviderResponse, failureCode:null, failureMessage:null}`) | None significant. | — |
| `save_image_detection_failure` | Mark image `detect-failed` with failure detection. | Direct (new): `test_save_image_detection_failure_sets_detect_failed_with_detection_payload` | `$set: {images, updatedAt}` (detection subdoc: `{model:null, rawProviderResponse:null, failureCode, failureMessage}`) | None significant. | — |
| `save_image_boxes_and_subject` | Mark image `ready`, set boxes, conditionally set subject. | Direct (new): `test_save_image_boxes_and_subject_sets_ready_boxes_and_conditional_subject` | `$set: {images, updatedAt}` (subject set only when not `None`) | None significant. | — |
| `delete_batch_image` | Mark image `deleted` and all its items `deleted`. | Direct (new): `test_delete_batch_image_marks_image_and_items_deleted` | `$set: {images, items, updatedAt}` | Multi-item/multi-image interaction. | `test_delete_batch_image_marks_all_items_for_image_deleted` |
| `commit_image_boxes` | Create queued items from image boxes; mark image `committed`; idempotent. | Direct (new): `test_commit_image_boxes_creates_items_and_is_idempotent` | `$set: {images, items, updatedAt}` (no-op when already `committed`) | `next_order` continuation across multiple commits; boxes-empty case. | `test_commit_image_boxes_continues_item_ordering` |
| `claim_item` | Atomically lease an item for extraction. | Indirect via worker tests; **atomic path out of scope** | `find_one_and_update` with `$set` (positional `items.$.`) + `$inc` (`items.$.retryCount`) | Atomic/concurrency behavior not characterized (out of scope). | `test_claim_item_acquires_queued_item` (future, when atomic paths in scope) |
| `save_item_extraction_success` | Mark item `ready` with crop/draft/extraction, clear lease. | Direct (new): `test_save_item_extraction_success_sets_ready_payload_and_clears_lease` | `$set: {items, updatedAt}` | None significant. | — |
| `save_item_extraction_failure` | Mark item `failed` with extraction, clear lease. | Direct (new): `test_save_item_extraction_failure_sets_failed_and_clears_lease` | `$set: {items, updatedAt}` | None significant. | — |
| `reset_item_for_retry` | Re-queue `failed`/`submit-failed`/lease-expired `extracting` items. | Direct (new): `test_reset_item_for_retry_resets_failed_and_lease_expired_and_skips_ineligible` + indirect worker | `$set: {items, updatedAt}` (conditional; no write if ineligible) | `submit-failed` eligibility (covered indirectly); item-not-found returns `False`. | `test_reset_item_for_retry_requeues_submit_failed_item` |
| `update_item_draft` | Merge allowed draft keys into an item. | Direct (new): `test_update_item_draft_merges_allowed_keys_and_returns_none_for_missing` | `$set: {items, updatedAt}` (allowed: `text, problemType, graphDsl, correctAnswer, tags, subject`) | None significant. | — |
| `mark_item_deleted` | Save `previousStatus`, mark item `deleted`; idempotent for `deleted`/`submitted`. | Direct (new): `test_mark_item_deleted_saves_previous_status_and_is_idempotent` | `$set: {items, updatedAt}` (conditional) | `submitted` idempotency (returns `True` without `previousStatus`). | `test_mark_item_deleted_is_idempotent_for_submitted` |
| `undo_item_deletion` | Restore `previousStatus`, drop `deletedAt`; `False` if not deleted/no prior status. | Direct (new): `test_undo_item_deletion_restores_status_and_returns_false_when_not_deleted` | `$set: {items, updatedAt}` (conditional) | Missing `previousStatus` returns `False`. | `test_undo_item_deletion_returns_false_without_previous_status` |
| `submit_items_and_complete_batch` | Persist per-item submit outcomes; complete batch when all non-deleted submitted. | Direct (new): `test_submit_items_and_complete_batch_completes_only_when_all_submitted` | `$set: {items, updatedAt, [status: completed]}` (conditional `status`) | Deleted items excluded from completion check. | `test_submit_items_completes_when_only_deleted_items_remain_unsubmitted` |
| `mark_batch_cleaned` | Mark batch `deleted`. | Direct: `test_mark_batch_cleaned` | `$set: {status, updatedAt}` | None significant. | — |
| `ensure_batch_indexes` | Create batch collection indexes. | None (DDL; `create_index` is a no-op on `FakeCollection`) | `create_index` per `BATCH_INDEXES` | Index creation not verified against a real Mongo. | `test_ensure_batch_indexes_creates_expected_indexes` (requires real/index-fake) |

## Out-of-Scope: direct single-image preview DB writes

`app/presentation/ingestion_workflow.py` performs direct writes to the
**`ingestion_previews`** collection (the legacy single-image ingestion flow),
which is a separate collection and code path from the bulk-ingestion
`ingestion_batches` repository cataloged above. These are observed but
**intentionally not cataloged** here, per issue #416.

Observed direct `ingestion_previews` writes in `ingestion_workflow.py`:

- Line 179: `update_one({"_id": preview_id}, {"$set": {status, extraction, ...}})` — persist extraction result.
- Line 211: `update_one({"_id": preview_id}, {"$set": {status, extraction, finishedAt, ...}})` — finalize extraction.
- Line 264: `update_one({"_id": preview["_id"]}, {"$set": {status, ...}})` — transition to extracting.
- Line 308: `update_one({"_id": preview["_id"]}, {"$set": {status, ...}})` — recover preview state.

A future catalog/characterization for the single-image preview flow should be
a separate issue if needed.

## Verification

Characterization tests added in
`backend/tests/infrastructure/test_ingestion_persistence.py` cover the exact
`update_one` / `$set` payloads and resulting stored state for all cataloged
non-atomic mutation functions.

Verification command (from issue #416):

```
cd backend && uv run --frozen --extra dev pytest tests/infrastructure/test_ingestion_persistence.py tests/worker/test_extraction_worker.py -x -q
```

Result: **43 passed** (run against the frozen, already-synced backend venv; CI
runs the canonical `uv run --frozen --extra dev pytest` command).
