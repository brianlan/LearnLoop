from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.domain.ingestion import ImageState, ItemState
from app.infrastructure.storage.mongo import Document
from app.presentation.helpers import (
    build_ingestion_crop_url,
    build_ingestion_source_image_url,
)
from app.presentation.schemas import SourceImagePayload


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


class SubmitItemResult(BaseModel):
    itemId: str
    status: str
    submittedProblemId: str | None = None
    failureCode: str | None = None
    failureMessage: str | None = None


class SubmitSummaryPayload(BaseModel):
    batchId: str
    status: str
    items: list[SubmitItemResult]


class SubmitSummaryResponse(BaseModel):
    submitSummary: SubmitSummaryPayload


def _serialize_source_image(
    source_image: dict[str, Any],
    batch_id: str | None = None,
    image_id: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "bucket": source_image["bucket"],
        "objectKey": source_image["objectKey"],
        "contentType": source_image.get("contentType"),
        "sizeBytes": source_image.get("sizeBytes"),
        "sha256": source_image.get("sha256"),
        "width": source_image.get("width"),
        "height": source_image.get("height"),
        "uploadedAt": source_image.get("uploadedAt"),
    }
    if batch_id and image_id:
        data["mediaUrl"] = build_ingestion_source_image_url(batch_id, image_id)
    return data


def _serialize_image(image: dict[str, Any], batch_id: str | None = None) -> dict[str, Any]:
    detection = image.get("detection") or {}
    return {
        "imageId": image["imageId"],
        "status": image["status"],
        "order": image["order"],
        "sourceImage": _serialize_source_image(
            image.get("sourceImage") or {}, batch_id=batch_id, image_id=image.get("imageId")
        ),
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


def _serialize_item(item: dict[str, Any], batch_id: str | None = None) -> dict[str, Any]:
    crop = item.get("crop")
    if crop and batch_id:
        crop = dict(crop)
        crop["mediaUrl"] = build_ingestion_crop_url(batch_id, item["itemId"])
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
        "crop": crop,
        "leaseUntil": item.get("leaseUntil"),
        "createdAt": item["createdAt"],
        "updatedAt": item["updatedAt"],
    }


def serialize_batch(batch: Document, *, include_deleted: bool = False) -> dict[str, Any]:
    batch_id = str(batch["_id"])
    if include_deleted:
        images = list(batch.get("images", []))
        items = list(batch.get("items", []))
    else:
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
            "id": batch_id,
            "userId": str(batch["userId"]),
            "status": batch["status"],
            "images": [_serialize_image(image, batch_id=batch_id) for image in images],
            "items": [_serialize_item(item, batch_id=batch_id) for item in items],
            "createdAt": batch["createdAt"],
            "updatedAt": batch["updatedAt"],
            "expiresAt": batch["expiresAt"],
        }
    }


def _build_submit_result(item: dict[str, Any]) -> SubmitItemResult:
    submit = dict(item.get("submit") or {})
    return SubmitItemResult(
        itemId=item["itemId"],
        status=item["status"],
        submittedProblemId=submit.get("submittedProblemId"),
        failureCode=submit.get("failureCode"),
        failureMessage=submit.get("failureMessage"),
    )
