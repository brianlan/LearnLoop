# Spike: Atomic Update Migration for Ingestion Repository

## Summary

This spike catalogs the read-modify-write functions in
`backend/app/infrastructure/ingestion/repository.py`, identifies lost-update
windows, proposes candidate atomic update shapes, lists required tests for a
future migration, and recommends a phased path forward.

No production code is changed by this spike.

## Current Architecture

All batch state is stored in a single MongoDB document per batch in the
`ingestion_batches` collection. Each document contains two embedded arrays:

- `images` – one entry per uploaded image
- `items` – one entry per extraction/commit item

Most repository functions follow a **read-modify-write** pattern:

1. `find_one` loads the entire batch document.
2. Python code iterates `images` or `items`, mutates the matching element
   in place, and copies the full array.
3. `update_one` writes the **entire array** back with `$set`.

This means any concurrent modification to the same array between the
`find_one` and `update_one` is silently overwritten.

One function, `claim_item`, already uses an atomic
`find_one_and_update` with `$elemMatch` and the positional `$` operator.

## Function Inventory

### Already atomic (no change needed)

| Function | Pattern | Notes |
|---|---|---|
| `claim_item` | `find_one_and_update` with `$elemMatch` + positional `$` | Atomic claim with lease guard. Reference implementation for future work. |

### Read-modify-write (candidates for atomic migration)

| # | Function | Reads | Mutates | Writes back | Risk |
|---|---|---|---|---|---|
| 1 | `add_source_image` | batch | appends to `images` | `images` array | Medium |
| 2 | `add_items_for_image` | batch | appends to `items`, updates one image status | `images` + `items` arrays | Medium |
| 3 | `start_image_detection` | batch | one image status | `images` array | Low |
| 4 | `save_image_detection_success` | batch | one image fields | `images` array | Low |
| 5 | `save_image_detection_failure` | batch | one image fields | `images` array | Low |
| 6 | `save_image_boxes_and_subject` | batch | one image fields | `images` array | Low |
| 7 | `delete_batch_image` | batch | one image + N items status | `images` + `items` arrays | Medium |
| 8 | `commit_image_boxes` | batch | one image status + appends items | `images` + `items` arrays | Medium |
| 9 | `save_item_extraction_success` | batch | one item fields | `items` array | High |
| 10 | `save_item_extraction_failure` | batch | one item fields | `items` array | High |
| 11 | `reset_item_for_retry` | batch | one item fields | `items` array | High |
| 12 | `update_item_draft` | batch | one item draft | `items` array | High |
| 13 | `mark_item_deleted` | batch | one item status | `items` array | High |
| 14 | `undo_item_deletion` | batch | one item status | `items` array | High |
| 15 | `submit_items_and_complete_batch` | batch | multiple items + batch status | `items` array + optional `status` | Medium |

### Not read-modify-write (no change needed)

| Function | Pattern |
|---|---|
| `create_batch` | `insert_one` only |
| `get_batch` | `find_one` only |
| `get_active_batch_for_user` | `find_one` only |
| `find_cleanup_candidates` | `find` only |
| `mark_batch_cleaned` | Direct `update_one` on scalar `status` field |
| `ensure_batch_indexes` | Index creation |

## Lost-Update Analysis

### High risk: item-level updates (functions 9-14)

