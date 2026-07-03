from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING

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
    for offset, _box in enumerate(target_image.get("boxes", [])):
        item_id = new_item_id()
        item_document = build_item_document(
            item_id=item_id,
            batch_id=batch["_id"],
            image_id=image_id,
            order=next_order + offset,
            now=now,
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
