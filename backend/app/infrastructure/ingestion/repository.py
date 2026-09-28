from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from bson import ObjectId
from pymongo import ASCENDING, ReturnDocument

from app.domain.ingestion import BatchState, ImageState, ItemState
from app.infrastructure.config.settings import Settings
from app.infrastructure.storage.mongo import Document
from app.problem_variation import (
    GenerationInProgressError,
    IngestionMode,
    InvalidVariationStateError,
    RevisionMismatchError,
    VariationNotFoundError,
    VariationStatus,
    VARIATION_IN_FLIGHT,
    VARIANT_INGESTION_MODES,
    has_semantic_change,
)

from .documents import (
    build_batch_document,
    build_image_document,
    build_item_document,
    new_image_id,
    new_item_id,
)

INGESTION_BATCHES_COLLECTION = "ingestion_batches"

BATCH_INDEXES = [
    {
        "keys": [("userId", ASCENDING), ("status", ASCENDING), ("expiresAt", ASCENDING)],
        "name": "batch_user_status_expiry",
    },
    {
        "keys": [("status", ASCENDING), ("expiresAt", ASCENDING)],
        "name": "batch_cleanup",
    },
    {
        "keys": [("items.itemId", ASCENDING)],
        "name": "batch_item_id",
    },
    {
        "keys": [("items.origin.batchId", ASCENDING), ("items.origin.itemId", ASCENDING)],
        "name": "batch_item_origin",
    },
]


def _now() -> datetime:
    return datetime.now(UTC)


def _collection(database: Any) -> Any:
    return database[INGESTION_BATCHES_COLLECTION]


def _object_id(value: str | ObjectId) -> ObjectId:
    return ObjectId(value) if isinstance(value, str) else value


async def _load_batch_for_update(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
) -> Document:
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")
    return batch


def is_batch_expired(batch: Document, *, now: datetime | None = None) -> bool:
    expires_at = batch.get("expiresAt")
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < (now or _now())


def _find_item(batch: Document, item_id: str) -> dict[str, Any] | None:
    for item in batch.get("items", []):
        if item.get("itemId") == item_id:
            return item
    return None


def _classify_variation_conflict(item: dict[str, Any] | None, expected_revision: int) -> None:
    """Raise the precise interactive-write conflict for a failed predicate."""
    if item is None:
        raise VariationNotFoundError("Item not found")
    if item.get("contentRevision") != expected_revision:
        raise RevisionMismatchError(
            f"expectedRevision {expected_revision} does not match item "
            f"contentRevision {item.get('contentRevision')}"
        )
    status = (item.get("variation") or {}).get("status")
    if status in [s.value for s in VARIATION_IN_FLIGHT]:
        raise GenerationInProgressError(
            f"Variation work is already in flight (status {status})"
        )
    if (item.get("variation") or {}).get("submitReservation"):
        raise InvalidVariationStateError("Item is reserved for submission")
    raise InvalidVariationStateError(f"Variation status {status} does not allow this action")


# Interactive variant-mode writes: predicate rejections are classified against
# a fresh read so handlers can return precise 409 codes.
_VARIANT_MODE_PREDICATE = {
    "ingestionMode": {"$in": [mode.value for mode in VARIANT_INGESTION_MODES]}
}

_ITEM_ACTIONABLE_PREDICATE = {
    "status": {
        "$nin": [ItemState.DELETED.value, ItemState.SUBMITTED.value]
    }
}


async def create_batch(
    database: Any,
    user_id: Any,
    settings: Settings,
    *,
    ingestion_mode: IngestionMode = IngestionMode.ORIGINAL,
    now: datetime | None = None,
) -> Document:
    current = now or _now()
    ttl_seconds = settings.bulk_ingestion_batch_ttl_seconds
    batch_id = ObjectId()
    document = build_batch_document(
        batch_id=batch_id,
        user_id=user_id,
        expires_at=current + timedelta(seconds=ttl_seconds),
        now=current,
        ingestion_mode=ingestion_mode.value,
    )
    await _collection(database).insert_one(document)
    return document


async def get_batch(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
) -> Document | None:
    return await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )


async def get_active_batch_for_user(
    database: Any,
    user_id: Any,
    *,
    now: datetime | None = None,
) -> Document | None:
    current = now or _now()
    return await _collection(database).find_one(
        {
            "userId": user_id,
            "status": BatchState.ACTIVE.value,
            "expiresAt": {"$gt": current},
        }
    )


# Bound for optimistic-concurrency retries (guarded read-compute-write
# mutations). Each attempt only lands on the exact document version it read,
# so a lost race re-reads and retries instead of overwriting concurrent work.
# ponytail: fixed bound; raise if a writer starves, which would indicate a bug.
_MAX_OCC_ATTEMPTS = 50


def _revision_guard(batch: Document) -> dict[str, Any]:
    """OCC predicate matching the exact document version that was read.

    ``revision`` is advanced by every writer (``$inc``), so equality on it is
    collision-safe where ``updatedAt`` equality is not: two writers that read
    the same version and write within the same millisecond cannot both match,
    because the first writer's ``$inc`` invalidates the second's filter.
    Batches created before the revision token existed have no field yet.
    """
    if "revision" in batch:
        return {"revision": batch["revision"]}
    return {"revision": {"$exists": False}}


