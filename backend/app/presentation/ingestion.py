from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field
from pymongo.asynchronous.database import AsyncDatabase

from app.domain import IngestionPreviewStatus, ProblemType, normalize_answer, transition_preview_state
from app.infrastructure.config.settings import Settings
from app.infrastructure.storage.mongo import Document
from app.infrastructure.storage.s3 import S3StorageAdapter, StorageObjectNotFoundError
from app.infrastructure.vlm.client import VLMClient, VLMError, recover_stale_preview
from app.presentation.deps import get_app_settings, get_current_user, get_database
from app.presentation.errors import ApiError

router = APIRouter(prefix="/ingestion-previews", tags=["ingestion"])

PREVIEW_TTL = timedelta(hours=24)
DEFAULT_SYNC_WAIT_SECONDS = 25.0
_preview_tasks: dict[str, asyncio.Task[None]] = {}


class PreviewDraftPatchRequest(BaseModel):
    text: str | None = None
    problemType: ProblemType | None = None
    graphDsl: str | None = None
    correctAnswer: str | None = None
    tags: list[str] | None = None


class PreviewDraftPayload(BaseModel):
    text: str | None = None
    problemType: str | None = None
    graphDsl: str | None = None
    correctAnswer: str | None = None
    tags: list[str] = Field(default_factory=list)


class PreviewSourceImagePayload(BaseModel):
    bucket: str
    objectKey: str
    contentType: str | None = None
    sizeBytes: int | None = None
    sha256: str | None = None
    uploadedAt: datetime | None = None


class PreviewExtractionPayload(BaseModel):
    requestModel: str | None = None
    requestStartedAt: datetime | None = None
    requestFinishedAt: datetime | None = None
    success: bool | None = None
    rawText: str | None = None
    rawProblemType: str | None = None
    rawGraphDsl: str | None = None
    rawProviderResponse: dict[str, Any] | None = None
    failureCode: str | None = None
    failureMessage: str | None = None


class PreviewPayload(BaseModel):
    id: str
    status: str
    sourceImage: PreviewSourceImagePayload
    draft: PreviewDraftPayload
    extraction: PreviewExtractionPayload
    createdAt: datetime
    updatedAt: datetime
    expiresAt: datetime


class PreviewResponse(BaseModel):
    preview: PreviewPayload


class ProblemCorrectAnswerPayload(BaseModel):
    display: str
    normalizedText: str
    normalizedSet: list[str] = Field(default_factory=list)
    format: str


class ProblemPayload(BaseModel):
    id: str
    text: str
    problemType: str
    graphDsl: str | None = None
    correctAnswer: ProblemCorrectAnswerPayload
    tags: list[str] = Field(default_factory=list)
    sourceImage: PreviewSourceImagePayload | None = None
    createdAt: datetime
    updatedAt: datetime


class ProblemResponse(BaseModel):
    problem: ProblemPayload


DatabaseDependency = Annotated[AsyncDatabase[Document], Depends(get_database)]
CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]


def get_s3_storage(settings: SettingsDependency) -> S3StorageAdapter:
    return S3StorageAdapter(settings=settings)


def get_vlm_client(settings: SettingsDependency) -> VLMClient:
    return VLMClient(settings=settings)


def get_preview_sync_wait_seconds() -> float:
    return DEFAULT_SYNC_WAIT_SECONDS


S3Dependency = Annotated[S3StorageAdapter, Depends(get_s3_storage)]
VLMDependency = Annotated[VLMClient, Depends(get_vlm_client)]
SyncWaitDependency = Annotated[float, Depends(get_preview_sync_wait_seconds)]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _preview_expires_at(now: datetime | None = None) -> datetime:
    return (now or _utc_now()) + PREVIEW_TTL


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []

    cleaned_tags: list[str] = []
    for tag in tags:
        normalized = tag.strip()
        if normalized and normalized not in cleaned_tags:
            cleaned_tags.append(normalized)
    return cleaned_tags


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _serialize_preview(preview: Mapping[str, Any]) -> PreviewPayload:
    extraction = dict(preview.get("extraction", {}))
    draft = dict(preview.get("editableDraft", {}))
    source_image = dict(preview.get("sourceImage", {}))
    return PreviewPayload.model_validate(
        {
            "id": str(preview["_id"]),
            "status": str(preview["status"]),
            "sourceImage": source_image,
            "draft": {
                "text": draft.get("text"),
                "problemType": _enum_value(draft.get("problemType")),
                "graphDsl": draft.get("graphDsl"),
                "correctAnswer": draft.get("correctAnswer"),
                "tags": list(draft.get("tags", [])),
            },
            "extraction": {
                "requestModel": extraction.get("requestModel"),
                "requestStartedAt": extraction.get("requestStartedAt"),
                "requestFinishedAt": extraction.get("requestFinishedAt"),
                "success": extraction.get("success"),
                "rawText": extraction.get("rawText"),
                "rawProblemType": _enum_value(extraction.get("rawProblemType")),
                "rawGraphDsl": extraction.get("rawGraphDsl"),
                "rawProviderResponse": extraction.get("rawProviderResponse"),
                "failureCode": extraction.get("failureCode"),
                "failureMessage": extraction.get("failureMessage"),
            },
            "createdAt": preview["createdAt"],
            "updatedAt": preview["updatedAt"],
            "expiresAt": preview["expiresAt"],
        }
    )


