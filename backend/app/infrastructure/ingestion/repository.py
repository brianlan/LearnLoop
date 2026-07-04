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


def is_batch_expired(batch: Document, *, now: datetime | None = None) -> bool:
    expires_at = batch.get("expiresAt")
    if not isinstance(expires_at, datetime):
        return False
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

    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    images = list(batch.get("images", []))
    images.append(image_document)
    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"images": images, "updatedAt": current}},
    )
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
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    items = list(batch.get("items", []))
    new_items: list[dict[str, Any]] = []
    for offset in range(item_count):
        item_id = new_item_id()
        item_document = build_item_document(
            item_id=item_id,
            batch_id=batch["_id"],
            image_id=image_id,
            order=starting_order + offset,
            now=current,
        )
        items.append(item_document)
        new_items.append(item_document)

    images = list(batch.get("images", []))
    for image in images:
        if image.get("imageId") == image_id:
            image["status"] = ImageState.COMMITTED.value
            image["committedAt"] = current
            image["updatedAt"] = current
            break

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"images": images, "items": items, "updatedAt": current}},
    )
    return new_items


async def start_image_detection(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    image_id: str,
    *,
    now: datetime,
) -> None:
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    images = list(batch.get("images", []))
    for image in images:
        if image.get("imageId") == image_id:
            image["status"] = ImageState.DETECTING.value
            image["updatedAt"] = now
            break

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"images": images, "updatedAt": now}},
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
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    images = list(batch.get("images", []))
    for image in images:
        if image.get("imageId") == image_id:
            image["status"] = ImageState.READY.value
            image["subject"] = subject
            image["boxes"] = list(boxes)
            image["detection"] = {
                "model": model,
                "rawProviderResponse": raw_provider_response,
                "failureCode": None,
                "failureMessage": None,
            }
            image["updatedAt"] = now
            break

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"images": images, "updatedAt": now}},
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
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    images = list(batch.get("images", []))
    for image in images:
        if image.get("imageId") == image_id:
            image["status"] = ImageState.DETECT_FAILED.value
            image["detection"] = {
                "model": None,
                "rawProviderResponse": None,
                "failureCode": failure_code,
                "failureMessage": failure_message,
            }
            image["updatedAt"] = now
            break

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"images": images, "updatedAt": now}},
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
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    images = list(batch.get("images", []))
    for image in images:
        if image.get("imageId") == image_id:
            image["status"] = ImageState.READY.value
            if subject is not None:
                image["subject"] = subject
            image["boxes"] = list(boxes)
            image["updatedAt"] = now
            break

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"images": images, "updatedAt": now}},
    )


async def delete_batch_image(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    image_id: str,
    *,
    now: datetime,
) -> None:
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    images = list(batch.get("images", []))
    for image in images:
        if image.get("imageId") == image_id:
            image["status"] = ImageState.DELETED.value
            image["updatedAt"] = now
            break

    items = list(batch.get("items", []))
    for item in items:
        if item.get("imageId") == image_id:
            item["status"] = ItemState.DELETED.value
            item["updatedAt"] = now

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"images": images, "items": items, "updatedAt": now}},
    )


async def commit_image_boxes(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    image_id: str,
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    images = list(batch.get("images", []))
    target_image = None
    for image in images:
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

    items = list(batch.get("items", []))
    items.extend(new_items)

    target_image["status"] = ImageState.COMMITTED.value
    target_image["committedAt"] = now
    target_image["updatedAt"] = now

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"images": images, "items": items, "updatedAt": now}},
    )
    return new_items


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
            "$inc": {"items.$.retryCount": 1},
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
    now: datetime,
) -> None:
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    items = list(batch.get("items", []))
    for item in items:
        if item.get("itemId") == item_id:
            item["status"] = ItemState.READY.value
            item["crop"] = crop
            item["draft"] = draft
            item["extraction"] = extraction
            item["leaseUntil"] = None
            item["updatedAt"] = now
            break

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"items": items, "updatedAt": now}},
    )