async def add_source_image(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    source_image: dict[str, Any],
    *,
    order: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    image_id = new_image_id()
    image_document = build_image_document(
        image_id=image_id,
        source_image=source_image,
        order=order,
        now=current,
    )

    result = await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {
            "$push": {"images": image_document},
            "$set": {"updatedAt": current},
            "$inc": {"revision": 1},
        },
    )
    if result.matched_count == 0:
        raise ValueError("Batch not found")
    return image_document


async def add_items_for_image(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    image_id: str,
    item_count: int,
    *,
    starting_order: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current = now or _now()
    await _load_batch_for_update(database, batch_id, user_id)

    new_items: list[dict[str, Any]] = []
    for offset in range(item_count):
        item_id = new_item_id()
        item_document = build_item_document(
            item_id=item_id,
            batch_id=_object_id(batch_id),
            image_id=image_id,
            order=starting_order + offset,
            now=current,
        )
        new_items.append(item_document)

    # Atomic append + image commit: the whole invariant lands in one
    # conditional update, so a concurrent commit cannot duplicate items.
    # Terminal image states (committed/deleted) are excluded so a commit can
    # never resurrect a deleted image's items.
    result = await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "images": {
                "$elemMatch": {
                    "imageId": image_id,
                    "status": {
                        "$nin": [
                            ImageState.COMMITTED.value,
                            ImageState.DELETED.value,
                        ]
                    },
                }
            },
        },
        {
            "$push": {"items": {"$each": new_items}},
            "$set": {
                "images.$.status": ImageState.COMMITTED.value,
                "images.$.committedAt": current,
                "images.$.updatedAt": current,
                "updatedAt": current,
            },
            "$inc": {"revision": 1},
        },
    )
    if result.matched_count == 0:
        raise ValueError("Image not found")
    return new_items


async def start_image_detection(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    image_id: str,
    *,
    now: datetime,
) -> None:
    await _load_batch_for_update(database, batch_id, user_id)

    # Only images whose current state legally transitions to DETECTING; a
    # deleted/committed image can never be re-entered by a late detection
    # start, so a lost race is a silent no-op.
    await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "images": {
                "$elemMatch": {
                    "imageId": image_id,
                    "status": {
                        "$in": [
                            ImageState.UPLOADED.value,
                            ImageState.DETECT_FAILED.value,
                        ]
                    },
                }
            },
        },
        {
            "$set": {
                "images.$.status": ImageState.DETECTING.value,
                "images.$.updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {"revision": 1},
        },
    )


async def save_image_detection_success(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    image_id: str,
    *,
    subject: str,
    boxes: list[dict[str, Any]],
    model: str,
    raw_provider_response: dict[str, Any] | None,
    now: datetime,
) -> None:
    await _load_batch_for_update(database, batch_id, user_id)

    # The result may only land on the image the detection request started
    # from; if delete (or a commit) won the race, the write matches nothing
    # and the stale result is discarded instead of resurrecting the image.
    await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "images": {
                "$elemMatch": {
                    "imageId": image_id,
                    "status": ImageState.DETECTING.value,
                }
            },
        },
        {
            "$set": {
                "images.$.status": ImageState.READY.value,
                "images.$.subject": subject,
                "images.$.boxes": list(boxes),
                "images.$.detection": {
                    "model": model,
                    "rawProviderResponse": raw_provider_response,
                    "failureCode": None,
                    "failureMessage": None,
                },
                "images.$.updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {"revision": 1},
        },
    )


async def save_image_detection_failure(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    image_id: str,
    *,
    failure_code: str,
    failure_message: str,
    now: datetime,
) -> None:
    await _load_batch_for_update(database, batch_id, user_id)

    # Same ownership rule as the success write: only the in-flight DETECTING
    # image can be marked failed.
    await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "images": {
                "$elemMatch": {
                    "imageId": image_id,
                    "status": ImageState.DETECTING.value,
                }
            },
        },
        {
            "$set": {
                "images.$.status": ImageState.DETECT_FAILED.value,
                "images.$.detection": {
                    "model": None,
                    "rawProviderResponse": None,
                    "failureCode": failure_code,
                    "failureMessage": failure_message,
                },
                "images.$.updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {"revision": 1},
        },
    )


async def save_image_boxes_and_subject(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    image_id: str,
    *,
    subject: str | None,
    boxes: list[dict[str, Any]],
    now: datetime,
) -> None:
    await _load_batch_for_update(database, batch_id, user_id)

    set_fields: dict[str, Any] = {
        "images.$.status": ImageState.READY.value,
        "images.$.boxes": list(boxes),
        "images.$.updatedAt": now,
        "updatedAt": now,
    }
    if subject is not None:
        set_fields["images.$.subject"] = subject

    # Box edits are legal from any non-terminal state (the API blocks only
    # committed/deleted images); the database predicate enforces the same
    # boundary so a delete that wins the race cannot be overwritten.
    await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "images": {
                "$elemMatch": {
                    "imageId": image_id,
                    "status": {
                        "$nin": [
                            ImageState.COMMITTED.value,
                            ImageState.DELETED.value,
                        ]
                    },
                }
            },
        },
        {"$set": set_fields, "$inc": {"revision": 1}},
    )


