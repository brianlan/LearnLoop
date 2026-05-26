from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any, Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.domain import IngestionPreviewStatus, ProblemType, normalize_answer
from app.infrastructure.config.settings import Settings
from app.infrastructure.storage.mongo import Document
from app.infrastructure.storage.s3 import StorageObjectNotFoundError
from app.infrastructure.vlm.client import VLMClient
from app.presentation.deps import (
    DatabaseDependency,
    StorageDependency,
    create_ingestion_vlm_client,
    get_app_settings,
    get_current_user,
    get_s3_storage,
)
from app.presentation.errors import ApiError
from app.presentation.helpers import normalize_tags, parse_object_id
from app.presentation.ingestion_serialization import ProblemResponse, PreviewResponse, serialize_preview, serialize_problem, _enum_value
from app.presentation.tags import _register_tags
from app.presentation.solution_generation import enqueue_solution_generation_task_for_problem
from app.presentation.ingestion_workflow import (
    DEFAULT_SYNC_WAIT_SECONDS,
    clean_optional_text,
    preview_expires_at,
    preview_tasks,
    recover_preview_if_stale,
    start_extraction,
    utc_now,
    wait_for_preview_result,
)

router = APIRouter(prefix="/ingestion-previews", tags=["ingestion"])

class PreviewDraftPatchRequest(BaseModel):
    text: str | None = None
    problemType: ProblemType | None = None
    graphDsl: str | None = None
    correctAnswer: str | None = None
    tags: list[str] | None = None


CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]


def get_preview_sync_wait_seconds() -> float:
    return DEFAULT_SYNC_WAIT_SECONDS


get_ingestion_vlm_client = create_ingestion_vlm_client

S3Dependency = StorageDependency
VLMDependency = Annotated[VLMClient, Depends(get_ingestion_vlm_client)]
SyncWaitDependency = Annotated[float, Depends(get_preview_sync_wait_seconds)]


async def _get_owned_preview(
    database: AsyncDatabase[Document],
    preview_id: str,
    user: Mapping[str, Any],
) -> Document:
    document_id = parse_object_id(preview_id, resource_name="Preview")
    preview = await database["ingestion_previews"].find_one(
        {"_id": document_id, "userId": user["_id"]}
    )
    if preview is None:
        raise ApiError(404, "NOT_FOUND", "Preview not found")
    return preview


def _ensure_status(preview: Mapping[str, Any], allowed: set[str]) -> None:
    if str(preview["status"]) not in allowed:
        raise ApiError(409, "INVALID_PREVIEW_STATE", "Preview is not in a valid state for this operation")


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


@router.post("", response_model=PreviewResponse, status_code=201)
async def create_preview(
    image: Annotated[UploadFile, File(...)],
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
    s3_storage: S3Dependency,
    vlm_client: VLMDependency,
    sync_wait_seconds: SyncWaitDependency,
) -> PreviewResponse:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise ApiError(400, "INVALID_IMAGE", "Uploaded file must be an image")

    image_bytes = await image.read()
    if not image_bytes:
        raise ApiError(400, "INVALID_IMAGE", "Uploaded image is empty")

    now = utc_now()
    object_key = s3_storage.build_object_key(str(user["_id"]), _guess_extension(image))
    s3_storage.put_object(settings.s3_bucket, object_key, image_bytes, image.content_type)

    preview: Document = {
        "_id": ObjectId(),
        "userId": user["_id"],
        "status": IngestionPreviewStatus.UPLOADED.value,
        "sourceImage": {
            "bucket": settings.s3_bucket,
            "objectKey": object_key,
            "contentType": image.content_type,
            "sizeBytes": len(image_bytes),
            "sha256": hashlib.sha256(image_bytes).hexdigest(),
            "uploadedAt": now,
        },
        "extraction": {
            "requestModel": None,
            "requestStartedAt": None,
            "requestFinishedAt": None,
            "success": None,
            "rawText": None,
            "rawProblemType": None,
            "rawGraphDsl": None,
            "rawProviderResponse": None,
            "failureCode": None,
            "failureMessage": None,
        },
        "editableDraft": {
            "text": None,
            "problemType": None,
            "graphDsl": None,
            "correctAnswer": None,
            "tags": [],
        },
        "createdAt": now,
        "updatedAt": now,
        "expiresAt": preview_expires_at(now),
    }
    await database["ingestion_previews"].insert_one(preview)

    extracting_preview, task = await start_extraction(
        database=database,
        preview=preview,
        vlm_client=vlm_client,
        s3_storage=s3_storage,
        settings=settings,
    )
    completed = await wait_for_preview_result(task, timeout_seconds=sync_wait_seconds)
    current_preview = extracting_preview
    if completed:
        refreshed = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
        if refreshed is not None:
            current_preview = refreshed
    return PreviewResponse(preview=serialize_preview(current_preview))


