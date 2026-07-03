"""Ingestion infrastructure namespace."""

from .cleanup import cleanup_batch_media, run_batch_cleanup
from .documents import (
    build_batch_document,
    build_image_document,
    build_item_document,
    build_source_image,
    new_image_id,
    new_item_id,
)
from .repository import (
    INGESTION_BATCHES_COLLECTION,
    BATCH_INDEXES,
    add_items_for_image,
    add_source_image,
    create_batch,
    ensure_batch_indexes,
    find_cleanup_candidates,
    get_active_batch_for_user,
    get_batch,
    is_batch_expired,
    mark_batch_cleaned,
)

__all__ = [
    "INGESTION_BATCHES_COLLECTION",
    "BATCH_INDEXES",
    "add_items_for_image",
    "add_source_image",
    "build_batch_document",
    "build_image_document",
    "build_item_document",
    "build_source_image",
    "cleanup_batch_media",
    "create_batch",
    "ensure_batch_indexes",
    "find_cleanup_candidates",
    "get_active_batch_for_user",
    "get_batch",
    "is_batch_expired",
    "mark_batch_cleaned",
    "new_image_id",
    "new_item_id",
    "run_batch_cleanup",
]
