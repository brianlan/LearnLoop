from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from fastapi import APIRouter
from pydantic import BaseModel, Field
from pymongo.asynchronous.database import AsyncDatabase

from app.infrastructure.storage.mongo import Document
from app.presentation.deps import CurrentUserDependency, DatabaseDependency
from app.presentation.errors import ApiError
from app.presentation.helpers import parse_object_id

router = APIRouter(prefix="/tags", tags=["tags"])


class TagPayload(BaseModel):
    id: str
    name: str
    createdAt: datetime
    problemCount: int


class TagListResponse(BaseModel):
    items: list[TagPayload]


class CreateTagRequest(BaseModel):
    name: str = Field(min_length=1)


class RenameTagRequest(BaseModel):
    name: str = Field(min_length=1)


class TagResponse(BaseModel):
    tag: TagPayload


async def _get_owned_tag(
    database: AsyncDatabase[Document],
    tag_id: str,
    user_id: ObjectId,
) -> dict[str, Any]:
    object_id = parse_object_id(tag_id, resource_name="Tag")
    tag = await database["tags"].find_one({"_id": object_id})
    if tag is None:
        raise ApiError(404, "NOT_FOUND", "Tag not found")
    if tag.get("userId") != user_id:
        raise ApiError(403, "FORBIDDEN", "Forbidden")
    return tag


def _serialize_tag(tag: dict[str, Any], problem_count: int) -> TagPayload:
    return TagPayload(
        id=str(tag["_id"]),
        name=str(tag["name"]),
        createdAt=tag["createdAt"],
        problemCount=problem_count,
    )


async def _count_problems_by_tag(
    database: AsyncDatabase[Document], user_id: ObjectId
) -> dict[str, int]:
    """Aggregate problem counts by tag for a user in a single query."""
    pipeline = [
        {"$match": {"userId": user_id, "isDeleted": False}},
        {"$unwind": "$tags"},
        {"$group": {"_id": "$tags", "count": {"$sum": 1}}},
    ]
    cursor = await database["problems"].aggregate(pipeline)
    results = await cursor.to_list(length=None)
    return {r["_id"]: r["count"] for r in results}


async def _count_problems_with_tag(database: AsyncDatabase[Document], user_id: ObjectId, tag_name: str) -> int:
    """Count problems containing a specific tag (for single-tag lookups)."""
    return await database["problems"].count_documents(
        {"userId": user_id, "tags": tag_name, "isDeleted": False}
    )


@router.get("", response_model=TagListResponse)
async def list_tags(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> TagListResponse:
    user_id = current_user["_id"]
    tags = await database["tags"].find({"userId": user_id}).sort("name", 1).to_list(length=None)
    counts = await _count_problems_by_tag(database, user_id)
    items = [_serialize_tag(tag, counts.get(tag["name"], 0)) for tag in tags]
    return TagListResponse(items=items)


@router.post("", response_model=TagResponse, status_code=201)
async def create_tag(
    payload: CreateTagRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> TagResponse:
    user_id = current_user["_id"]
    name = payload.name.strip()
    if not name:
        raise ApiError(400, "VALIDATION_ERROR", "Tag name cannot be empty")
    existing = await database["tags"].find_one({"userId": user_id, "name": name})
    if existing is not None:
        raise ApiError(409, "DUPLICATE_TAG", "Tag with this name already exists")
    now = datetime.now(UTC)
    tag = {"_id": ObjectId(), "userId": user_id, "name": name, "createdAt": now, "updatedAt": now}
    await database["tags"].insert_one(tag)
    return TagResponse(tag=_serialize_tag(tag, 0))


@router.get("/{tag_id}", response_model=TagResponse)
async def get_tag(
    tag_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> TagResponse:
    user_id = current_user["_id"]
    tag = await _get_owned_tag(database, tag_id, user_id)
    count = await _count_problems_with_tag(database, user_id, tag["name"])
    return TagResponse(tag=_serialize_tag(tag, count))


@router.patch("/{tag_id}", response_model=TagResponse)
async def rename_tag(
    tag_id: str,
    payload: RenameTagRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> TagResponse:
    user_id = current_user["_id"]
    tag = await _get_owned_tag(database, tag_id, user_id)
    new_name = payload.name.strip()
    if not new_name:
        raise ApiError(400, "VALIDATION_ERROR", "Tag name cannot be empty")
    old_name = tag["name"]
    if new_name == old_name:
        count = await _count_problems_with_tag(database, user_id, old_name)
        return TagResponse(tag=_serialize_tag(tag, count))
    existing = await database["tags"].find_one({"userId": user_id, "name": new_name})
    if existing is not None:
        raise ApiError(409, "DUPLICATE_TAG", "Tag with this name already exists")
    now = datetime.now(UTC)
    await database["tags"].update_one(
        {"_id": tag["_id"]},
        {"$set": {"name": new_name, "updatedAt": now}},
    )
    await database["problems"].update_many(
        {"userId": user_id, "tags": old_name},
        [
            {"$set": {
                "tags": {"$map": {
                    "input": "$tags",
                    "as": "t",
                    "in": {"$cond": [{"$eq": ["$$t", old_name]}, new_name, "$$t"]},
                }},
                "updatedAt": now,
            }},
        ],
    )
    tag["name"] = new_name
    count = await _count_problems_with_tag(database, user_id, new_name)
    return TagResponse(tag=_serialize_tag(tag, count))


@router.delete("/{tag_id}")
async def delete_tag(
    tag_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> dict[str, bool]:
    user_id = current_user["_id"]
    try:
        tag = await _get_owned_tag(database, tag_id, user_id)
    except ApiError as e:
        if e.code == "NOT_FOUND":
            return {"ok": True}
        raise
    old_name = tag["name"]
    now = datetime.now(UTC)
    await database["tags"].delete_one({"_id": tag["_id"]})
    await database["problems"].update_many(
        {"userId": user_id, "tags": old_name},
        [
            {"$set": {
                "tags": {"$filter": {
                    "input": "$tags",
                    "as": "t",
                    "cond": {"$ne": ["$$t", old_name]},
                }},
                "updatedAt": now,
            }},
        ],
    )
    return {"ok": True}
