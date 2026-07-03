from __future__ import annotations

import hashlib
import logging
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

import base64

logger = logging.getLogger(__name__)

from app.domain.ingestion import (
    BatchState,
    ImageState,
    InvalidBoxError,
    ItemState,
    transition_image_state,
    validate_boxes,
)
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion.documents import build_source_image
from app.infrastructure.ingestion.image_size import get_image_size
from app.infrastructure.ingestion.repository import (
    add_source_image,
    commit_image_boxes,
    create_batch as create_batch_repo,
    delete_batch_image,
    get_active_batch_for_user,
    get_batch,
    is_batch_expired,
    reset_item_for_retry,
    save_image_boxes_and_subject,
    save_image_detection_failure,
    save_image_detection_success,
    start_image_detection,
)
from app.infrastructure.storage.mongo import Document
from app.infrastructure.storage.s3 import S3StorageAdapter
from app.infrastructure.vlm.base_client import BaseVLMError
from app.presentation.deps import (
    DatabaseDependency,
    HelperVLMDependency,
    get_app_settings,
    get_current_user,
    get_s3_storage,
)
from app.presentation.errors import ApiError
from app.presentation.helpers import parse_object_id
from app.presentation.schemas import SourceImagePayload

router = APIRouter(prefix="/ingestion-batches", tags=["bulk-ingestion"])

CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]
StorageDependency = Annotated[S3StorageAdapter, Depends(get_s3_storage)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]


class DetectionResponse(BaseModel):
    model: str | None
    rawProviderResponse: Any | None
    failureCode: str | None
    failureMessage: str | None


class ImageResponse(BaseModel):
    imageId: str
    status: str
    order: int
    sourceImage: SourceImagePayload
    subject: str | None
    boxes: list[dict[str, Any]]
    detection: DetectionResponse
    committedAt: datetime | None
    createdAt: datetime
    updatedAt: datetime


class ItemResponse(BaseModel):
    itemId: str
    imageId: str
    batchId: str
    status: str
    order: int
    draft: dict[str, Any]
    extraction: dict[str, Any]
    retryCount: int
    submit: dict[str, Any]
    origin: dict[str, Any]
    crop: dict[str, Any] | None
    leaseUntil: datetime | None
    createdAt: datetime
    updatedAt: datetime


class BatchPayload(BaseModel):
    id: str
    userId: str
    status: str
    images: list[ImageResponse]
    items: list[ItemResponse]
    createdAt: datetime
    updatedAt: datetime
    expiresAt: datetime


class BatchResponse(BaseModel):
    batch: BatchPayload


def _guess_extension(upload: UploadFile) -> str:
    if upload.filename:
        suffix = Path(upload.filename).suffix
        if suffix:
            return suffix
    if upload.content_type:
        guessed = mimetypes.guess_extension(upload.content_type)
        if guessed:
            return guessed
    return ".bin"


def _serialize_source_image(source_image: dict[str, Any]) -> dict[str, Any]:
    return {
        "bucket": source_image["bucket"],
        "objectKey": source_image["objectKey"],
        "contentType": source_image.get("contentType"),
        "sizeBytes": source_image.get("sizeBytes"),
        "sha256": source_image.get("sha256"),
        "width": source_image.get("width"),
        "height": source_image.get("height"),
        "uploadedAt": source_image.get("uploadedAt"),
    }


def _serialize_image(image: dict[str, Any]) -> dict[str, Any]:
    detection = image.get("detection") or {}
    return {
        "imageId": image["imageId"],
        "status": image["status"],
        "order": image["order"],
        "sourceImage": _serialize_source_image(image.get("sourceImage") or {}),
        "subject": image.get("subject"),
        "boxes": list(image.get("boxes", [])),
        "detection": {
            "model": detection.get("model"),
            "rawProviderResponse": detection.get("rawProviderResponse"),
            "failureCode": detection.get("failureCode"),
            "failureMessage": detection.get("failureMessage"),
        },
        "committedAt": image.get("committedAt"),
        "createdAt": image["createdAt"],
        "updatedAt": image["updatedAt"],
    }


def _serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "itemId": item["itemId"],
        "imageId": item["imageId"],
        "batchId": str(item["batchId"]),
        "status": item["status"],
        "order": item["order"],
        "draft": dict(item.get("draft", {})),
        "extraction": dict(item.get("extraction", {})),
        "retryCount": item.get("retryCount", 0),
        "submit": dict(item.get("submit", {})),
        "origin": dict(item.get("origin", {})),
        "crop": item.get("crop"),
        "leaseUntil": item.get("leaseUntil"),
        "createdAt": item["createdAt"],
        "updatedAt": item["updatedAt"],
    }