@router.get("/{preview_id}", response_model=PreviewResponse)
async def get_preview(
    preview_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
) -> PreviewResponse:
    preview = await _get_owned_preview(database, preview_id, user)
    if str(preview["status"]) == IngestionPreviewStatus.EXTRACTING.value:
        preview = await recover_preview_if_stale(database, preview, settings)
    return PreviewResponse(preview=serialize_preview(preview))


@router.patch("/{preview_id}", response_model=PreviewResponse)
async def patch_preview(
    preview_id: str,
    payload: PreviewDraftPatchRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> PreviewResponse:
    preview = await _get_owned_preview(database, preview_id, user)
    _ensure_status(preview, {IngestionPreviewStatus.READY.value, IngestionPreviewStatus.VLM_FAILED.value})

    draft = dict(preview.get("editableDraft", {}))
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if key == "tags":
            draft[key] = normalize_tags(value)
        elif key in {"text", "correctAnswer"}:
            draft[key] = clean_optional_text(value)
        else:
            draft[key] = value

    now = utc_now()
    await database["ingestion_previews"].update_one(
        {"_id": preview["_id"]},
        {
            "$set": {
                "editableDraft": {
                    "text": draft.get("text"),
                    "problemType": draft.get("problemType"),
                    "graphDsl": draft.get("graphDsl"),
                    "correctAnswer": draft.get("correctAnswer"),
                    "tags": normalize_tags(list(draft.get("tags", []))),
                },
                "updatedAt": now,
                "expiresAt": preview_expires_at(now),
            }
        },
    )
    refreshed = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
    if refreshed is None:
        raise ApiError(404, "NOT_FOUND", "Preview not found")
    return PreviewResponse(preview=serialize_preview(refreshed))


@router.post("/{preview_id}/retry", response_model=PreviewResponse)
async def retry_preview(
    preview_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
    s3_storage: S3Dependency,
    vlm_client: VLMDependency,
    sync_wait_seconds: SyncWaitDependency,
) -> PreviewResponse:
    preview = await _get_owned_preview(database, preview_id, user)
    _ensure_status(preview, {IngestionPreviewStatus.VLM_FAILED.value})
    extracting_preview, task = await start_extraction(
        database=database,
        preview=preview,
        vlm_client=vlm_client,
        s3_storage=s3_storage,
        settings=settings,
    )
    completed = await wait_for_preview_result(task, timeout_seconds=sync_wait_seconds)
    current_preview = extracting_preview
    if completed:
        refreshed = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
        if refreshed is not None:
            current_preview = refreshed
    return PreviewResponse(preview=serialize_preview(current_preview))


@router.post("/{preview_id}/confirm", response_model=ProblemResponse, status_code=201)
async def confirm_preview(
    preview_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> ProblemResponse:
    now = utc_now()
    preview_oid = parse_object_id(preview_id, resource_name="Preview")
    previews = database["ingestion_previews"]
    claimed_preview: Document | None
    try:
        claimed_preview = await previews.find_one_and_update(
            {
                "_id": preview_oid,
                "userId": user["_id"],
                "status": {
                    "$in": [
                        IngestionPreviewStatus.READY.value,
                        IngestionPreviewStatus.VLM_FAILED.value,
                    ]
                },
            },
            {"$set": {"status": IngestionPreviewStatus.CONFIRMED.value, "updatedAt": now}},
        )
    except AttributeError:
        claimed_preview = await previews.find_one({"_id": preview_oid, "userId": user["_id"]})
        if claimed_preview is not None and str(claimed_preview.get("status")) in {
            IngestionPreviewStatus.READY.value,
            IngestionPreviewStatus.VLM_FAILED.value,
        }:
            await previews.update_one(
                {"_id": preview_oid},
                {"$set": {"status": IngestionPreviewStatus.CONFIRMED.value, "updatedAt": now}},
            )
    if claimed_preview is None:
        preview_exists = await previews.find_one({"_id": preview_oid, "userId": user["_id"]})
        if preview_exists is None:
            raise ApiError(404, "NOT_FOUND", "Preview not found")
        raise ApiError(
            409,
            "PREVIEW_ALREADY_CONFIRMED",
            "Preview has already been confirmed or is no longer available",
        )

    draft = dict(claimed_preview.get("editableDraft", {}))
    text = clean_optional_text(draft.get("text"))
    problem_type_value = draft.get("problemType")
    correct_answer = clean_optional_text(draft.get("correctAnswer"))
    if text is None or problem_type_value is None or correct_answer is None:
        await previews.update_one(
            {"_id": preview_oid},
            {"$set": {"status": claimed_preview["status"], "updatedAt": now}},
        )
        raise ApiError(400, "INVALID_PREVIEW", "Preview draft is missing required fields")

    problem_type = ProblemType(problem_type_value)
    normalized_answer = normalize_answer(correct_answer, problem_type)
    problem: Document = {
        "_id": ObjectId(),
        "userId": user["_id"],
        "text": text,
        "problemType": problem_type.value,
        "graphDsl": draft.get("graphDsl"),
        "correctAnswer": normalized_answer.model_dump(),
        "tags": normalize_tags(list(draft.get("tags", []))),
        "sourceImage": dict(claimed_preview.get("sourceImage", {})),
        "origin": {
            "previewId": str(claimed_preview["_id"]),
            "vlmModel": dict(claimed_preview.get("extraction", {})).get("requestModel"),
            "rawExtractedText": dict(claimed_preview.get("extraction", {})).get("rawText"),
            "rawExtractedProblemType": _enum_value(
                dict(claimed_preview.get("extraction", {})).get("rawProblemType")
            ),
            "rawExtractedGraphDsl": dict(claimed_preview.get("extraction", {})).get("rawGraphDsl"),
        },
        "tracking": {
            "exposureCount": 0,
            "correctCount": 0,
            "failedCount": 0,
            "lastTestedAt": None,
            "lastAttemptCorrect": None,
        },
        "isDeleted": False,
        "deletedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        await database["problems"].insert_one(problem)
        await enqueue_solution_generation_task_for_problem(database, problem, now=now)
    except Exception:
        delete_problem = getattr(database["problems"], "delete_one", None)
        if callable(delete_problem):
            try:
                await delete_problem({"_id": problem["_id"]})
            except Exception:
                pass
        await previews.update_one(
            {"_id": preview_oid},
            {"$set": {"status": claimed_preview["status"], "updatedAt": now}},
        )
        raise ApiError(500, "PROBLEM_CREATION_FAILED", "Failed to create problem. Please retry confirmation.")
    await _register_tags(database, user["_id"], list(draft.get("tags", [])))
    return ProblemResponse(problem=serialize_problem(problem))


@router.get("/{preview_id}/image")
async def stream_preview_image(
    preview_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    s3_storage: S3Dependency,
) -> StreamingResponse:
    preview = await _get_owned_preview(database, preview_id, user)
    source_image = dict(preview.get("sourceImage") or {})
    bucket = source_image.get("bucket")
    object_key = source_image.get("objectKey")
    if not bucket or not object_key:
        raise ApiError(404, "NOT_FOUND", "Preview image not found")

    try:
        image_bytes = s3_storage.get_object(str(bucket), str(object_key))
    except StorageObjectNotFoundError as exc:
        raise ApiError(404, "NOT_FOUND", "Preview image not found") from exc

    return StreamingResponse(
        BytesIO(image_bytes),
        media_type=str(source_image.get("contentType") or "application/octet-stream"),
    )