def _serialize_problem(problem: Mapping[str, Any]) -> ProblemPayload:
    return ProblemPayload.model_validate(
        {
            "id": str(problem["_id"]),
            "text": problem["text"],
            "problemType": _enum_value(problem["problemType"]),
            "graphDsl": problem.get("graphDsl"),
            "correctAnswer": problem["correctAnswer"],
            "tags": list(problem.get("tags", [])),
            "sourceImage": problem.get("sourceImage"),
            "createdAt": problem["createdAt"],
            "updatedAt": problem["updatedAt"],
        }
    )


def _parse_object_id(value: str, *, field_name: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise ApiError(404, "NOT_FOUND", f"{field_name} not found")
    return ObjectId(value)


async def _get_owned_preview(
    database: AsyncDatabase[Document],
    preview_id: str,
    user: Mapping[str, Any],
) -> Document:
    document_id = _parse_object_id(preview_id, field_name="Preview")
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


def _merge_draft_with_extraction(
    existing_draft: Mapping[str, Any] | None,
    *,
    text: str,
    problem_type: str | None,
    graph_dsl: str | None,
) -> dict[str, Any]:
    draft = dict(existing_draft or {})
    return {
        "text": _clean_optional_text(draft.get("text")) or _clean_optional_text(text),
        "problemType": draft.get("problemType") or problem_type,
        "graphDsl": draft.get("graphDsl") if draft.get("graphDsl") is not None else graph_dsl,
        "correctAnswer": _clean_optional_text(draft.get("correctAnswer")),
        "tags": _clean_tags(list(draft.get("tags", []))),
    }


async def _maybe_close_vlm_client(vlm_client: Any) -> None:
    aclose = getattr(vlm_client, "aclose", None)
    if callable(aclose):
        await aclose()


def _forget_preview_task(preview_id: str, task: asyncio.Task[None]) -> None:
    if _preview_tasks.get(preview_id) is task:
        _preview_tasks.pop(preview_id, None)


def _register_preview_task(preview_id: str, task: asyncio.Task[None]) -> None:
    existing = _preview_tasks.get(preview_id)
    if existing is not None and not existing.done():
        existing.cancel()
    _preview_tasks[preview_id] = task
    task.add_done_callback(lambda finished_task: _forget_preview_task(preview_id, finished_task))


async def _wait_for_preview_result(
    task: asyncio.Task[None],
    *,
    timeout_seconds: float,
) -> bool:
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return False
    return True


async def _load_active_extracting_preview(
    database: AsyncDatabase[Document],
    preview_id: ObjectId,
    started_at: datetime,
) -> Document | None:
    preview = await database["ingestion_previews"].find_one({"_id": preview_id})
    if preview is None:
        return None
    if str(preview.get("status")) != IngestionPreviewStatus.EXTRACTING.value:
        return None
    current_started_at = dict(preview.get("extraction", {})).get("requestStartedAt")
    if current_started_at != started_at:
        return None
    return preview


async def _run_extraction_task(
    *,
    database: AsyncDatabase[Document],
    preview_id: ObjectId,
    started_at: datetime,
    s3_storage: S3StorageAdapter,
    vlm_client: VLMClient,
    settings: Settings,
) -> None:
    try:
        preview = await _load_active_extracting_preview(database, preview_id, started_at)
        if preview is None:
            return

        source_image = dict(preview.get("sourceImage", {}))
        image_bytes = s3_storage.get_object(source_image["bucket"], source_image["objectKey"])
        result = await vlm_client.extract(image_base64=base64.b64encode(image_bytes).decode("utf-8"))
        preview = await _load_active_extracting_preview(database, preview_id, started_at)
        if preview is None:
            return

        finished_at = _utc_now()
        draft = _merge_draft_with_extraction(
            preview.get("editableDraft"),
            text=result.text,
            problem_type=result.problem_type,
            graph_dsl=result.graph_dsl,
        )
        await database["ingestion_previews"].update_one(
            {"_id": preview_id},
            {
                "$set": {
                    "status": transition_preview_state(
                        IngestionPreviewStatus.EXTRACTING,
                        IngestionPreviewStatus.READY,
                    ).value,
                    "updatedAt": finished_at,
                    "editableDraft": draft,
                    "extraction": {
                        "requestModel": result.model,
                        "requestStartedAt": started_at,
                        "requestFinishedAt": finished_at,
                        "success": True,
                        "rawText": result.text,
                        "rawProblemType": result.problem_type,
                        "rawGraphDsl": result.graph_dsl,
                        "rawProviderResponse": result.raw_provider_response,
                        "failureCode": None,
                        "failureMessage": None,
                    },
                }
            },
        )
    except (VLMError, StorageObjectNotFoundError) as exc:
        latest_preview = await _load_active_extracting_preview(database, preview_id, started_at)
        if latest_preview is None:
            return

        latest_extraction = dict(latest_preview.get("extraction", {}))
        finished_at = _utc_now()
        await database["ingestion_previews"].update_one(
            {"_id": preview_id},
            {
                "$set": {
                    "status": transition_preview_state(
                        IngestionPreviewStatus.EXTRACTING,
                        IngestionPreviewStatus.VLM_FAILED,
                    ).value,
                    "updatedAt": finished_at,
                    "extraction": {
                        **latest_extraction,
                        "requestModel": settings.vlm_model,
                        "requestStartedAt": started_at,
                        "requestFinishedAt": finished_at,
                        "success": False,
                        "failureCode": exc.code if isinstance(exc, VLMError) else "source-image-missing",
                        "failureMessage": str(exc),
                        "rawProviderResponse": exc.raw_provider_response if isinstance(exc, VLMError) else None,
                    },
                }
            },
        )
    finally:
        await _maybe_close_vlm_client(vlm_client)


async def _start_extraction(
    *,
    database: AsyncDatabase[Document],
    preview: Document,
    vlm_client: VLMClient,
    s3_storage: S3StorageAdapter,
    settings: Settings,
) -> tuple[Document, asyncio.Task[None]]:
    started_at = _utc_now()
    transition_preview_state(
        IngestionPreviewStatus(str(preview["status"])),
        IngestionPreviewStatus.EXTRACTING,
    )
    extracting_preview = {
        **preview,
        "status": IngestionPreviewStatus.EXTRACTING.value,
        "updatedAt": started_at,
        "extraction": {
            **dict(preview.get("extraction", {})),
            "requestModel": settings.vlm_model,
            "requestStartedAt": started_at,
            "requestFinishedAt": None,
            "success": None,
            "failureCode": None,
            "failureMessage": None,
        },
    }
    await database["ingestion_previews"].update_one(
        {"_id": preview["_id"]},
        {
            "$set": {
                "status": extracting_preview["status"],
                "updatedAt": extracting_preview["updatedAt"],
                "extraction": extracting_preview["extraction"],
            }
        },
    )

    task = asyncio.create_task(
        _run_extraction_task(
            database=database,
            preview_id=preview["_id"],
            started_at=started_at,
            s3_storage=s3_storage,
            vlm_client=vlm_client,
            settings=settings,
        )
    )
    _register_preview_task(str(preview["_id"]), task)
    refreshed = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
    if refreshed is None:
        raise ApiError(404, "NOT_FOUND", "Preview not found")
    return refreshed, task


async def _recover_preview_if_stale(
    database: AsyncDatabase[Document],
    preview: Document,
    settings: Settings,
) -> Document:
    recovered = recover_stale_preview(preview, extracting_window_seconds=settings.vlm_timeout_seconds)
    if recovered is None:
        return preview

    task = _preview_tasks.pop(str(preview["_id"]), None)
    if task is not None and not task.done():
        task.cancel()

    await database["ingestion_previews"].update_one(
        {"_id": preview["_id"]},
        {
            "$set": {
                "status": recovered["status"],
                "updatedAt": recovered["updatedAt"],
                "extraction": recovered["extraction"],
            }
        },
    )
    refreshed = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
    if refreshed is None:
        raise ApiError(404, "NOT_FOUND", "Preview not found")
    return refreshed


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

    now = _utc_now()
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
        "expiresAt": _preview_expires_at(now),
    }
    await database["ingestion_previews"].insert_one(preview)

    extracting_preview, task = await _start_extraction(
        database=database,
        preview=preview,
        vlm_client=vlm_client,
        s3_storage=s3_storage,
        settings=settings,
    )
    completed = await _wait_for_preview_result(task, timeout_seconds=sync_wait_seconds)
    current_preview = extracting_preview
    if completed:
        refreshed = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
        if refreshed is not None:
            current_preview = refreshed
    return PreviewResponse(preview=_serialize_preview(current_preview))


