from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, NamedTuple

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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
    edit_variation_candidate,
    get_active_batch_for_user,
    get_batch,
    is_batch_expired,
    mark_item_deleted,
    renew_submit_reservation,
    request_variation_generation,
    request_variation_revalidation,
    reset_item_for_retry,
    reserve_items_for_original_submit,
    save_image_boxes_and_subject,
    save_image_detection_failure,
    save_image_detection_success,
    start_image_detection,
    submit_items_and_complete_batch,
    undo_item_deletion,
    update_item_draft,
    update_item_draft_variant,
)
from app.infrastructure.storage.mongo import Document
from app.infrastructure.vlm.base_client import BaseVLMError
from app.infrastructure.vlm.variant_client import (
    VariantVLMError,
    build_variant_generator_vlm_client,
    build_variant_helper_vlm_client,
    build_variant_validator_vlm_client,
)
from app.problem_variation import (
    GenerationInProgressError,
    IngestionMode,
    InvalidVariationStateError,
    RevisionMismatchError,
    VariationNotFoundError,
    is_variant_mode,
)
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
    guess_upload_extension,
    normalize_tags,
    parse_object_id,
    stream_storage_metadata,
)
from app.presentation.problem_creation import create_problem_from_draft

router = APIRouter(prefix="/ingestion-batches", tags=["bulk-ingestion"])


def _find_image_or_404(batch: Document, image_id: str) -> dict[str, Any]:
    for image in batch.get("images", []):
        if image.get("imageId") == image_id:
            return image
    raise ApiError(404, "NOT_FOUND", "Image not found")


class SaveBoxesRequest(BaseModel):
    subject: str | None = None
    boxes: list[dict[str, Any]]


def _raise_variation_conflict(exc: Exception) -> ApiError:
    if isinstance(exc, VariationNotFoundError):
        return ApiError(404, "NOT_FOUND", str(exc))
    if isinstance(exc, RevisionMismatchError):
        return ApiError(409, "REVISION_MISMATCH", str(exc))
    if isinstance(exc, GenerationInProgressError):
        return ApiError(409, "VARIATION_BUSY", str(exc))
    return ApiError(409, "INVALID_VARIATION_STATE", str(exc))


def _require_variant_mode(batch: Document) -> None:
    if not is_variant_mode(batch.get("ingestionMode") or "original"):
        raise ApiError(
            409,
            "INGESTION_MODE_MISMATCH",
            "Batch is not in a variant ingestion mode",
        )


def _build_variant_clients(settings: Settings):
    """Build the four role clients; an unconfigured profile is an explicit 409."""
    try:
        return (
            build_variant_generator_vlm_client(settings),
            build_variant_validator_vlm_client(settings),
            build_variant_validator_vlm_client(settings, second=True),
            build_variant_helper_vlm_client(settings),
        )
    except VariantVLMError as exc:
        raise ApiError(409, exc.code, str(exc)) from exc


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
        return [_UploadPayload(image_bytes, content_type, width, height, guess_upload_extension(upload))]

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


class CreateBatchRequest(BaseModel):
    ingestionMode: IngestionMode = IngestionMode.ORIGINAL


@router.post("", response_model=BatchResponse, status_code=201)
async def create_batch(
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
    request: CreateBatchRequest | None = None,
) -> BatchResponse:
    mode = request.ingestionMode if request is not None else IngestionMode.ORIGINAL
    batch = await create_batch_repo(database, user["_id"], settings, ingestion_mode=mode)
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
    expectedRevision: int | None = None


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
    batch = await _load_owned_batch(database, batch_id, user["_id"])

    draft_update = request.model_dump(exclude_unset=True, exclude={"expectedRevision"})
    if "tags" in draft_update:
        draft_update["tags"] = normalize_tags(draft_update["tags"])

    try:
        if is_variant_mode(batch.get("ingestionMode") or "original"):
            if request.expectedRevision is None:
                raise ApiError(
                    400,
                    "REVISION_REQUIRED",
                    "expectedRevision is required for edits in a variant batch",
                )
            await update_item_draft_variant(
                database,
                batch_id,
                user["_id"],
                item_id,
                draft_update=draft_update,
                expected_revision=request.expectedRevision,
                now=datetime.now(UTC),
            )
        else:
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
    except (
        VariationNotFoundError,
        RevisionMismatchError,
        GenerationInProgressError,
        InvalidVariationStateError,
    ) as exc:
        raise _raise_variation_conflict(exc) from exc

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


