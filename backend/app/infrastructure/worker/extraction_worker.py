from __future__ import annotations

import asyncio
import base64
import logging
from datetime import UTC, datetime
from typing import Any

from app.domain.ingestion import BatchState, ItemState
from app.domain.normalization import normalize_extracted_problem_text
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion.crop import crop_image_to_box
from app.infrastructure.ingestion.documents import build_crop_image
from app.infrastructure.ingestion.repository import (
    INGESTION_BATCHES_COLLECTION,
    claim_item,
    save_item_extraction_failure,
    save_item_extraction_success,
)
from app.infrastructure.storage.s3 import S3StorageAdapter
from app.infrastructure.vlm.base_client import BaseVLMError
from app.infrastructure.vlm.client import VLMClient

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _failed_extraction(
    code: str,
    message: str,
    started_at: datetime,
    finished_at: datetime,
    *,
    raw_provider_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "model": None,
        "rawText": None,
        "rawProblemType": None,
        "rawGraphDsl": None,
        "rawProviderResponse": raw_provider_response,
        "failureCode": code,
        "failureMessage": message,
        "requestStartedAt": started_at,
        "requestFinishedAt": finished_at,
    }


def _find_image(batch: dict[str, Any], image_id: str) -> dict[str, Any] | None:
    for image in batch.get("images", []):
        if image.get("imageId") == image_id:
            return image
    return None


def _content_type_extension(content_type: str) -> str:
    if content_type == "image/jpeg":
        return ".jpg"
    return ".png"


def _is_item_claimable(item: dict[str, Any], now: datetime) -> bool:
    status = item.get("status")
    if status == ItemState.QUEUED.value:
        return True
    if status == ItemState.EXTRACTING.value:
        lease_until = item.get("leaseUntil")
        if lease_until is None:
            return True
        if isinstance(lease_until, datetime):
            if lease_until.tzinfo is None:
                lease_until = lease_until.replace(tzinfo=UTC)
            return lease_until <= now
    return False


async def _select_client(
    subject: str,
    math_client: VLMClient,
    english_client: VLMClient,
) -> VLMClient:
    if subject == "math":
        return math_client
    if subject == "english":
        return english_client
    raise ValueError(f"Unsupported subject: {subject}")


