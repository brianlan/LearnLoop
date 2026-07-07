from __future__ import annotations

import hashlib
import logging
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, NamedTuple

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import base64
from io import BytesIO

logger = logging.getLogger(__name__)

from app.domain.ingestion import (
    BatchState,
    ImageState,
    InvalidBoxError,
    ItemState,
    transition_image_state,
    validate_boxes,
)

from app.domain.models import ProblemSubject, ProblemType
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion.documents import build_source_image
from app.infrastructure.ingestion.image_size import get_image_size
from app.infrastructure.ingestion.pdf import PdfRenderError, render_pdf_pages
from app.infrastructure.ingestion.repository import (
    add_source_image,
    commit_image_boxes,
    create_batch as create_batch_repo,
    delete_batch_image,
    get_active_batch_for_user,
    get_batch,
    is_batch_expired,
    mark_item_deleted,
    reset_item_for_retry,
    save_image_boxes_and_subject,
    save_image_detection_failure,
    save_image_detection_success,
    start_image_detection,
    submit_items_and_complete_batch,
    undo_item_deletion,
    update_item_draft,
)
from app.infrastructure.storage.mongo import Document
from app.infrastructure.storage.s3 import StorageObjectNotFoundError
from app.infrastructure.vlm.base_client import BaseVLMError
from app.presentation.deps import (
    CurrentUserDependency,
    DatabaseDependency,
    HelperVLMDependency,
    SettingsDependency,
    StorageDependency,
)
from app.presentation.bulk_serialization import (
    BatchResponse,
    SubmitItemResult,
    SubmitSummaryPayload,
    SubmitSummaryResponse,
    _build_submit_result,
    serialize_batch,
)
from app.presentation.errors import ApiError
from app.presentation.helpers import (
    normalize_tags,
    parse_object_id,
)
from app.presentation.problem_creation import create_problem_from_draft

router = APIRouter(prefix="/ingestion-batches", tags=["bulk-ingestion"])


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


async def _load_owned_batch_for_read(
    database: Any,
    batch_id: str,
    user_id: Any,
) -> Document:
    parse_object_id(batch_id, resource_name="Batch")
    batch = await get_batch(database, batch_id, user_id)
    if batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return batch


class _UploadPayload(NamedTuple):
    image_bytes: bytes
    content_type: str
    width: int | None
    height: int | None
    extension: str