class VariationOriginalPayload(BaseModel):
    """The currently reviewed source draft, confirmed by Generate."""

    text: str = Field(min_length=1)
    problemType: str = Field(min_length=1)
    correctAnswer: str = Field(min_length=1)
    graphDsl: str | None = None
    subject: str | None = None


class VariationGenerateRequest(BaseModel):
    expectedRevision: int
    original: VariationOriginalPayload


class VariationCandidateUpdateRequest(BaseModel):
    expectedRevision: int
    text: str | None = None
    problemType: str | None = None
    graphDsl: str | None = None
    correctAnswer: str | None = None
    tags: list[str] | None = None


class VariationRevalidateRequest(BaseModel):
    expectedRevision: int


@router.post(
    "/{batch_id}/items/{item_id}/variation/generate",
    response_model=BatchResponse,
    status_code=202,
)
async def generate_variation(
    batch_id: str,
    item_id: str,
    request: VariationGenerateRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
) -> BatchResponse:
    batch = await _load_owned_batch(database, batch_id, user["_id"])
    _require_variant_mode(batch)
    item = _find_item_or_404(batch, item_id)

    # Profiles are checked before queueing so unconfigured deployments never
    # create work that can never run.
    _build_variant_clients(settings)

    draft = dict(item.get("draft") or {})
    original = {
        "text": request.original.text,
        "problemType": request.original.problemType,
        "graphDsl": request.original.graphDsl,
        "correctAnswer": request.original.correctAnswer,
        "subject": request.original.subject or draft.get("subject"),
    }

    try:
        await request_variation_generation(
            database,
            batch_id,
            user["_id"],
            item_id,
            original=original,
            expected_revision=request.expectedRevision,
            now=datetime.now(UTC),
        )
    except (
        VariationNotFoundError,
        RevisionMismatchError,
        GenerationInProgressError,
        InvalidVariationStateError,
    ) as exc:
        raise _raise_variation_conflict(exc) from exc

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch, include_deleted=True))


@router.patch(
    "/{batch_id}/items/{item_id}/variation/candidate",
    response_model=BatchResponse,
)
async def edit_variation_candidate_endpoint(
    batch_id: str,
    item_id: str,
    request: VariationCandidateUpdateRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> BatchResponse:
    batch = await _load_owned_batch(database, batch_id, user["_id"])
    _require_variant_mode(batch)

    candidate_update = request.model_dump(exclude_unset=True, exclude={"tags", "expectedRevision"})
    if not candidate_update and request.tags is None:
        raise ApiError(400, "INVALID_REQUEST", "No candidate changes provided")
    tags = normalize_tags(request.tags) if request.tags is not None else None

    try:
        await edit_variation_candidate(
            database,
            batch_id,
            user["_id"],
            item_id,
            candidate_update=candidate_update,
            tags=tags,
            expected_revision=request.expectedRevision,
            now=datetime.now(UTC),
        )
    except (
        VariationNotFoundError,
        RevisionMismatchError,
        GenerationInProgressError,
        InvalidVariationStateError,
    ) as exc:
        raise _raise_variation_conflict(exc) from exc

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch, include_deleted=True))