async def delete_batch_image(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    image_id: str,
    *,
    now: datetime,
) -> None:
    await _load_batch_for_update(database, batch_id, user_id)

    # Image + all of its items in one atomic update (array-filtered items).
    await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "images": {"$elemMatch": {"imageId": image_id}},
        },
        {
            "$set": {
                "images.$.status": ImageState.DELETED.value,
                "images.$.updatedAt": now,
                "items.$[item].status": ItemState.DELETED.value,
                "items.$[item].updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {"revision": 1},
        },
        array_filters=[{"item.imageId": image_id}],
    )


async def commit_image_boxes(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    image_id: str,
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    collection = _collection(database)
    for _ in range(_MAX_OCC_ATTEMPTS):
        batch = await _load_batch_for_update(database, batch_id, user_id)

        target_image = None
        for image in batch.get("images", []):
            if image.get("imageId") == image_id:
                target_image = image
                break
        if target_image is None:
            raise ValueError("Image not found")

        if target_image["status"] == ImageState.COMMITTED.value:
            return [
                item for item in batch.get("items", [])
                if item.get("imageId") == image_id and item.get("status") != ItemState.DELETED.value
            ]
        if target_image["status"] != ImageState.READY.value:
            # The API only commits ready images; reject deleted (or otherwise
            # not-yet-ready) images instead of resurrecting deleted content.
            raise ValueError("Image not found")

        existing_items = [
            item for item in batch.get("items", [])
            if item.get("status") != ItemState.DELETED.value
        ]
        next_order = max((item["order"] for item in existing_items), default=-1) + 1

        new_items: list[dict[str, Any]] = []
        for offset, box in enumerate(target_image.get("boxes", [])):
            item_id = new_item_id()
            item_document = build_item_document(
                item_id=item_id,
                batch_id=batch["_id"],
                image_id=image_id,
                order=next_order + offset,
                now=now,
                box=box,
            )
            new_items.append(item_document)

        # Guarded append: the update only lands on the exact document version
        # that was read (revision equality), so two concurrent commits cannot
        # duplicate items or orders; the loser re-reads and retries. Requiring
        # the READY pre-commit state in the database predicate means a delete
        # that won the race can never be overwritten back to committed.
        result = await collection.update_one(
            {
                "_id": _object_id(batch_id),
                "userId": user_id,
                **_revision_guard(batch),
                "images": {
                    "$elemMatch": {
                        "imageId": image_id,
                        "status": ImageState.READY.value,
                    }
                },
            },
            {
                "$push": {"items": {"$each": new_items}},
                "$set": {
                    "images.$.status": ImageState.COMMITTED.value,
                    "images.$.committedAt": now,
                    "images.$.updatedAt": now,
                    "updatedAt": now,
                },
                "$inc": {"revision": 1},
            },
        )
        if result.matched_count == 1:
            return new_items
    raise RuntimeError("commit_image_boxes lost too many concurrent races")


async def claim_item(
    database: Any,
    batch_id: str | ObjectId,
    item_id: str,
    user_id: Any,
    *,
    lease_timeout_seconds: int,
    now: datetime,
) -> dict[str, Any] | None:
    """Atomically claim an item for extraction. Returns the updated item or None."""
    lease_until = now + timedelta(seconds=lease_timeout_seconds)
    result = await _collection(database).find_one_and_update(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
                    "status": {"$in": [ItemState.QUEUED.value, ItemState.EXTRACTING.value]},
                    # Items with variant workflow state are never re-extracted:
                    # a confirmed variant source must never be overwritten by
                    # a new extraction result (missing variation matches null).
                    "variation.status": {
                        "$in": [None, VariationStatus.NOT_REQUESTED.value]
                    },
                    "$or": [
                        {"leaseUntil": None},
                        {"leaseUntil": {"$lte": now}},
                    ],
                }
            },
        },
        {
            "$set": {
                "items.$.status": ItemState.EXTRACTING.value,
                "items.$.leaseUntil": lease_until,
                "items.$.updatedAt": now,
                "items.$.extraction.requestStartedAt": now,
                "updatedAt": now,
            },
            "$inc": {"items.$.retryCount": 1, "revision": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    for item in result.get("items", []):
        if item.get("itemId") == item_id:
            return item
    return None


async def save_item_extraction_success(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    crop: dict[str, Any],
    draft: dict[str, Any],
    extraction: dict[str, Any],
    lease_until: datetime | None,
    now: datetime,
) -> bool:
    """Persist an extraction result only if the caller still owns the lease.

    Returns True when the write landed. A False return means the item was
    deleted, already completed by a newer owner, or re-claimed with a new
    lease; the caller must discard its stale result.
    """
    await _load_batch_for_update(database, batch_id, user_id)

    result = await _collection(database).find_one_and_update(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
                    "status": ItemState.EXTRACTING.value,
                    # Same variant-source protection as the claim: a generate
                    # confirmed between claim and completion wins; the stale
                    # extraction result is discarded instead of overwriting
                    # the confirmed source.
                    "variation.status": {
                        "$in": [None, VariationStatus.NOT_REQUESTED.value]
                    },
                    "leaseUntil": lease_until,
                }
            },
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
            "$inc": {"revision": 1},
        },
    )
    return result is not None