These functions modify a single item's fields but write back the **entire**
`items` array. If two operations target different items in the same batch
concurrently (e.g., the extraction worker saves item A while the user edits
item B's draft), the second `update_one` overwrites the first's changes.

**Concrete scenario:**

1. Worker calls `save_item_extraction_success` for item A.
2. User calls `update_item_draft` for item B.
3. Both `find_one` calls return the same batch snapshot.
4. Worker writes `items` array with item A updated.
5. User writes `items` array with item B updated — **item A's extraction
   result is lost**.

This is the most dangerous class because the extraction worker and user
front-end operate concurrently on the same batch.

### Medium risk: image-level updates and array appends (functions 1, 2, 7, 8)

These functions modify images or append items. The risk is lower because
image-level operations are typically user-initiated (not concurrent with the
worker), but a race between `commit_image_boxes` and `delete_batch_image`
could still lose updates.

`commit_image_boxes` is particularly notable because it both updates an
image's status and appends new items — two arrays written in a single
`$set`, widening the window.

### Low risk: single-image detection updates (functions 3-6)

These functions update a single image's detection fields. The extraction
worker processes one image at a time, so concurrent updates to the same
image are unlikely. However, a user-triggered `delete_batch_image` or
`save_image_boxes_and_subject` racing with detection completion could still
cause data loss.

### Already mitigated: `claim_item`

`claim_item` uses `find_one_and_update` with `$elemMatch` on the items
array and the positional `$` operator. This is the correct atomic pattern
and serves as the reference for migrating the other functions.

## Candidate Atomic Update Shapes

### Pattern A: Positional `$` for single-item updates (functions 9-14)

Replace the read-modify-write with a single `find_one_and_update` using
`$elemMatch` to match the target item and the positional `$` operator to
set only the changed fields.

**Example for `save_item_extraction_success`:**

```python
result = await _collection(database).find_one_and_update(
    {
        "_id": _object_id(batch_id),
        "userId": user_id,
        "items.itemId": item_id,
    },
    {
        "$set": {
            "items.$.status": ItemState.READY.value,
            "items.$.crop": crop,
            "items.$.draft": draft,
            "items.$.extraction": extraction,
            "items.$.leaseUntil": None,
            "items.$.updatedAt": now,
            "updatedAt": now,
        },
    },
    return_document=ReturnDocument.AFTER,
)
```

This is exactly the pattern `claim_item` already uses.

**Considerations:**

- The positional `$` operator matches the first array element that
  satisfies the query's array filter. Since `itemId` is unique within the
  batch, this is safe.
- Functions that need to return the updated item (`update_item_draft`,
  `reset_item_for_retry`, `mark_item_deleted`, `undo_item_deletion`) can
  use `return_document=ReturnDocument.AFTER` and extract the item from the
  result, mirroring `claim_item`.
- Functions with conditional logic (e.g., `reset_item_for_retry` checks
  status and lease, `mark_item_deleted` checks status, `undo_item_deletion`
  checks status == DELETED) can add those conditions to the `$elemMatch`
  query. If no document matches, the function returns `False` or `None`,
  preserving existing behavior.

### Pattern B: `$push` for array appends (functions 1, 2, 8)

Replace the read-modify-write with `$push` to append new elements without
reading the full array.

**Example for `add_source_image`:**

```python
await _collection(database).update_one(
    {"_id": _object_id(batch_id), "userId": user_id},
    {
        "$push": {"images": image_document},
        "$set": {"updatedAt": current},
    },
)
```

**Considerations:**

- `add_items_for_image` and `commit_image_boxes` need to both `$push`
  items and `$set` the image status. These can be combined in a single
  `update_one` with both operators.
- `commit_image_boxes` has idempotency logic (returns existing items if
  already committed) and order calculation (`max(item["order"]) + 1`).
  The idempotency check requires a read, but the write can still be atomic.
  The order calculation may need a separate approach (e.g., storing a
  counter or using `$inc`).

### Pattern C: Multi-field positional update (function 7)

`delete_batch_image` updates one image's status and multiple items'
statuses. This can use two atomic operations:

1. `update_one` with positional `$` for the image status.
2. `update_many` or `update_one` with array filters for items with matching
   `imageId`.

Alternatively, use array filters (`$[elem]`) to update all matching items
in a single operation:

```python
await _collection(database).update_one(
    {
        "_id": _object_id(batch_id),
        "userId": user_id,
        "images.imageId": image_id,
    },
    {
        "$set": {
            "images.$.status": ImageState.DELETED.value,
            "images.$.updatedAt": now,
            "items.$[elem].status": ItemState.DELETED.value,
            "items.$[elem].updatedAt": now,
            "updatedAt": now,
        },
    },
    array_filters=[{"elem.imageId": image_id, "elem.status": {"$ne": ItemState.DELETED.value}}],
)
```

**Considerations:**

- Array filters require MongoDB 3.6+ (well within supported range).
- The FakeDatabase test harness would need to support `array_filters` in
  `update_one`. This may require extending `FakeCollection.update_one`.

### Pattern D: Multi-item update with completion check (function 15)

`submit_items_and_complete_batch` updates multiple items and optionally
sets the batch status to `completed`. This is the most complex function
to migrate because it:

1. Updates N items based on submit results.
2. Checks if all non-deleted items are submitted.
3. Sets batch status to `completed` if so.

A fully atomic migration would require:

1. A loop of `update_one` with positional `$` for each item result.
2. A final `find_one_and_update` that checks the condition and sets
   `status` if all items are submitted.

Or a single aggregation pipeline update (MongoDB 4.2+) that uses
`$set` with conditional logic based on the items array. This is more
complex but possible.

**Recommendation:** Keep `submit_items_and_complete_batch` as
read-modify-write for now. The concurrent submit scenario is unlikely
(submit is a single user action), and the complexity of an atomic version
outweighs the benefit. Revisit if submit concurrency becomes a
requirement.

## Required Tests for Future Migration

### Concurrency tests (per function or per pattern)

For each migrated function, add a test that simulates concurrent access:

1. **Item-level concurrency:** Two concurrent operations on different items
   in the same batch. Verify both updates persist.
2. **Image-level concurrency:** Concurrent image status update and user
   action (e.g., detection + delete). Verify both persist.
3. **Append concurrency:** Two concurrent appends. Verify both elements
   are present.

### Regression tests (per function)

Each migrated function must preserve its existing behavior:

1. Return value matches (dict, bool, list, or None).
2. Error behavior matches (ValueError for missing batch/item).
3. Idempotency is preserved (e.g., `commit_image_boxes` returns existing
   items if already committed, `mark_item_deleted` returns True if
   already deleted).
4. Conditional logic is preserved (e.g., `reset_item_for_retry` only
   resets items in eligible states).
5. Batch `updatedAt` is set on every write.

### FakeDatabase support

The test `FakeCollection.update_one` currently supports:
- `$set` with scalar fields and dotted paths
- Positional `$` operator (via `_resolve_positional_key`)
- `$inc`

It does **not** support:
- `array_filters` parameter
- `$push` operator
- `find_one_and_update` (though `claim_item` tests work because they test
  through the API, not directly against FakeCollection)

A future migration would need to either:
1. Extend `FakeCollection` to support these operations, or
2. Add integration tests that use a real MongoDB test container.

## Phased Recommendation

### Phase 1: High-risk item-level updates (functions 9-14)

**Scope:** `save_item_extraction_success`, `save_item_extraction_failure`,
`reset_item_for_retry`, `update_item_draft`, `mark_item_deleted`,
`undo_item_deletion`.

**Approach:** Migrate to Pattern A (positional `$` with `find_one_and_update`).

**Rationale:** These functions have the highest lost-update risk because
the extraction worker and user front-end operate concurrently on different
items in the same batch. The positional `$` pattern is already proven by
`claim_item`.

**Estimated effort:** 6 functions, each a mechanical conversion. Tests
already cover the behavior; add concurrency tests.

### Phase 2: Array appends (functions 1, 2, 8)

**Scope:** `add_source_image`, `add_items_for_image`,
`commit_image_boxes`.

**Approach:** Migrate to Pattern B (`$push` + `$set`).

**Rationale:** Lower risk than Phase 1 but still a lost-update window.
`$push` is simpler and more correct than read-modify-write for appends.

**Considerations:** `commit_image_boxes` has idempotency and order
calculation logic that requires a read before the write. The write itself
can still be atomic.

**Estimated effort:** 3 functions. May require FakeDatabase `$push`
support.

### Phase 3: Image-level detection updates (functions 3-6)

**Scope:** `start_image_detection`, `save_image_detection_success`,
`save_image_detection_failure`, `save_image_boxes_and_subject`.

**Approach:** Migrate to Pattern A (positional `$`).

**Rationale:** Low risk (detection is typically not concurrent with other
image operations), but the conversion is straightforward and consistent
with Phase 1.

**Estimated effort:** 4 functions, mechanical conversion.

### Phase 4: Complex multi-field updates (functions 7, 15)

**Scope:** `delete_batch_image`, `submit_items_and_complete_batch`.

**Approach:**
- `delete_batch_image`: Pattern C (array filters).
- `submit_items_and_complete_batch`: Keep as read-modify-write or use
  aggregation pipeline update.

**Rationale:** These are more complex and lower priority. Defer until
Phases 1-3 are stable.

**Considerations:** Requires FakeDatabase `array_filters` support for
testing `delete_batch_image`.

### Functions to leave unchanged

- `claim_item` – already atomic.
- `create_batch`, `get_batch`, `get_active_batch_for_user`,
  `find_cleanup_candidates`, `mark_batch_cleaned`,
  `ensure_batch_indexes` – not read-modify-write.

## Conclusion

The ingestion repository has 14 read-modify-write functions that write
entire `images` or `items` arrays back to MongoDB. The highest lost-update
risk is in item-level updates where the extraction worker and user
front-end operate concurrently. `claim_item` already demonstrates the
correct atomic pattern using `find_one_and_update` with the positional `$`
operator.

A phased migration starting with the highest-risk item-level functions
(Phase 1) would provide the most safety improvement with the least
complexity. Each phase should include concurrency tests and preserve all
existing regression tests.
