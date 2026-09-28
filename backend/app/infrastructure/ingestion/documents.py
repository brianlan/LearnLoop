from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from bson import ObjectId

from app.domain.ingestion import BatchState, ImageState, ItemState
from app.problem_variation import VariationStatus


def new_image_id() -> str:
    return str(uuid4())


def new_item_id() -> str:
    return str(uuid4())


def build_source_image(
    *,
    bucket: str,
    object_key: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    uploaded_at: datetime,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "objectKey": object_key,
        "contentType": content_type,
        "sizeBytes": size_bytes,
        "sha256": sha256,
        "uploadedAt": uploaded_at,
        "width": width,
        "height": height,
    }


def build_crop_image(
    *,
    bucket: str,
    object_key: str,
    content_type: str,
    size_bytes: int,
    width: int,
    height: int,
    uploaded_at: datetime,
) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "objectKey": object_key,
        "contentType": content_type,
        "sizeBytes": size_bytes,
        "width": width,
        "height": height,
        "uploadedAt": uploaded_at,
    }


def build_audit_image_key(
    user_id: Any,
    batch_id: Any,
    item_id: str,
    content_type: str | None,
) -> str:
    """Deterministic permanent audit-image key for one item's admitted variant.

    Keyed by batch/item identity (never a fresh uuid) so a failed-submit retry
    overwrites the same object instead of accumulating copies, and so cleanup
    can recompute the key of an abandoned pending copy without extra
    bookkeeping.
    """
    extension = "png"
    if content_type and content_type.startswith("image/"):
        candidate = content_type.split("/", 1)[1].split(";", 1)[0].strip().lower()
        if candidate:
            extension = candidate
    return f"users/{user_id}/problems/audit/{batch_id}/{item_id}.{extension}"


def build_image_document(
    *,
    image_id: str,
    source_image: dict[str, Any],
    order: int,
    now: datetime,
) -> dict[str, Any]:
    return {
        "imageId": image_id,
        "status": ImageState.UPLOADED.value,
        "order": order,
        "sourceImage": source_image,
        "subject": None,
        "boxes": [],
        "detection": {
            "model": None,
            "rawProviderResponse": None,
            "failureCode": None,
            "failureMessage": None,
        },
        "committedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }


def build_item_document(
    *,
    item_id: str,
    batch_id: ObjectId,
    image_id: str,
    order: int,
    now: datetime,
    box: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "itemId": item_id,
        "imageId": image_id,
        "batchId": batch_id,
        "status": ItemState.QUEUED.value,
        "order": order,
        "box": box,
        "draft": {
            "text": None,
            "problemType": None,
            "graphDsl": None,
            "correctAnswer": None,
            "tags": [],
            "subject": None,
        },
        "extraction": {
            "success": None,
            "model": None,
            "rawText": None,
            "rawProblemType": None,
            "rawGraphDsl": None,
            "rawProviderResponse": None,
            "failureCode": None,
            "failureMessage": None,
            "requestStartedAt": None,
            "requestFinishedAt": None,
        },
        "retryCount": 0,
        # Per-item semantic revision: bumped by semantic source/candidate
        # changes and new generation; tags and worker progress never bump it.
        "contentRevision": 0,
        "variation": {
            "status": VariationStatus.NOT_REQUESTED.value,
            "generationCount": 0,
            "original": None,
            "candidate": None,
            "validation": None,
            "validatedRevision": None,
            "claimToken": None,
            "leaseUntil": None,
            "queuedAt": None,
        },
        "submit": {
            "submittedProblemId": None,
            "success": None,
            "failureCode": None,
            "failureMessage": None,
        },
        "origin": {
            "batchId": str(batch_id),
            "imageId": image_id,
            "itemId": item_id,
        },
        "crop": None,
        "leaseUntil": None,
        "createdAt": now,
        "updatedAt": now,
    }


def build_batch_document(
    *,
    batch_id: ObjectId,
    user_id: Any,
    expires_at: datetime,
    now: datetime,
    ingestion_mode: Any = None,
) -> dict[str, Any]:
    return {
        "_id": batch_id,
        "userId": user_id,
        "status": BatchState.ACTIVE.value,
        # Immutable after creation; missing on legacy batches means original.
        "ingestionMode": ingestion_mode,
        "images": [],
        "items": [],
        # Monotonic mutation counter used as the optimistic-concurrency token
        # by guarded read-compute-write mutations (BSON dates only carry
        # millisecond precision, so updatedAt equality is not collision-safe).
        "revision": 0,
        "createdAt": now,
        "updatedAt": now,
        "expiresAt": expires_at,
    }