async def save_item_extraction_failure(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    extraction: dict[str, Any],
    lease_until: datetime | None,
    now: datetime,
) -> bool:
    """Persist an extraction failure only if the caller still owns the lease."""
    await _load_batch_for_update(database, batch_id, user_id)

    result = await _collection(database).find_one_and_update(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
                    "status": ItemState.EXTRACTING.value,
                    "leaseUntil": lease_until,
                }
            },
        },
        {
            "$set": {
                "items.$.status": ItemState.FAILED.value,
                "items.$.extraction": extraction,
                "items.$.leaseUntil": None,
                "items.$.updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {"revision": 1},
        },
    )
    return result is not None


async def reset_item_for_retry(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    now: datetime,
) -> bool:
    collection = _collection(database)
    batch = await _load_batch_for_update(database, batch_id, user_id)
    item = _find_item(batch, item_id)
    if item is None:
        return False

    # A confirmed variant source is never re-extracted: a submit-failed
    # variant item returns straight to ready so the submit can be retried
    # without clobbering the confirmed draft/variation data.
    variation = item.get("variation") or {}
    if variation.get("original"):
        result = await collection.find_one_and_update(
            {
                "_id": _object_id(batch_id),
                "userId": user_id,
                "items": {
                    "$elemMatch": {
                        "itemId": item_id,
                        "status": ItemState.SUBMIT_FAILED.value,
                    }
                },
            },
            {
                "$set": {
                    "items.$.status": ItemState.READY.value,
                    "items.$.leaseUntil": None,
                    "items.$.updatedAt": now,
                    "updatedAt": now,
                },
                "$inc": {"revision": 1},
            },
        )
        return result is not None
    return await _reset_item_for_retry_unclaimed(
        collection, batch_id, user_id, item, now=now
    )


async def _reset_item_for_retry_unclaimed(
    collection: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item: dict[str, Any],
    *,
    now: datetime,
) -> bool:
    result = await collection.find_one_and_update(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "items": {
                "$elemMatch": {
                    "itemId": item["itemId"],
                    "$or": [
                        {"status": ItemState.FAILED.value},
                        {"status": ItemState.SUBMIT_FAILED.value},
                        {"status": ItemState.EXTRACTING.value, "leaseUntil": {"$lte": now}},
                    ],
                }
            },
        },
        {
            "$set": {
                "items.$.status": ItemState.QUEUED.value,
                "items.$.leaseUntil": None,
                "items.$.updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {"revision": 1},
        },
    )
    return result is not None


async def update_item_draft(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    draft_update: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    await _load_batch_for_update(database, batch_id, user_id)

    set_fields: dict[str, Any] = {
        "items.$.updatedAt": now,
        "updatedAt": now,
    }
    allowed = {"text", "problemType", "graphDsl", "correctAnswer", "tags", "subject"}
    for key in allowed:
        if key in draft_update:
            set_fields[f"items.$.draft.{key}"] = draft_update[key]

    result = await _collection(database).find_one_and_update(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "items": {"$elemMatch": {"itemId": item_id}},
        },
        {"$set": set_fields, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    for item in result.get("items", []):
        if item.get("itemId") == item_id:
            return item
    return None


async def mark_item_deleted(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    now: datetime,
) -> bool:
    await _load_batch_for_update(database, batch_id, user_id)

    # previousStatus must record the observed status, so each candidate state
    # gets its own conditional attempt; the first match wins atomically.
    # ponytail: enumerated states; extend here when ItemState gains members.
    for status in (
        ItemState.QUEUED.value,
        ItemState.EXTRACTING.value,
        ItemState.READY.value,
        ItemState.FAILED.value,
        ItemState.SUBMIT_FAILED.value,
    ):
        result = await _collection(database).find_one_and_update(
            {
                "_id": _object_id(batch_id),
                "userId": user_id,
                "items": {"$elemMatch": {"itemId": item_id, "status": status}},
            },
            {
                "$set": {
                    "items.$.previousStatus": status,
                    "items.$.status": ItemState.DELETED.value,
                    "items.$.deletedAt": now,
                    "items.$.updatedAt": now,
                    "updatedAt": now,
                },
                "$inc": {"revision": 1},
            },
        )
        if result is not None:
            return True

    final = await _load_batch_for_update(database, batch_id, user_id)
    item = next(
        (i for i in final.get("items", []) if i.get("itemId") == item_id),
        None,
    )
    if item is not None and item.get("status") in {
        ItemState.DELETED.value,
        ItemState.SUBMITTED.value,
    }:
        return True
    return False


async def undo_item_deletion(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    now: datetime,
) -> bool:
    batch = await _load_batch_for_update(database, batch_id, user_id)

    item = next(
        (i for i in batch.get("items", []) if i.get("itemId") == item_id),
        None,
    )
    if item is None or item.get("status") != ItemState.DELETED.value:
        return False
    previous_status = item.get("previousStatus")
    if previous_status is None:
        return False

    # Undo restores still-current variation data and requeues canceled
    # in-flight work. The claim token is always cleared, so a pre-delete
    # worker claim can never land its result on the restored item.
    variation = dict(item.get("variation") or {})
    set_fields: dict[str, Any] = {
        "items.$.status": previous_status,
        "items.$.updatedAt": now,
        "updatedAt": now,
    }
    if variation.get("status") in [
        VariationStatus.QUEUED.value,
        VariationStatus.GENERATING.value,
        VariationStatus.VALIDATING.value,
    ]:
        variation["status"] = VariationStatus.QUEUED.value
        variation["claimToken"] = None
        variation["leaseUntil"] = None
        set_fields["items.$.variation"] = variation

    result = await _collection(database).find_one_and_update(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
                    "status": ItemState.DELETED.value,
                    "previousStatus": previous_status,
                }
            },
        },
        {
            "$set": set_fields,
            "$unset": {
                "items.$.previousStatus": "",
                "items.$.deletedAt": "",
            },
            "$inc": {"revision": 1},
        },
    )
    return result is not None