async def process_item(
    item: dict[str, Any],
    batch: dict[str, Any],
    database: Any,
    storage: S3StorageAdapter,
    math_client: VLMClient,
    english_client: VLMClient,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> None:
    """Crop, extract, and persist results for a single claimed item."""
    current = now or _utc_now()
    batch_id = batch["_id"]
    user_id = batch["userId"]
    item_id = item["itemId"]
    image_id = item["imageId"]

    image = _find_image(batch, image_id)
    if image is None:
        await save_item_extraction_failure(
            database,
            batch_id,
            user_id,
            item_id,
            extraction=_failed_extraction(
                "source-image-missing",
                "Source image not found in batch",
                current,
                current,
            ),
            now=current,
        )
        return

    source_image = image.get("sourceImage") or {}
    subject = image.get("subject")
    if not subject:
        await save_item_extraction_failure(
            database,
            batch_id,
            user_id,
            item_id,
            extraction=_failed_extraction(
                "missing-subject",
                "Image subject is not set",
                current,
                current,
            ),
            now=current,
        )
        return

    box = item.get("box")
    if not box:
        await save_item_extraction_failure(
            database,
            batch_id,
            user_id,
            item_id,
            extraction=_failed_extraction(
                "missing-box",
                "Item has no crop box",
                current,
                current,
            ),
            now=current,
        )
        return

    try:
        source_bytes = storage.get_object(source_image["bucket"], source_image["objectKey"])
    except Exception as exc:
        logger.exception("Failed to load source image for extraction")
        await save_item_extraction_failure(
            database,
            batch_id,
            user_id,
            item_id,
            extraction=_failed_extraction(
                "storage-read-failed",
                str(exc),
                current,
                current,
            ),
            now=current,
        )
        return

    try:
        crop_bytes, crop_content_type, crop_width, crop_height = crop_image_to_box(source_bytes, box)
    except Exception as exc:
        logger.exception("Failed to crop source image")
        await save_item_extraction_failure(
            database,
            batch_id,
            user_id,
            item_id,
            extraction=_failed_extraction(
                "crop-failed",
                str(exc),
                current,
                current,
            ),
            now=current,
        )
        return

    crop_extension = _content_type_extension(crop_content_type)
    crop_object_key = storage.build_object_key(
        str(user_id), crop_extension, category="ingestion/crops"
    )
    storage.put_object(settings.s3_bucket, crop_object_key, crop_bytes, crop_content_type)
    crop_meta = build_crop_image(
        bucket=settings.s3_bucket,
        object_key=crop_object_key,
        content_type=crop_content_type,
        size_bytes=len(crop_bytes),
        width=crop_width,
        height=crop_height,
        uploaded_at=current,
    )

    try:
        client = await _select_client(subject, math_client, english_client)
        result = await client.extract(image_base64=base64.b64encode(crop_bytes).decode())
    except BaseVLMError as exc:
        finished_at = _utc_now()
        await save_item_extraction_failure(
            database,
            batch_id,
            user_id,
            item_id,
            extraction=_failed_extraction(
                exc.code,
                exc.args[0],
                current,
                finished_at,
                raw_provider_response=exc.raw_provider_response,
            ),
            now=finished_at,
        )
        return
    except Exception as exc:
        logger.exception("Unexpected extraction failure")
        finished_at = _utc_now()
        await save_item_extraction_failure(
            database,
            batch_id,
            user_id,
            item_id,
            extraction=_failed_extraction(
                "extraction-failed",
                str(exc),
                current,
                finished_at,
            ),
            now=finished_at,
        )
        return

    finished_at = _utc_now()
    normalized_text = normalize_extracted_problem_text(result.text)
    await save_item_extraction_success(
        database,
        batch_id,
        user_id,
        item_id,
        crop=crop_meta,
        draft={
            "text": normalized_text,
            "problemType": result.problem_type,
            "graphDsl": result.graph_dsl,
            "correctAnswer": result.correct_answer,
            "tags": [],
            "subject": subject,
        },
        extraction={
            "success": True,
            "model": result.model,
            "rawText": result.text,
            "rawProblemType": result.problem_type,
            "rawGraphDsl": result.graph_dsl,
            "rawProviderResponse": result.raw_provider_response,
            "failureCode": None,
            "failureMessage": None,
            "requestStartedAt": current,
            "requestFinishedAt": finished_at,
        },
        now=finished_at,
    )


async def claim_next_item(
    database: Any,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Find and atomically claim the next available item across active batches."""
    current = now or _utc_now()
    cursor = database[INGESTION_BATCHES_COLLECTION].find(
        {
            "status": BatchState.ACTIVE.value,
            "items.status": {"$in": [ItemState.QUEUED.value, ItemState.EXTRACTING.value]},
        }
    )
    batches = await cursor.to_list(length=None)
    for batch in batches:
        batch_user_id = batch.get("userId")
        for item in batch.get("items", []):
            if not _is_item_claimable(item, current):
                continue
            claimed = await claim_item(
                database,
                batch["_id"],
                item["itemId"],
                batch_user_id,
                lease_timeout_seconds=settings.bulk_ingestion_item_lease_timeout_seconds,
                now=current,
            )
            if claimed is not None:
                return batch, claimed
    return None


async def run_extraction_worker(
    database: Any,
    storage: S3StorageAdapter,
    settings: Settings,
    math_client: VLMClient,
    english_client: VLMClient,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Loop claiming and processing extraction items up to configured concurrency."""
    poll_interval = getattr(settings, "bulk_ingestion_extraction_poll_interval_seconds", 5)

    logger.info("Extraction worker started")

    while True:
        if stop_event and stop_event.is_set():
            break

        tasks: list[asyncio.Task[Any]] = []
        for _ in range(settings.bulk_ingestion_extraction_concurrency):
            claimed = await claim_next_item(database, settings, now=_utc_now())
            if claimed is None:
                break
            batch, item = claimed
            task = asyncio.create_task(
                process_item(
                    item,
                    batch,
                    database,
                    storage,
                    math_client,
                    english_client,
                    settings,
                )
            )
            tasks.append(task)

        if not tasks:
            # No work available: sleep for the poll interval, or exit when asked.
            if stop_event:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                except asyncio.TimeoutError:
                    continue
                break
            await asyncio.sleep(poll_interval)
            continue

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.exception("Extraction task failed")
