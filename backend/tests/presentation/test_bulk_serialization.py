from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from app.domain.ingestion import ImageState, ItemState
from app.presentation.bulk_serialization import (
    BatchResponse,
    SubmitItemResult,
    _build_submit_result,
    serialize_batch,
)

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
BATCH_ID = ObjectId.from_datetime(NOW)
BATCH_ID_STR = str(BATCH_ID)


def _make_source_image(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "bucket": "test-bucket",
        "objectKey": "obj-1",
        "contentType": "image/png",
        "sizeBytes": 100,
        "sha256": "abc123",
        "width": 10,
        "height": 10,
        "uploadedAt": NOW,
    }
    base.update(overrides)
    return base


def _make_image(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "imageId": "img-1",
        "status": ImageState.READY.value,
        "order": 0,
        "sourceImage": _make_source_image(),
        "subject": "math",
        "boxes": [{"x": 1, "y": 2, "w": 3, "h": 4}],
        "detection": {
            "model": "vlm-1",
            "rawProviderResponse": {"foo": "bar"},
            "failureCode": None,
            "failureMessage": None,
        },
        "committedAt": None,
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    base.update(overrides)
    return base


def _make_item(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "itemId": "item-1",
        "imageId": "img-1",
        "batchId": BATCH_ID,
        "status": ItemState.READY.value,
        "order": 0,
        "draft": {"text": "question"},
        "extraction": {"raw": "data"},
        "retryCount": 0,
        "submit": {},
        "origin": {"source": "upload"},
        "crop": {
            "bucket": "test-bucket",
            "objectKey": "crop-1",
            "contentType": "image/png",
        },
        "leaseUntil": None,
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    base.update(overrides)
    return base


def _make_batch(images: list | None = None, items: list | None = None, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_id": BATCH_ID,
        "userId": ObjectId.from_datetime(NOW),
        "status": "active",
        "images": images if images is not None else [_make_image()],
        "items": items if items is not None else [_make_item()],
        "createdAt": NOW,
        "updatedAt": NOW,
        "expiresAt": NOW,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Complete response shape
# ---------------------------------------------------------------------------


def test_serialize_batch_complete_shape():
    batch = _make_batch()
    result = serialize_batch(batch)

    assert set(result.keys()) == {"batch"}
    payload = result["batch"]
    assert payload["id"] == BATCH_ID_STR
    assert payload["userId"] == str(batch["userId"])
    assert payload["status"] == "active"
    assert payload["createdAt"] == NOW
    assert payload["updatedAt"] == NOW
    assert payload["expiresAt"] == NOW

    image = payload["images"][0]
    assert image == {
        "imageId": "img-1",
        "status": "ready",
        "order": 0,
        "sourceImage": {
            "bucket": "test-bucket",
            "objectKey": "obj-1",
            "contentType": "image/png",
            "sizeBytes": 100,
            "sha256": "abc123",
            "width": 10,
            "height": 10,
            "uploadedAt": NOW,
            "mediaUrl": "/api/v1/ingestion-batches/{}/images/img-1/source".format(BATCH_ID_STR),
        },
        "subject": "math",
        "boxes": [{"x": 1, "y": 2, "w": 3, "h": 4}],
        "detection": {
            "model": "vlm-1",
            "rawProviderResponse": {"foo": "bar"},
            "failureCode": None,
            "failureMessage": None,
        },
        "committedAt": None,
        "createdAt": NOW,
        "updatedAt": NOW,
    }

    item = payload["items"][0]
    assert item == {
        "itemId": "item-1",
        "imageId": "img-1",
        "batchId": BATCH_ID_STR,
        "status": "ready",
        "order": 0,
        "draft": {"text": "question"},
        "extraction": {"raw": "data"},
        "retryCount": 0,
        "submit": {},
        "origin": {"source": "upload"},
        "crop": {
            "bucket": "test-bucket",
            "objectKey": "crop-1",
            "contentType": "image/png",
            "mediaUrl": "/api/v1/ingestion-batches/{}/items/item-1/crop".format(BATCH_ID_STR),
        },
        "leaseUntil": None,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def test_serialize_batch_validates_as_batch_response():
    """The serialized dict must be accepted by the BatchResponse model."""
    batch = _make_batch()
    result = serialize_batch(batch)
    response = BatchResponse(**result)
    assert response.batch.id == BATCH_ID_STR


# ---------------------------------------------------------------------------
# include_deleted True / False
# ---------------------------------------------------------------------------


def test_include_deleted_false_filters_deleted_images_and_items():
    deleted_image = _make_image(
        imageId="img-deleted",
        status=ImageState.DELETED.value,
        sourceImage=_make_source_image(objectKey="obj-deleted"),
    )
    deleted_item = _make_item(
        itemId="item-deleted",
        status=ItemState.DELETED.value,
        crop=None,
    )
    batch = _make_batch(images=[_make_image(), deleted_image], items=[_make_item(), deleted_item])

    result = serialize_batch(batch, include_deleted=False)
    image_ids = [img["imageId"] for img in result["batch"]["images"]]
    item_ids = [it["itemId"] for it in result["batch"]["items"]]
    assert image_ids == ["img-1"]
    assert item_ids == ["item-1"]


def test_include_deleted_true_keeps_deleted_images_and_items():
    deleted_image = _make_image(
        imageId="img-deleted",
        status=ImageState.DELETED.value,
        sourceImage=_make_source_image(objectKey="obj-deleted"),
    )
    deleted_item = _make_item(
        itemId="item-deleted",
        status=ItemState.DELETED.value,
        crop=None,
    )
    batch = _make_batch(images=[_make_image(), deleted_image], items=[_make_item(), deleted_item])

    result = serialize_batch(batch, include_deleted=True)
    image_ids = [img["imageId"] for img in result["batch"]["images"]]
    item_ids = [it["itemId"] for it in result["batch"]["items"]]
    assert image_ids == ["img-1", "img-deleted"]
    assert item_ids == ["item-1", "item-deleted"]


# ---------------------------------------------------------------------------
# Source image media URL present and omitted
# ---------------------------------------------------------------------------


def test_source_image_media_url_present():
    batch = _make_batch()
    result = serialize_batch(batch)
    source_image = result["batch"]["images"][0]["sourceImage"]
    assert "mediaUrl" in source_image
    assert source_image["mediaUrl"] == "/api/v1/ingestion-batches/{}/images/img-1/source".format(BATCH_ID_STR)


def test_source_image_media_url_omitted_when_image_id_missing():
    """An image without an imageId still serializes but omits mediaUrl."""
    batch = _make_batch(images=[_make_image(imageId="")])
    result = serialize_batch(batch)
    source_image = result["batch"]["images"][0]["sourceImage"]
    assert "mediaUrl" not in source_image


# ---------------------------------------------------------------------------
# Crop media URL present and omitted
# ---------------------------------------------------------------------------


def test_crop_media_url_present():
    batch = _make_batch()
    result = serialize_batch(batch)
    crop = result["batch"]["items"][0]["crop"]
    assert "mediaUrl" in crop
    assert crop["mediaUrl"] == "/api/v1/ingestion-batches/{}/items/item-1/crop".format(BATCH_ID_STR)


def test_crop_media_url_omitted_when_crop_none():
    batch = _make_batch(items=[_make_item(crop=None)])
    result = serialize_batch(batch)
    assert result["batch"]["items"][0]["crop"] is None


# ---------------------------------------------------------------------------
# Missing or None detection fields
# ---------------------------------------------------------------------------


def test_detection_fields_none_when_detection_missing():
    batch = _make_batch(images=[_make_image(detection=None)])
    result = serialize_batch(batch)
    detection = result["batch"]["images"][0]["detection"]
    assert detection == {
        "model": None,
        "rawProviderResponse": None,
        "failureCode": None,
        "failureMessage": None,
    }


def test_detection_fields_none_when_detection_empty():
    batch = _make_batch(images=[_make_image(detection={})])
    result = serialize_batch(batch)
    detection = result["batch"]["images"][0]["detection"]
    assert detection == {
        "model": None,
        "rawProviderResponse": None,
        "failureCode": None,
        "failureMessage": None,
    }


def test_detection_partial_fields_preserved():
    batch = _make_batch(
        images=[_make_image(detection={"model": "vlm-2", "failureCode": "oops"})]
    )
    result = serialize_batch(batch)
    detection = result["batch"]["images"][0]["detection"]
    assert detection == {
        "model": "vlm-2",
        "rawProviderResponse": None,
        "failureCode": "oops",
        "failureMessage": None,
    }


# ---------------------------------------------------------------------------
# No input document mutation
# ---------------------------------------------------------------------------


def test_serialize_batch_does_not_mutate_input():
    batch = _make_batch()
    snapshot = copy.deepcopy(batch)
    serialize_batch(batch)
    assert batch == snapshot


def test_serialize_batch_does_not_mutate_input_with_deleted():
    batch = _make_batch(
        images=[_make_image(), _make_image(imageId="img-del", status=ImageState.DELETED.value)],
        items=[_make_item(), _make_item(itemId="item-del", status=ItemState.DELETED.value, crop=None)],
    )
    snapshot = copy.deepcopy(batch)
    serialize_batch(batch, include_deleted=True)
    assert batch == snapshot


def test_serialize_batch_does_not_mutate_crop_dict():
    """The crop dict inside an item must not gain a mediaUrl key in the input."""
    batch = _make_batch()
    original_crop = copy.deepcopy(batch["items"][0]["crop"])
    serialize_batch(batch)
    assert batch["items"][0]["crop"] == original_crop


# ---------------------------------------------------------------------------
# _build_submit_result
# ---------------------------------------------------------------------------


def test_build_submit_result_ready_item():
    item = _make_item(
        status=ItemState.SUBMITTED.value,
        submit={"submittedProblemId": "prob-1", "success": True, "failureCode": None, "failureMessage": None},
    )
    result = _build_submit_result(item)
    assert result == SubmitItemResult(
        itemId="item-1",
        status=ItemState.SUBMITTED.value,
        submittedProblemId="prob-1",
        failureCode=None,
        failureMessage=None,
    )


def test_build_submit_result_failed_item():
    item = _make_item(
        status=ItemState.SUBMIT_FAILED.value,
        submit={"submittedProblemId": None, "success": False, "failureCode": "bad", "failureMessage": "oops"},
    )
    result = _build_submit_result(item)
    assert result == SubmitItemResult(
        itemId="item-1",
        status=ItemState.SUBMIT_FAILED.value,
        submittedProblemId=None,
        failureCode="bad",
        failureMessage="oops",
    )


def test_build_submit_result_missing_submit():
    item = _make_item(submit=None)
    result = _build_submit_result(item)
    assert result.itemId == "item-1"
    assert result.submittedProblemId is None
    assert result.failureCode is None
    assert result.failureMessage is None