# How long an original-submit reservation blocks Generate. ponytail: fixed
# window; expiry reclaim keeps a crashed submit request from blocking
# Generate forever, at the cost of a possible duplicate save if problem
# creation stalls past it.
_SUBMIT_RESERVATION_TIMEOUT = timedelta(minutes=10)


async def reserve_items_for_original_submit(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_ids: list[str],
    *,
    now: datetime,
) -> tuple[str, list[str]]:
    """Atomically reserve still-eligible original-submit items.

    Closes the Generate-vs-submit side-effect race: only items that are
    still ready, non-deleted and variant-free get a reservation, Generate
    refuses reserved items, and the submit completion only lands on items
    holding this token. Reservations expire (crashed submit request) and
    are then reclaimable.
    """
    token = str(uuid4())
    reservation = {"token": token, "expiresAt": now + _SUBMIT_RESERVATION_TIMEOUT}
    reserved: list[str] = []
    for item_id in item_ids:
        result = await _collection(database).update_one(
            {
                "_id": _object_id(batch_id),
                "userId": user_id,
                "status": BatchState.ACTIVE.value,
                "items": {
                    "$elemMatch": {
                        "itemId": item_id,
                        "status": ItemState.READY.value,
                        "variation.original": {"$in": [None]},
                        "variation.status": {
                            "$in": [None, VariationStatus.NOT_REQUESTED.value]
                        },
                        "$or": [
                            {"variation.submitReservation": {"$in": [None]}},
                            {"variation.submitReservation.expiresAt": {"$lte": now}},
                        ],
                    }
                },
            },
            {
                "$set": {
                    "items.$.variation.submitReservation": reservation,
                    "items.$.updatedAt": now,
                    "updatedAt": now,
                },
                "$inc": {"revision": 1},
            },
        )
        if result.matched_count == 1:
            reserved.append(item_id)
    return token, reserved


async def submit_items_and_complete_batch(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    *,
    item_results: list[dict[str, Any]],
    reservation_token: str,
    now: datetime,
) -> Document | None:
    """Persist per-item submit outcomes and mark the batch completed if appropriate.

    Results only land on items this submit reserved (matching token, still
    non-variant): a variant confirmed after the reservation expired can
    never be recorded as an original submission. Reserved items without a
    result keep their status and just drop the reservation.

    The batch is marked ``completed`` when every non-deleted item has status
    ``submitted``. Items in other states (including ``submit-failed``) keep the
    batch active so they can be retried or deleted.
    """
    collection = _collection(database)
    result_by_item = {result["itemId"]: result for result in item_results}

    for _ in range(_MAX_OCC_ATTEMPTS):
        batch = await _load_batch_for_update(database, batch_id, user_id)

        items = [dict(item) for item in batch.get("items", [])]
        for item in items:
            variation = item.get("variation") or {}
            reservation = variation.get("submitReservation")
            if not reservation or reservation.get("token") != reservation_token:
                continue
            # This submit owns the reservation: release it either way.
            if isinstance(item.get("variation"), dict):
                item["variation"]["submitReservation"] = None
            result = result_by_item.get(item.get("itemId"))
            if result is None:
                # Reserved but never processed (crash window): keep status.
                continue
            if item.get("status") == ItemState.DELETED.value:
                # Deletion won the race (possibly between this OCC retry's
                # read and the first, rejected attempt): a stale submit
                # result must not resurrect the deleted item.
                continue
            if variation.get("original") or variation.get("status") not in (
                None,
                "not-requested",
            ):
                # A variant was confirmed after the reservation expired:
                # an original submission can never be recorded for it.
                continue
            item["status"] = result["status"]
            item["submit"] = result["submit"]
            item["updatedAt"] = now

        all_submitted = True
        has_submitted = False
        for item in items:
            status = item.get("status")
            if status == ItemState.DELETED.value:
                continue
            if status == ItemState.SUBMITTED.value:
                has_submitted = True
            else:
                all_submitted = False

        update: dict[str, Any] = {"items": items, "updatedAt": now}
        if all_submitted and has_submitted:
            update["status"] = BatchState.COMPLETED.value

        # Guarded write: outcomes + completion only land on the exact version
        # that was read (revision equality), so concurrent item work is never
        # overwritten — even when both writes carry the same millisecond.
        result = await collection.update_one(
            {
                "_id": _object_id(batch_id),
                "userId": user_id,
                **_revision_guard(batch),
            },
            {"$set": update, "$inc": {"revision": 1}},
        )
        if result.matched_count == 1:
            return await collection.find_one(
                {"_id": _object_id(batch_id), "userId": user_id}
            )
    raise RuntimeError("submit_items_and_complete_batch lost too many concurrent races")