async def _expand_upload(
    upload: UploadFile,
    settings: Settings,
) -> list[_UploadPayload]:
    """Expand a single uploaded file into one or more image payloads.

    Images pass through directly. PDFs are rendered to one PNG page image
    per page. Unsupported files are rejected.
    """
    content_type = upload.content_type or ""
    filename = (upload.filename or "").lower()

    if content_type.startswith("image/"):
        image_bytes = await upload.read()
        if not image_bytes:
            raise ApiError(400, "INVALID_IMAGE", "Uploaded image is empty")
        if len(image_bytes) > settings.bulk_ingestion_max_image_bytes:
            raise ApiError(
                400,
                "IMAGE_TOO_LARGE",
                f"Image exceeds maximum size of {settings.bulk_ingestion_max_image_bytes} bytes",
            )
        width, height = get_image_size(image_bytes) or (None, None)
        return [_UploadPayload(image_bytes, content_type, width, height, _guess_extension(upload))]

    if content_type == "application/pdf" or filename.endswith(".pdf"):
        pdf_bytes = await upload.read()
        if not pdf_bytes:
            raise ApiError(400, "INVALID_PDF", "Uploaded PDF is empty")
        try:
            rendered_pages = render_pdf_pages(pdf_bytes)
        except PdfRenderError as exc:
            raise ApiError(400, "INVALID_PDF", str(exc)) from exc
        payloads: list[_UploadPayload] = []
        for page in rendered_pages:
            if len(page.bytes) > settings.bulk_ingestion_max_image_bytes:
                raise ApiError(
                    400,
                    "IMAGE_TOO_LARGE",
                    f"Rendered PDF page exceeds maximum size of {settings.bulk_ingestion_max_image_bytes} bytes",
                )
            payloads.append(
                _UploadPayload(page.bytes, page.content_type, page.width, page.height, ".png")
            )
        return payloads

    raise ApiError(400, "INVALID_IMAGE", "Uploaded file must be an image or PDF")


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

    # Expand all uploads (images pass through; PDFs render to page images)
    # before enforcing the batch image limit, so partial storage is avoided
    # on validation failures and the limit accounts for expanded pages.
    payloads: list[_UploadPayload] = []
    for upload in images:
        payloads.extend(await _expand_upload(upload, settings))

    if existing_count + len(payloads) > settings.bulk_ingestion_max_images:
        raise ApiError(
            409,
            "BATCH_IMAGE_LIMIT_EXCEEDED",
            f"Batch cannot exceed {settings.bulk_ingestion_max_images} images",
        )

    now = datetime.now(UTC)
    for offset, payload in enumerate(payloads):
        object_key = s3_storage.build_object_key(
            str(user["_id"]), payload.extension, category="ingestion/batches"
        )
        s3_storage.put_object(
            settings.s3_bucket, object_key, payload.image_bytes, payload.content_type
        )

        source_image = build_source_image(
            bucket=settings.s3_bucket,
            object_key=object_key,
            content_type=payload.content_type,
            size_bytes=len(payload.image_bytes),
            sha256=hashlib.sha256(payload.image_bytes).hexdigest(),
            uploaded_at=now,
            width=payload.width,
            height=payload.height,
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
        detected_boxes = validate_boxes(
            [box.model_dump() for box in result.boxes],
            None,
            None,
        )
        await save_image_detection_success(
            database,
            batch_id,
            user["_id"],
            image_id,
            subject=result.subject,
            boxes=detected_boxes,
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


class UpdateItemDraftRequest(BaseModel):
    text: str | None = None
    problemType: ProblemType | None = None
    graphDsl: str | None = None
    correctAnswer: str | None = None
    tags: list[str] | None = None
    subject: ProblemSubject | None = None


@router.get("/{batch_id}", response_model=BatchResponse)
async def get_batch_detail(
    batch_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> BatchResponse:
    batch = await _load_owned_batch_for_read(database, batch_id, user["_id"])
    return BatchResponse(**serialize_batch(batch, include_deleted=True))


@router.patch("/{batch_id}/items/{item_id}", response_model=BatchResponse)
async def update_item_draft_endpoint(
    batch_id: str,
    item_id: str,
    request: UpdateItemDraftRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> BatchResponse:
    await _load_owned_batch(database, batch_id, user["_id"])

    draft_update = request.model_dump(exclude_unset=True)
    if "tags" in draft_update:
        draft_update["tags"] = normalize_tags(draft_update["tags"])

    updated = await update_item_draft(
        database,
        batch_id,
        user["_id"],
        item_id,
        draft_update=draft_update,
        now=datetime.now(UTC),
    )
    if updated is None:
        raise ApiError(404, "NOT_FOUND", "Item not found")

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch, include_deleted=True))


@router.delete("/{batch_id}/items/{item_id}", response_model=BatchResponse)
async def delete_item(
    batch_id: str,
    item_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> BatchResponse:
    await _load_owned_batch(database, batch_id, user["_id"])
    await mark_item_deleted(
        database,
        batch_id,
        user["_id"],
        item_id,
        now=datetime.now(UTC),
    )

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch, include_deleted=True))


@router.post("/{batch_id}/items/{item_id}/undo-delete", response_model=BatchResponse)
async def undo_delete_item_endpoint(
    batch_id: str,
    item_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> BatchResponse:
    await _load_owned_batch(database, batch_id, user["_id"])
    restored = await undo_item_deletion(
        database,
        batch_id,
        user["_id"],
        item_id,
        now=datetime.now(UTC),
    )
    if not restored:
        raise ApiError(
            409,
            "ITEM_NOT_DELETED",
            "Item is not deleted or has no restorable state",
        )

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch, include_deleted=True))


