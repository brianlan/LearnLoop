from __future__ import annotations

import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Any

from bson import ObjectId
from fastapi import UploadFile
from fastapi.responses import StreamingResponse
from pymongo.asynchronous.database import AsyncDatabase

from app.infrastructure.storage.mongo import Document
from app.infrastructure.storage.s3 import S3StorageAdapter, StorageObjectNotFoundError
from app.presentation.errors import ApiError


def normalize_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        trimmed = tag.strip()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        normalized.append(trimmed)
    return normalized


def build_problem_image_url(problem_id: Any) -> str:
    return f"/api/v1/problems/{problem_id}/image"


def build_ingestion_source_image_url(batch_id: Any, image_id: str) -> str:
    return f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/source"


def build_ingestion_crop_url(batch_id: Any, item_id: str) -> str:
    return f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/crop"


def parse_object_id(raw_id: str, *, resource_name: str) -> ObjectId:
    if not ObjectId.is_valid(raw_id):
        raise ApiError(404, "NOT_FOUND", f"{resource_name} not found")
    return ObjectId(raw_id)


def guess_upload_extension(upload: UploadFile) -> str:
    if upload.filename:
        suffix = Path(upload.filename).suffix
        if suffix:
            return suffix
    if upload.content_type:
        guessed = mimetypes.guess_extension(upload.content_type)
        if guessed:
            return guessed
    return ".bin"


async def get_owned_problem(
    database: AsyncDatabase[Document],
    problem_id: str,
    user_id: ObjectId,
    *,
    allow_deleted: bool = False,
) -> dict[str, Any]:
    object_id = parse_object_id(problem_id, resource_name="Problem")
    problem = await database["problems"].find_one({"_id": object_id})
    if problem is None:
        raise ApiError(404, "NOT_FOUND", "Problem not found")
    if problem.get("userId") != user_id:
        raise ApiError(403, "FORBIDDEN", "Forbidden")
    if not allow_deleted and problem.get("isDeleted", False):
        raise ApiError(404, "NOT_FOUND", "Problem not found")
    return problem


async def get_owned_folder(
    database: AsyncDatabase[Document],
    folder_id: str,
    user_id: ObjectId,
) -> dict[str, Any]:
    """Get a folder by ID, verifying ownership."""
    object_id = parse_object_id(folder_id, resource_name="Folder")
    folder = await database["folders"].find_one({"_id": object_id})
    if folder is None:
        raise ApiError(404, "NOT_FOUND", "Folder not found")
    if folder.get("userId") != user_id:
        raise ApiError(403, "FORBIDDEN", "Forbidden")
    return folder


async def get_all_descendant_folder_ids(
    database: AsyncDatabase[Document],
    folder_id: ObjectId,
) -> set[ObjectId]:
    """Get all descendant folder IDs recursively."""
    descendants: set[ObjectId] = set()
    to_check = [folder_id]

    while to_check:
        current_batch = to_check
        to_check = []

        cursor = database["folders"].find(
            {"parentId": {"$in": current_batch}},
            {"_id": 1},
        )
        children = await cursor.to_list(length=None)

        for child in children:
            child_id = child["_id"]
            if child_id not in descendants:
                descendants.add(child_id)
                to_check.append(child_id)

    return descendants


def stream_storage_metadata(
    metadata: dict[str, Any],
    storage: S3StorageAdapter,
    *,
    missing_metadata_code: str,
    missing_metadata_message: str,
) -> StreamingResponse:
    """Stream already-authorized storage metadata as a response.

    Reads bytes from storage and returns a StreamingResponse with the stored
    content type.  Raises ``missing_metadata_code`` when bucket/objectKey are
    absent, and ``NOT_FOUND`` when the storage object itself is missing.
    """
    bucket = metadata.get("bucket")
    object_key = metadata.get("objectKey")
    if not bucket or not object_key:
        raise ApiError(404, missing_metadata_code, missing_metadata_message)

    try:
        image_bytes = storage.get_object(str(bucket), str(object_key))
    except StorageObjectNotFoundError as exc:
        raise ApiError(404, "NOT_FOUND", missing_metadata_message) from exc

    return StreamingResponse(
        BytesIO(image_bytes),
        media_type=str(metadata.get("contentType") or "application/octet-stream"),
    )