# ---------------------------------------------------------------------------
# Variant workflow writers (issue #613). All interactive writes classify a
# rejected predicate against a fresh read; all background writes return bool
# and are fenced by claim token + content revision + expiry + deletion.
# ---------------------------------------------------------------------------


async def request_variation_generation(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    original: dict[str, Any],
    expected_revision: int,
    now: datetime,
) -> None:
    """Confirm the submitted draft, snapshot it, and queue variant generation.

    One atomic write: saves/confirm the reviewed source, snapshots its
    semantic fields, clears any prior candidate/approval, bumps
    ``contentRevision`` and ``generationCount``, and enters ``queued``.
    Raises the domain conflict errors for stale revision / in-flight work.
    """
    await _load_batch_for_update(database, batch_id, user_id)
    result = await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            **_VARIANT_MODE_PREDICATE,
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
                    "contentRevision": expected_revision,
                    "variation.status": {
                        "$nin": [status.value for status in VARIATION_IN_FLIGHT]
                    },
                    # A live submit reservation wins the Generate-vs-submit
                    # race: never confirm a variant into an in-flight submit.
                    "$or": [
                        {"variation.submitReservation": {"$in": [None]}},
                        {"variation.submitReservation.expiresAt": {"$lte": now}},
                    ],
                    **_ITEM_ACTIONABLE_PREDICATE,
                }
            },
        },
        {
            "$set": {
                "items.$.draft.text": original["text"],
                "items.$.draft.problemType": original["problemType"],
                "items.$.draft.graphDsl": original["graphDsl"],
                "items.$.draft.correctAnswer": original["correctAnswer"],
                "items.$.draft.subject": original["subject"],
                "items.$.variation.original": original,
                "items.$.variation.candidate": None,
                "items.$.variation.validation": None,
                "items.$.variation.validatedRevision": None,
                "items.$.variation.status": VariationStatus.QUEUED.value,
                "items.$.variation.queuedAt": now,
                "items.$.variation.claimToken": None,
                "items.$.variation.leaseUntil": None,
                # Generate won over an expired reservation: clear it so the
                # stale submit completion can never record a submission.
                "items.$.variation.submitReservation": None,
                "items.$.updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {
                "items.$.contentRevision": 1,
                "items.$.variation.generationCount": 1,
                "revision": 1,
            },
        },
    )
    if result.matched_count == 0:
        batch = await _load_batch_for_update(database, batch_id, user_id)
        _classify_variation_conflict(_find_item(batch, item_id), expected_revision)


async def update_item_draft_variant(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    draft_update: dict[str, Any],
    expected_revision: int,
    now: datetime,
) -> None:
    """Variant-mode draft edit with semantic invalidation.

    A semantic source change atomically invalidates the variation (cancels
    in-flight work, clears candidate and current approval) and bumps
    ``contentRevision``; a no-op/full-form save or tag-only edit does not.
    """
    collection = _collection(database)
    allowed = ("text", "problemType", "graphDsl", "correctAnswer", "tags", "subject")
    for _ in range(_MAX_OCC_ATTEMPTS):
        batch = await _load_batch_for_update(database, batch_id, user_id)
        item = _find_item(batch, item_id)
        if item is None:
            raise VariationNotFoundError("Item not found")
        if item.get("contentRevision") != expected_revision:
            raise RevisionMismatchError(
                f"expectedRevision {expected_revision} does not match item "
                f"contentRevision {item.get('contentRevision')}"
            )

        draft = dict(item.get("draft") or {})
        merged_draft = {**draft}
        for key in allowed:
            if key in draft_update:
                merged_draft[key] = draft_update[key]

        semantic_changed = has_semantic_change(draft, merged_draft)

        set_fields: dict[str, Any] = {
            "items.$.updatedAt": now,
            "updatedAt": now,
        }
        for key in allowed:
            if key in draft_update:
                set_fields[f"items.$.draft.{key}"] = draft_update[key]
        update: dict[str, Any] = {"$set": set_fields, "$inc": {"revision": 1}}
        if semantic_changed:
            update["$set"].update(
                {
                    # Cancel any attempt and discard the confirmed snapshot,
                    # candidate and approval; the old claim can never land
                    # after this revision bump.
                    "items.$.variation.status": VariationStatus.NOT_REQUESTED.value,
                    "items.$.variation.original": None,
                    "items.$.variation.candidate": None,
                    "items.$.variation.validation": None,
                    "items.$.variation.validatedRevision": None,
                    "items.$.variation.claimToken": None,
                    "items.$.variation.leaseUntil": None,
                }
            )
            update["$inc"]["items.$.contentRevision"] = 1

        result = await collection.update_one(
            {
                "_id": _object_id(batch_id),
                "userId": user_id,
                **_revision_guard(batch),
                "items": {
                    "$elemMatch": {
                        "itemId": item_id,
                        "contentRevision": expected_revision,
                        **_ITEM_ACTIONABLE_PREDICATE,
                    }
                },
            },
            update,
        )
        if result.matched_count == 1:
            return
    raise RuntimeError("update_item_draft_variant lost too many concurrent races")


