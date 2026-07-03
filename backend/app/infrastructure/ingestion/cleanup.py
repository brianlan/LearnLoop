from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from app.infrastructure.storage.mongo import Document
from app.infrastructure.storage.s3 import StorageObjectNotFoundError

from .repository import INGESTION_BATCHES_COLLECTION, find_cleanup_candidates, mark_batch_cleaned


def _delete_media(storage: Any, bucket: str | None, key: str | None) -> None:
    if not bucket or not key:
        return
    try:
        storage.delete_object(bucket, key)
    except StorageObjectNotFoundError:
        return
    except ClientError:
        return


async def cleanup_batch_media(storage: Any, batch: Document) -> None:
    """Delete all temporary source images and item crops for a batch.

    Missing objects are ignored so the cleanup remains idempotent.
    """
    for image in batch.get("images", []):
        source_image = image.get("sourceImage") or {}
        _delete_media(storage, source_image.get("bucket"), source_image.get("objectKey"))

    for item in batch.get("items", []):
        crop = item.get("crop") or {}
        _delete_media(storage, crop.get("bucket"), crop.get("objectKey"))


async def run_batch_cleanup(
    database: Any,
    storage: Any,
    *,
    now: Any | None = None,
) -> int:
    """Find and clean expired/completed batches. Returns number cleaned."""
    candidates = await find_cleanup_candidates(database, now=now)
    cleaned = 0
    for batch in candidates:
        await cleanup_batch_media(storage, batch)
        await mark_batch_cleaned(database, batch["_id"], now=now)
        cleaned += 1
    return cleaned


__all__ = [
    "INGESTION_BATCHES_COLLECTION",
    "cleanup_batch_media",
    "find_cleanup_candidates",
    "run_batch_cleanup",
]