def serialize_batch(batch: Document) -> dict[str, Any]:
    images = [
        image for image in batch.get("images", [])
        if image.get("status") != ImageState.DELETED.value
    ]
    items = [
        item for item in batch.get("items", [])
        if item.get("status") != ItemState.DELETED.value
    ]
    return {
        "batch": {
            "id": str(batch["_id"]),
            "userId": str(batch["userId"]),
            "status": batch["status"],
            "images": [_serialize_image(image) for image in images],
            "items": [_serialize_item(item) for item in items],
            "createdAt": batch["createdAt"],
            "updatedAt": batch["updatedAt"],
            "expiresAt": batch["expiresAt"],
        }
    }


def _find_image_or_404(batch: Document, image_id: str) -> dict[str, Any]:
    for image in batch.get("images", []):
        if image.get("imageId") == image_id:
            return image
    raise ApiError(404, "NOT_FOUND", "Image not found")


class SaveBoxesRequest(BaseModel):
    subject: str | None = None
    boxes: list[dict[str, Any]]


async def _load_owned_batch(
    database: Any,
    batch_id: str,
    user_id: Any,
) -> Document:
    parse_object_id(batch_id, resource_name="Batch")
    batch = await get_batch(database, batch_id, user_id)
    if batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    if is_batch_expired(batch):
        raise ApiError(409, "BATCH_EXPIRED", "Batch has expired")
    if batch["status"] != BatchState.ACTIVE.value:
        raise ApiError(409, "INVALID_BATCH_STATE", "Batch is not active")
    return batch


async def _validate_image_upload(
    image: UploadFile,
    max_image_bytes: int,
) -> tuple[bytes, str]:
    content_type = image.content_type or ""
    if not content_type.startswith("image/"):
        raise ApiError(400, "INVALID_IMAGE", "Uploaded file must be an image")

    image_bytes = await image.read()
    if not image_bytes:
        raise ApiError(400, "INVALID_IMAGE", "Uploaded image is empty")
    if len(image_bytes) > max_image_bytes:
        raise ApiError(
            400,
            "IMAGE_TOO_LARGE",
            f"Image exceeds maximum size of {max_image_bytes} bytes",
        )
    return image_bytes, content_type


@router.post("", response_model=BatchResponse, status_code=201)
async def create_batch(
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
) -> BatchResponse:
    batch = await create_batch_repo(database, user["_id"], settings)
    return BatchResponse(**serialize_batch(batch))


@router.get("/active", response_model=BatchResponse)
async def get_active_batch(
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> BatchResponse:
    batch = await get_active_batch_for_user(database, user["_id"])
    if batch is None:
        raise ApiError(404, "NOT_FOUND", "No active batch found")
    return BatchResponse(**serialize_batch(batch))


@router.post("/{batch_id}/images", response_model=BatchResponse, status_code=201)
async def upload_batch_images(
    batch_id: str,
    images: Annotated[list[UploadFile], File(...)],
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
    s3_storage: StorageDependency,
) -> BatchResponse:
    batch = await _load_owned_batch(database, batch_id, user["_id"])

    if not images:
        raise ApiError(400, "INVALID_IMAGE", "No images provided")

    existing_count = len(batch.get("images", []))
    if existing_count + len(images) > settings.bulk_ingestion_max_images:
        raise ApiError(
            409,
            "BATCH_IMAGE_LIMIT_EXCEEDED",
            f"Batch cannot exceed {settings.bulk_ingestion_max_images} images",
        )

    now = datetime.now(UTC)
    for offset, image in enumerate(images):
        image_bytes, content_type = await _validate_image_upload(
            image, settings.bulk_ingestion_max_image_bytes
        )
        object_key = s3_storage.build_object_key(
            str(user["_id"]), _guess_extension(image), category="ingestion/batches"
        )
        s3_storage.put_object(settings.s3_bucket, object_key, image_bytes, content_type)

        width, height = get_image_size(image_bytes) or (None, None)
        source_image = build_source_image(
            bucket=settings.s3_bucket,
            object_key=object_key,
            content_type=content_type,
            size_bytes=len(image_bytes),
            sha256=hashlib.sha256(image_bytes).hexdigest(),
            uploaded_at=now,
            width=width,
            height=height,
        )
        await add_source_image(
            database,
            batch_id,
            user["_id"],
            source_image,
            order=existing_count + offset,
            now=now,
        )

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch))