async def edit_variation_candidate(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    candidate_update: dict[str, Any],
    tags: list[str] | None,
    expected_revision: int,
    now: datetime,
) -> None:
    """Edit a ready/needs-validation candidate.

    A semantic candidate change enters ``needs-validation`` and clears the
    current approval; tags alone never invalidate (they live in the shared
    ``item.draft.tags``). A mismatched candidate type is preserved on purpose
    so validation can FAIL with evidence.
    """
    collection = _collection(database)
    allowed = ("text", "problemType", "graphDsl", "correctAnswer")
    for _ in range(_MAX_OCC_ATTEMPTS):
        batch = await _load_batch_for_update(database, batch_id, user_id)
        item = _find_item(batch, item_id)
        if item is None:
            raise VariationNotFoundError("Item not found")

        variation = dict(item.get("variation") or {})
        merged_candidate = {**(variation.get("candidate") or {}), **candidate_update}
        semantic_changed = has_semantic_change(
            variation.get("candidate"), merged_candidate
        )

        set_fields: dict[str, Any] = {
            "items.$.variation.candidate": merged_candidate,
            "items.$.updatedAt": now,
            "updatedAt": now,
        }
        update: dict[str, Any] = {"$set": set_fields, "$inc": {"revision": 1}}
        if semantic_changed:
            update["$set"].update(
                {
                    "items.$.variation.status": VariationStatus.NEEDS_VALIDATION.value,
                    "items.$.variation.validation": None,
                    "items.$.variation.validatedRevision": None,
                }
            )
            update["$inc"]["items.$.contentRevision"] = 1
        if tags is not None:
            update["$set"]["items.$.draft.tags"] = tags

        result = await collection.update_one(
            {
                "_id": _object_id(batch_id),
                "userId": user_id,
                **_revision_guard(batch),
                "items": {
                    "$elemMatch": {
                        "itemId": item_id,
                        "contentRevision": expected_revision,
                        "variation.status": {
                            "$in": [
                                VariationStatus.READY.value,
                                VariationStatus.NEEDS_VALIDATION.value,
                            ]
                        },
                        **_ITEM_ACTIONABLE_PREDICATE,
                    }
                },
            },
            update,
        )
        if result.matched_count == 1:
            return
        fresh = _find_item(await _load_batch_for_update(database, batch_id, user_id), item_id)
        _classify_variation_conflict(fresh, expected_revision)
    raise RuntimeError("edit_variation_candidate lost too many concurrent races")


async def request_variation_revalidation(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    expected_revision: int,
    now: datetime,
) -> None:
    """Queue validator-only work for a needs-validation candidate.

    Keeps source and candidate, clears the current approval, and does not
    touch ``generationCount`` or ``contentRevision`` (claim-token fencing
    separates consecutive validator-only runs).
    """
    await _load_batch_for_update(database, batch_id, user_id)
    result = await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            **_VARIANT_MODE_PREDICATE,
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
                    "contentRevision": expected_revision,
                    "variation.status": VariationStatus.NEEDS_VALIDATION.value,
                    **_ITEM_ACTIONABLE_PREDICATE,
                }
            },
        },
        {
            "$set": {
                "items.$.variation.status": VariationStatus.QUEUED.value,
                "items.$.variation.validation": None,
                "items.$.variation.validatedRevision": None,
                "items.$.variation.queuedAt": now,
                "items.$.variation.claimToken": None,
                "items.$.variation.leaseUntil": None,
                "items.$.updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {"revision": 1},
        },
    )
    if result.matched_count == 0:
        batch = await _load_batch_for_update(database, batch_id, user_id)
        _classify_variation_conflict(_find_item(batch, item_id), expected_revision)