@router.get("/{preview_id}", response_model=PreviewResponse)
async def get_preview(
    preview_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
) -> PreviewResponse:
    preview = await _get_owned_preview(database, preview_id, user)
    if str(preview["status"]) == IngestionPreviewStatus.EXTRACTING.value:
        preview = await _recover_preview_if_stale(database, preview, settings)
    return PreviewResponse(preview=_serialize_preview(preview))


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
            draft[key] = _clean_tags(value)
        elif key in {"text", "correctAnswer"}:
            draft[key] = _clean_optional_text(value)
        else:
            draft[key] = value

    now = _utc_now()
    await database["ingestion_previews"].update_one(
        {"_id": preview["_id"]},
        {
            "$set": {
                "editableDraft": {
                    "text": draft.get("text"),
                    "problemType": draft.get("problemType"),
                    "graphDsl": draft.get("graphDsl"),
                    "correctAnswer": draft.get("correctAnswer"),
                    "tags": _clean_tags(list(draft.get("tags", []))),
                },
                "updatedAt": now,
                "expiresAt": _preview_expires_at(now),
            }
        },
    )
    refreshed = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
    if refreshed is None:
        raise ApiError(404, "NOT_FOUND", "Preview not found")
    return PreviewResponse(preview=_serialize_preview(refreshed))


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
    extracting_preview, task = await _start_extraction(
        database=database,
        preview=preview,
        vlm_client=vlm_client,
        s3_storage=s3_storage,
        settings=settings,
    )
    completed = await _wait_for_preview_result(task, timeout_seconds=sync_wait_seconds)
    current_preview = extracting_preview
    if completed:
        refreshed = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
        if refreshed is not None:
            current_preview = refreshed
    return PreviewResponse(preview=_serialize_preview(current_preview))