@router.post("/{batch_id}/submit", response_model=SubmitSummaryResponse)
async def submit_batch(
    batch_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> SubmitSummaryResponse:
    batch = await _load_owned_batch_for_read(database, batch_id, user["_id"])
    if is_batch_expired(batch):
        raise ApiError(409, "BATCH_EXPIRED", "Batch has expired")
    if batch["status"] not in {
        BatchState.ACTIVE.value,
        BatchState.COMPLETED.value,
    }:
        raise ApiError(
            409,
            "INVALID_BATCH_STATE",
            "Batch is not active",
        )

    if batch["status"] == BatchState.COMPLETED.value:
        results = [
            _build_submit_result(item)
            for item in batch.get("items", [])
            if item.get("status") != ItemState.DELETED.value
        ]
        return SubmitSummaryResponse(
            submitSummary=SubmitSummaryPayload(
                batchId=batch_id,
                status=batch["status"],
                items=results,
            )
        )

    now = datetime.now(UTC)
    item_results: list[dict[str, Any]] = []
    for item in batch.get("items", []):
        if item.get("status") != ItemState.READY.value:
            continue

        try:
            problem = await create_problem_from_draft(
                database,
                user["_id"],
                draft=item.get("draft"),
                source_image=item.get("crop"),
                origin=item.get("origin"),
                now=now,
            )
            item_results.append(
                {
                    "itemId": item["itemId"],
                    "status": ItemState.SUBMITTED.value,
                    "submit": {
                        "submittedProblemId": str(problem["_id"]),
                        "success": True,
                        "failureCode": None,
                        "failureMessage": None,
                    },
                }
            )
        except ApiError as exc:
            item_results.append(
                {
                    "itemId": item["itemId"],
                    "status": ItemState.SUBMIT_FAILED.value,
                    "submit": {
                        "submittedProblemId": None,
                        "success": False,
                        "failureCode": exc.code,
                        "failureMessage": exc.message,
                    },
                }
            )

    updated_batch = await submit_items_and_complete_batch(
        database,
        batch_id,
        user["_id"],
        item_results=item_results,
        now=now,
    )
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")

    results = [
        SubmitItemResult(
            itemId=r["itemId"],
            status=r["status"],
            submittedProblemId=r["submit"]["submittedProblemId"],
            failureCode=r["submit"]["failureCode"],
            failureMessage=r["submit"]["failureMessage"],
        )
        for r in item_results
    ]
    return SubmitSummaryResponse(
        submitSummary=SubmitSummaryPayload(
            batchId=batch_id,
            status=updated_batch["status"],
            items=results,
        )
    )


@router.get("/{batch_id}/images/{image_id}/source")
async def stream_source_image(
    batch_id: str,
    image_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    storage: StorageDependency,
) -> StreamingResponse:
    batch = await _load_owned_batch(database, batch_id, user["_id"])
    image = _find_image_or_404(batch, image_id)
    source_image = dict(image.get("sourceImage") or {})
    bucket = source_image.get("bucket")
    object_key = source_image.get("objectKey")
    if not bucket or not object_key:
        raise ApiError(404, "NOT_FOUND", "Source image not found")

    try:
        image_bytes = storage.get_object(str(bucket), str(object_key))
    except StorageObjectNotFoundError as exc:
        raise ApiError(404, "NOT_FOUND", "Source image not found") from exc

    return StreamingResponse(
        BytesIO(image_bytes),
        media_type=str(source_image.get("contentType") or "application/octet-stream"),
    )


@router.get("/{batch_id}/items/{item_id}/crop")
async def stream_item_crop(
    batch_id: str,
    item_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    storage: StorageDependency,
) -> StreamingResponse:
    batch = await _load_owned_batch(database, batch_id, user["_id"])
    item = _find_item_or_404(batch, item_id)
    crop = dict(item.get("crop") or {})
    bucket = crop.get("bucket")
    object_key = crop.get("objectKey")
    if not bucket or not object_key:
        raise ApiError(404, "CROP_NOT_FOUND", "Crop image not found")

    try:
        image_bytes = storage.get_object(str(bucket), str(object_key))
    except StorageObjectNotFoundError as exc:
        raise ApiError(404, "NOT_FOUND", "Crop image not found") from exc

    return StreamingResponse(
        BytesIO(image_bytes),
        media_type=str(crop.get("contentType") or "application/octet-stream"),
    )