async def save_item_extraction_failure(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    extraction: dict[str, Any],
    now: datetime,
) -> None:
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    items = list(batch.get("items", []))
    for item in items:
        if item.get("itemId") == item_id:
            item["status"] = ItemState.FAILED.value
            item["extraction"] = extraction
            item["leaseUntil"] = None
            item["updatedAt"] = now
            break

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"items": items, "updatedAt": now}},
    )


async def reset_item_for_retry(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    now: datetime,
) -> bool:
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    items = list(batch.get("items", []))
    changed = False
    for item in items:
        if item.get("itemId") != item_id:
            continue
        status = item.get("status")
        lease_until = item.get("leaseUntil")
        if (
            status == ItemState.FAILED.value
            or status == ItemState.SUBMIT_FAILED.value
            or (
                status == ItemState.EXTRACTING.value
                and isinstance(lease_until, datetime)
                and lease_until <= now
            )
        ):
            item["status"] = ItemState.QUEUED.value
            item["leaseUntil"] = None
            item["updatedAt"] = now
            changed = True
        break

    if not changed:
        return False

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"items": items, "updatedAt": now}},
    )
    return True


async def update_item_draft(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    draft_update: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    items = list(batch.get("items", []))
    updated_item: dict[str, Any] | None = None
    for item in items:
        if item.get("itemId") != item_id:
            continue
        draft = dict(item.get("draft", {}))
        allowed = {"text", "problemType", "graphDsl", "correctAnswer", "tags", "subject"}
        for key, value in draft_update.items():
            if key in allowed:
                draft[key] = value
        item["draft"] = draft
        item["updatedAt"] = now
        updated_item = item
        break

    if updated_item is None:
        return None

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"items": items, "updatedAt": now}},
    )
    return updated_item


async def mark_item_deleted(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    now: datetime,
) -> bool:
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    items = list(batch.get("items", []))
    changed = False
    for item in items:
        if item.get("itemId") != item_id:
            continue
        status = item.get("status")
        if status in {ItemState.DELETED.value, ItemState.SUBMITTED.value}:
            return True
        item["previousStatus"] = status
        item["status"] = ItemState.DELETED.value
        item["deletedAt"] = now
        item["updatedAt"] = now
        changed = True
        break

    if not changed:
        return False

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"items": items, "updatedAt": now}},
    )
    return True


async def undo_item_deletion(
    database: Any,
    batch_id: str | ObjectId,
    user_id: Any,
    item_id: str,
    *,
    now: datetime,
) -> bool:
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    items = list(batch.get("items", []))
    changed = False
    for item in items:
        if item.get("itemId") != item_id:
            continue
        if item.get("status") != ItemState.DELETED.value:
            return False
        previous_status = item.pop("previousStatus", None)
        if previous_status is None:
            return False
        item["status"] = previous_status
        item.pop("deletedAt", None)
        item["updatedAt"] = now
        changed = True
        break

    if not changed:
        return False

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": {"items": items, "updatedAt": now}},
    )
    return True


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
    batch = await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )
    if batch is None:
        raise ValueError("Batch not found")

    result_by_item = {result["itemId"]: result for result in item_results}
    items = list(batch.get("items", []))
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

    await _collection(database).update_one(
        {"_id": _object_id(batch_id), "userId": user_id},
        {"$set": update},
    )
    return await _collection(database).find_one(
        {"_id": _object_id(batch_id), "userId": user_id}
    )


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
        {"$set": {"status": BatchState.DELETED.value, "updatedAt": current}},
    )


async def ensure_batch_indexes(database: Any) -> None:
    collection = _collection(database)
    create_index = getattr(collection, "create_index", None)
    if not callable(create_index):
        return
    for index in BATCH_INDEXES:
        await create_index(index["keys"], name=index["name"])