@router.post(
    "/{batch_id}/items/{item_id}/variation/revalidate",
    response_model=BatchResponse,
    status_code=202,
)
async def revalidate_variation(
    batch_id: str,
    item_id: str,
    request: VariationRevalidateRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
) -> BatchResponse:
    batch = await _load_owned_batch(database, batch_id, user["_id"])
    _require_variant_mode(batch)
    _build_variant_clients(settings)

    try:
        await request_variation_revalidation(
            database,
            batch_id,
            user["_id"],
            item_id,
            expected_revision=request.expectedRevision,
            now=datetime.now(UTC),
        )
    except (
        VariationNotFoundError,
        RevisionMismatchError,
        GenerationInProgressError,
        InvalidVariationStateError,
    ) as exc:
        raise _raise_variation_conflict(exc) from exc

    updated_batch = await get_batch(database, batch_id, user["_id"])
    if updated_batch is None:
        raise ApiError(404, "NOT_FOUND", "Batch not found")
    return BatchResponse(**serialize_batch(updated_batch, include_deleted=True))


def _submit_item_failure(item_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "itemId": item_id,
        "status": ItemState.SUBMIT_FAILED.value,
        "submit": {
            "submittedProblemId": None,
            "success": False,
            "failureCode": code,
            "failureMessage": message,
        },
    }


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
    candidate_item_ids = [
        item["itemId"]
        for item in batch.get("items", [])
        if item.get("status") == ItemState.READY.value
        # Variant items never enter the original-draft submit path: pending
        # or invalid variants must not save the confirmed source by accident
        # (the dedicated variant save integration lands with #614).
        and not (
            (item.get("variation") or {}).get("original")
            or (item.get("variation") or {}).get("status") not in (None, "not-requested")
        )
    ]
    # Atomically reserve the still-eligible items before creating problems.
    # Generate refuses reserved items, so a variant can never be confirmed
    # into the submit window, and the completion only records results for
    # items this token reserved.
    reservation_token, reserved_ids = await reserve_items_for_original_submit(
        database, batch_id, user["_id"], candidate_item_ids, now=now,
    )
    reserved_items = [
        item for item in batch.get("items", []) if item["itemId"] in set(reserved_ids)
    ]

    item_results: list[dict[str, Any]] = []
    for item in reserved_items:
        # Prove the reservation is still owned immediately before the
        # side effect; a lost reservation fails the item closed instead of
        # creating an original problem for a source Generate may already own.
        owned = await renew_submit_reservation(
            database, batch_id, user["_id"], item["itemId"],
            token=reservation_token, now=datetime.now(UTC),
        )
        if not owned:
            item_results.append(_submit_item_failure(
                item["itemId"],
                "RESERVATION_LOST",
                "Original-submit reservation was lost before problem creation",
            ))
            continue
        try:
            # The guard re-proves ownership atomically right before the
            # problem insert: if the reservation was lost while creation
            # ran (stall, item deletion, batch expiry), nothing is written.
            async def ownership_guard() -> bool:
                return await renew_submit_reservation(
                    database, batch_id, user["_id"], item["itemId"],
                    token=reservation_token, now=datetime.now(UTC),
                )

            problem = await create_problem_from_draft(
                database,
                user["_id"],
                draft=item.get("draft"),
                source_image=item.get("crop"),
                origin=item.get("origin"),
                now=now,
                ownership_guard=ownership_guard,
            )
        except ApiError as exc:
            item_results.append(_submit_item_failure(
                item["itemId"], exc.code, exc.message,
            ))
            continue
        # Ownership proof again after the side effect: a loss between the
        # insert and the recording still fails closed, so a stale original
        # save is never recorded as a submission.
        if not await renew_submit_reservation(
            database, batch_id, user["_id"], item["itemId"],
            token=reservation_token, now=datetime.now(UTC),
        ):
            item_results.append(_submit_item_failure(
                item["itemId"],
                "RESERVATION_LOST",
                "Original-submit reservation was lost during problem creation",
            ))
            continue
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

    updated_batch = await submit_items_and_complete_batch(
        database,
        batch_id,
        user["_id"],
        item_results=item_results,
        reservation_token=reservation_token,
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
    return stream_storage_metadata(
        source_image,
        storage,
        missing_metadata_code="NOT_FOUND",
        missing_metadata_message="Source image not found",
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
    return stream_storage_metadata(
        crop,
        storage,
        missing_metadata_code="CROP_NOT_FOUND",
        missing_metadata_message="Crop image not found",
    )