@router.post("/{preview_id}/confirm", response_model=ProblemResponse, status_code=201)
async def confirm_preview(
    preview_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> ProblemResponse:
    preview = await _get_owned_preview(database, preview_id, user)
    _ensure_status(preview, {IngestionPreviewStatus.READY.value})

    draft = dict(preview.get("editableDraft", {}))
    text = _clean_optional_text(draft.get("text"))
    problem_type_value = draft.get("problemType")
    correct_answer = _clean_optional_text(draft.get("correctAnswer"))
    if text is None or problem_type_value is None or correct_answer is None:
        raise ApiError(400, "INVALID_PREVIEW", "Preview draft is missing required fields")

    problem_type = ProblemType(problem_type_value)
    normalized_answer = normalize_answer(correct_answer, problem_type)
    now = _utc_now()
    transition_preview_state(IngestionPreviewStatus.READY, IngestionPreviewStatus.CONFIRMED)
    problem: Document = {
        "_id": ObjectId(),
        "userId": user["_id"],
        "text": text,
        "problemType": problem_type.value,
        "graphDsl": draft.get("graphDsl"),
        "correctAnswer": normalized_answer.model_dump(),
        "tags": _clean_tags(list(draft.get("tags", []))),
        "sourceImage": dict(preview.get("sourceImage", {})),
        "origin": {
            "previewId": str(preview["_id"]),
            "vlmModel": dict(preview.get("extraction", {})).get("requestModel"),
            "rawExtractedText": dict(preview.get("extraction", {})).get("rawText"),
            "rawExtractedProblemType": _enum_value(dict(preview.get("extraction", {})).get("rawProblemType")),
            "rawExtractedGraphDsl": dict(preview.get("extraction", {})).get("rawGraphDsl"),
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
    await database["problems"].insert_one(problem)
    await database["ingestion_previews"].update_one(
        {"_id": preview["_id"]},
        {"$set": {"status": IngestionPreviewStatus.CONFIRMED.value, "updatedAt": now}},
    )
    return ProblemResponse(problem=_serialize_problem(problem))