async def claim_variation_work(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    lease_timeout_seconds: int,
    now: datetime,
) -> dict[str, Any] | None:
    """Atomically claim one item's variation work.

    Targets ``generating`` when no candidate is persisted (new generation or
    crash before checkpoint) and ``validating`` when the candidate checkpoint
    exists (revalidate, or crash recovery resuming validation). Claims check
    expiry, deletion and the item's current content revision; returns the
    claimed item (with the new claim token) or None.
    """
    collection = _collection(database)
    batch = await _load_batch_for_update(database, batch_id, user_id)
    item = _find_item(batch, item_id)
    if item is None:
        return None
    variation = item.get("variation") or {}
    if variation.get("status") == VariationStatus.NOT_REQUESTED.value:
        return None
    if variation.get("status") in [
        VariationStatus.READY.value,
        VariationStatus.FAILED.value,
        VariationStatus.NEEDS_VALIDATION.value,
    ]:
        return None
    has_candidate = variation.get("candidate") is not None
    target = (
        VariationStatus.VALIDATING.value if has_candidate
        else VariationStatus.GENERATING.value
    )
    token = str(uuid4())
    lease_until = now + timedelta(seconds=lease_timeout_seconds)
    claimed = await collection.find_one_and_update(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "status": BatchState.ACTIVE.value,
            "expiresAt": {"$gt": now},
            **_VARIANT_MODE_PREDICATE,
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
                    "contentRevision": item.get("contentRevision"),
                    **_ITEM_ACTIONABLE_PREDICATE,
                    "variation.status": {
                        "$in": [
                            VariationStatus.QUEUED.value,
                            VariationStatus.GENERATING.value,
                            VariationStatus.VALIDATING.value,
                        ]
                    },
                    "$or": [
                        {"variation.status": VariationStatus.QUEUED.value},
                        {"variation.leaseUntil": {"$lte": now}},
                    ],
                    "variation.candidate": (
                        {"$ne": None} if has_candidate else {"$in": [None]}
                    ),
                }
            },
        },
        {
            "$set": {
                "items.$.variation.status": target,
                "items.$.variation.claimToken": token,
                "items.$.variation.leaseUntil": lease_until,
                "items.$.updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {"revision": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        return None
    for updated_item in claimed.get("items", []):
        if updated_item.get("itemId") == item_id:
            updated_item["variation"]["claimToken"] = token
            return updated_item
    return None


async def renew_variation_lease(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    token: str,
    lease_timeout_seconds: int,
    now: datetime,
) -> bool:
    """Extend the lease of an in-flight claim the caller still owns."""
    result = await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "status": BatchState.ACTIVE.value,
            "expiresAt": {"$gt": now},
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
                    "variation.claimToken": token,
                    "variation.status": {
                        "$in": [
                            VariationStatus.GENERATING.value,
                            VariationStatus.VALIDATING.value,
                        ]
                    },
                    **_ITEM_ACTIONABLE_PREDICATE,
                }
            },
        },
        {
            "$set": {
                "items.$.variation.leaseUntil": now
                + timedelta(seconds=lease_timeout_seconds),
                "items.$.updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {"revision": 1},
        },
    )
    return result.matched_count == 1


async def save_variation_candidate_checkpoint(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    token: str,
    claimed_revision: int,
    candidate: dict[str, Any],
    now: datetime,
) -> bool:
    """Persist the generated candidate and move the claim to ``validating``.

    Snapshotting before any validator call means a crash or reclaim resumes
    validation from the persisted candidate instead of regenerating.
    """
    result = await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "status": BatchState.ACTIVE.value,
            "expiresAt": {"$gt": now},
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
                    "variation.claimToken": token,
                    "variation.status": VariationStatus.GENERATING.value,
                    "contentRevision": claimed_revision,
                    **_ITEM_ACTIONABLE_PREDICATE,
                }
            },
        },
        {
            "$set": {
                "items.$.variation.candidate": candidate,
                "items.$.variation.status": VariationStatus.VALIDATING.value,
                "items.$.updatedAt": now,
                "updatedAt": now,
            },
            "$inc": {"revision": 1},
        },
    )
    return result.matched_count == 1


async def save_variation_result(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    token: str,
    claimed_revision: int,
    verdict: str,
    validation: dict[str, Any],
    now: datetime,
) -> bool:
    """Land a validation verdict only for the current claim.

    A stale token, a superseded revision (semantic edit invalidated the
    attempt), an expired batch, or a deleted/submitted item all reject the
    write, so no old result can become ready.
    """
    if verdict not in ("pass", "fail"):
        raise ValueError(f"Invalid variation verdict: {verdict}")
    set_fields: dict[str, Any] = {
        "items.$.variation.status": (
            VariationStatus.READY.value if verdict == "pass"
            else VariationStatus.FAILED.value
        ),
        "items.$.variation.validation": validation,
        "items.$.variation.claimToken": None,
        "items.$.variation.leaseUntil": None,
        "items.$.updatedAt": now,
        "updatedAt": now,
    }
    if verdict == "pass":
        set_fields["items.$.variation.validatedRevision"] = claimed_revision
    result = await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "status": BatchState.ACTIVE.value,
            "expiresAt": {"$gt": now},
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
                    "variation.claimToken": token,
                    "variation.status": {
                        "$in": [
                            VariationStatus.GENERATING.value,
                            VariationStatus.VALIDATING.value,
                        ]
                    },
                    "contentRevision": claimed_revision,
                    **_ITEM_ACTIONABLE_PREDICATE,
                }
            },
        },
        {"$set": set_fields, "$inc": {"revision": 1}},
    )
    return result.matched_count == 1


async def find_cleanup_candidates(
    database: Any,
    *,
    now: datetime | None = None,
) -> list[Document]:
    current = now or _now()
    query = {
        "$or": [
            {"status": BatchState.EXPIRED.value},
            {"status": BatchState.COMPLETED.value},
            {
                "status": BatchState.ACTIVE.value,
                "expiresAt": {"$lte": current},
            },
        ]
    }
    cursor = _collection(database).find(query)
    return await cursor.to_list(length=None)


async def mark_batch_cleaned(
    database: Any,
    batch_id: str | ObjectId,
    *,
    now: datetime | None = None,
) -> None:
    current = now or _now()
    await _collection(database).update_one(
        {"_id": _object_id(batch_id)},
        {
            "$set": {"status": BatchState.DELETED.value, "updatedAt": current},
            "$inc": {"revision": 1},
        },
    )


async def ensure_batch_indexes(database: Any) -> None:
    collection = _collection(database)
    create_index = getattr(collection, "create_index", None)
    if not callable(create_index):
        return
    for index in BATCH_INDEXES:
        await create_index(index["keys"], name=index["name"])