@router.post("/{batch_id}/images/{image_id}/detect", response_model=BatchResponse)
async def detect_image_boxes(
    batch_id: str,
    image_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    s3_storage: StorageDependency,
    vlm: HelperVLMDependency,
) -> BatchResponse:
    batch = await _load_owned_batch(database, batch_id, user["_id"])
    image = _find_image_or_404(batch, image_id)

    current_state = ImageState(image["status"])
    try:
        transition_image_state(current_state, ImageState.DETECTING)
    except Exception as exc:
        raise ApiError(
            409, "INVALID_IMAGE_STATE", f"Image cannot be detected: {exc}"
        ) from exc
    await start_image_detection(
        database, batch_id, user["_id"], image_id, now=datetime.now(UTC)
    )

    source_image = image.get("sourceImage") or {}
    try:
        image_bytes = s3_storage.get_object(
            source_image["bucket"], source_image["objectKey"]
        )
    except Exception as exc:
        logger.exception("Unexpected storage read failure during detection")
        await save_image_detection_failure(
            database,
            batch_id,
            user["_id"],
            image_id,
            failure_code="storage-read-failed",
            failure_message=str(exc),
            now=datetime.now(UTC),
        )
        updated_batch = await get_batch(database, batch_id, user["_id"])
        if updated_batch is None:
            raise ApiError(404, "NOT_FOUND", "Batch not found")
        return BatchResponse(**serialize_batch(updated_batch))

    try:
        result = await vlm.detect_problem_boxes(
            image_base64=base64.b64encode(image_bytes).decode()
        )
        await save_image_detection_success(
            database,
            batch_id,
            user["_id"],
            image_id,
            subject=result.subject,
            boxes=[box.model_dump() for box in result.boxes],
            model=result.model,
            raw_provider_response=result.raw_provider_response,
            now=datetime.now(UTC),
        )
    except BaseVLMError as exc:
        await save_image_detection_failure(
            database,
            batch_id,
            user["_id"],
            image_id,
            failure_code=exc.code,
            failure_message=exc.args[0],
            now=datetime.now(UTC),
        )
    except Exception as exc:
        logger.exception("Unexpected error during box detection")
        await save_image_detection_failure(
            database,
            batch_id,
            user["_id"],
            image_id,
            failure_code="detection-failed",
            failure_message=str(exc),
            now=datetime.now(UTC),
        )

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch))


@router.patch("/{batch_id}/images/{image_id}", response_model=BatchResponse)
async def save_image_boxes(
    batch_id: str,
    image_id: str,
    request: SaveBoxesRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> BatchResponse:
    batch = await _load_owned_batch(database, batch_id, user["_id"])
    image = _find_image_or_404(batch, image_id)

    if image["status"] == ImageState.COMMITTED.value:
        raise ApiError(409, "IMAGE_ALREADY_COMMITTED", "Image has already been committed")
    if image["status"] == ImageState.DELETED.value:
        raise ApiError(409, "IMAGE_DELETED", "Image has been deleted")

    source_image = image.get("sourceImage") or {}
    try:
        validated_boxes = validate_boxes(
            request.boxes,
            source_image.get("width"),
            source_image.get("height"),
        )
    except InvalidBoxError as exc:
        raise ApiError(400, "INVALID_BOXES", str(exc)) from exc

    await save_image_boxes_and_subject(
        database,
        batch_id,
        user["_id"],
        image_id,
        subject=request.subject,
        boxes=validated_boxes,
        now=datetime.now(UTC),
    )

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch))


@router.delete("/{batch_id}/images/{image_id}", response_model=BatchResponse)
async def delete_image(
    batch_id: str,
    image_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> BatchResponse:
    await _load_owned_batch(database, batch_id, user["_id"])
    await delete_batch_image(
        database,
        batch_id,
        user["_id"],
        image_id,
        now=datetime.now(UTC),
    )
    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch))


@router.post("/{batch_id}/images/{image_id}/commit", response_model=BatchResponse)
async def commit_image(
    batch_id: str,
    image_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> BatchResponse:
    batch = await _load_owned_batch(database, batch_id, user["_id"])
    image = _find_image_or_404(batch, image_id)

    if image["status"] == ImageState.COMMITTED.value:
        return BatchResponse(**serialize_batch(batch))
    if image["status"] != ImageState.READY.value:
        raise ApiError(
            409,
            "IMAGE_NOT_READY",
            "Image must be ready before committing",
        )

    await commit_image_boxes(
        database,
        batch_id,
        user["_id"],
        image_id,
        now=datetime.now(UTC),
    )

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch))


def _find_item_or_404(batch: Document, item_id: str) -> dict[str, Any]:
    for item in batch.get("items", []):
        if item.get("itemId") == item_id:
            return item
    raise ApiError(404, "NOT_FOUND", "Item not found")


@router.post("/{batch_id}/extract", status_code=202)
async def start_extraction(
    batch_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> dict[str, Any]:
    # The global worker polls the database for queued items; this endpoint only
    # confirms the batch is actionable and tells the client to start polling.
    await _load_owned_batch(database, batch_id, user["_id"])
    return {"batchId": batch_id, "status": "extracting"}


@router.post("/{batch_id}/items/{item_id}/retry", response_model=BatchResponse)
async def retry_item(
    batch_id: str,
    item_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> BatchResponse:
    await _load_owned_batch(database, batch_id, user["_id"])
    retried = await reset_item_for_retry(
        database,
        batch_id,
        user["_id"],
        item_id,
        now=datetime.now(UTC),
    )
    if not retried:
        raise ApiError(
            409,
            "ITEM_NOT_RETRYABLE",
            "Item is not in a failed or stalled state",
        )

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch))
