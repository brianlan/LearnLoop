from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, ReturnDocument

from app.domain.ingestion import BatchState, ImageState, ItemState
from app.infrastructure.config.settings import Settings
from app.infrastructure.storage.mongo import Document

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


async def create_batch(
    database: Any,
    user_id: Any,
    settings: Settings,
    *,
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

    await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "images": {"$elemMatch": {"imageId": image_id}},
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

    await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "images": {"$elemMatch": {"imageId": image_id}},
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

    await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "images": {"$elemMatch": {"imageId": image_id}},
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

    await _collection(database).update_one(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "images": {"$elemMatch": {"imageId": image_id}},
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
    await _load_batch_for_update(database, batch_id, user_id)

    result = await _collection(database).find_one_and_update(
        {
            "_id": _object_id(batch_id),
            "userId": user_id,
            "items": {
                "$elemMatch": {
                    "itemId": item_id,
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
            "$set": {
                "items.$.status": previous_status,
                "items.$.updatedAt": now,
                "updatedAt": now,
            },
            "$unset": {
                "items.$.previousStatus": "",
                "items.$.deletedAt": "",
            },
            "$inc": {"revision": 1},
        },
    )
    return result is not None


async def submit_items_and_complete_batch(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    *,
    item_results: list[dict[str, Any]],
    now: datetime,
) -> Document | None:
    """Persist per-item submit outcomes and mark the batch completed if appropriate.

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
            result = result_by_item.get(item.get("itemId"))
            if result is None:
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
