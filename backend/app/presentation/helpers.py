from __future__ import annotations

from base64 import b64encode
from typing import Any

from bson import ObjectId
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


def parse_object_id(raw_id: str, *, resource_name: str) -> ObjectId:
    if not ObjectId.is_valid(raw_id):
        raise ApiError(404, "NOT_FOUND", f"{resource_name} not found")
    return ObjectId(raw_id)


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


def load_source_image_base64(
    source_image: dict[str, Any] | None,
    storage: S3StorageAdapter,
) -> str | None:
    """Load image from S3 and return as base64 string, or None if not found."""
    if not source_image:
        return None
    bucket = source_image.get("bucket")
    object_key = source_image.get("objectKey")
    if not bucket or not object_key:
        return None
    try:
        image_bytes = storage.get_object(str(bucket), str(object_key))
    except StorageObjectNotFoundError:
        return None
    return b64encode(image_bytes).decode("ascii")
