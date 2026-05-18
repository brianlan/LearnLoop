from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from inspect import isawaitable
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.domain import IngestionPreviewStatus, recover_stale_preview, transition_preview_state
from app.infrastructure.config.settings import Settings
from app.infrastructure.storage.mongo import Document
from app.infrastructure.storage.s3 import S3StorageAdapter, StorageObjectNotFoundError
from app.infrastructure.vlm.client import VLMClient, VLMError
from app.presentation.errors import ApiError
from app.presentation.helpers import normalize_tags

PREVIEW_TTL = timedelta(hours=24)
DEFAULT_SYNC_WAIT_SECONDS = 25.0

# TODO(production): _preview_tasks is process-local and will not survive restarts
# or work across multiple workers. Replace with a durable job queue (e.g. Celery,
# Dramatiq) or store extraction state in Mongo and poll from a background worker.
_preview_tasks: dict[str, asyncio.Task[None]] = {}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _preview_expires_at(now: datetime | None = None) -> datetime:
    return (now or _utc_now()) + PREVIEW_TTL


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


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
        "tags": normalize_tags(list(draft.get("tags", []))),
    }


async def _maybe_close_vlm_client(vlm_client: Any) -> None:
    aclose = getattr(vlm_client, "aclose", None)
    if callable(aclose):
        maybe_awaitable = aclose()
        if isawaitable(maybe_awaitable):
            await maybe_awaitable


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


def _is_same_instant(a: datetime | None, b: datetime | None, *, tolerance_seconds: float = 1.0) -> bool:
    if a is None or b is None:
        return a is b
    if a.tzinfo is not None and b.tzinfo is None:
        b = b.replace(tzinfo=a.tzinfo)
    elif a.tzinfo is None and b.tzinfo is not None:
        a = a.replace(tzinfo=b.tzinfo)
    return abs((a - b).total_seconds()) < tolerance_seconds


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
    if not _is_same_instant(current_started_at, started_at):
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
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        result = await vlm_client.extract(image_base64=image_b64)
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
    recovered = recover_stale_preview(
        preview,
        extracting_window_seconds=settings.preview_extracting_window_seconds,
    )
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
