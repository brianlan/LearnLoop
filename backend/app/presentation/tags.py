from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from pymongo.asynchronous.database import AsyncDatabase

from app.infrastructure.storage.mongo import Document
from app.presentation.deps import DatabaseDependency, get_current_user
from app.presentation.errors import ApiError
from app.presentation.helpers import normalize_tags, parse_object_id

router = APIRouter(prefix="/tags", tags=["tags"])

CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]


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


async def _count_problems_with_tag(database: AsyncDatabase[Document], user_id: ObjectId, tag_name: str) -> int:
    return await database["problems"].count_documents(
        {"userId": user_id, "tags": tag_name, "isDeleted": False}
    )


async def _register_tags(database: AsyncDatabase[Document], user_id: ObjectId, tags: list[str]) -> None:
    """Auto-register any new tags that don't already exist for the user."""
    normalized = normalize_tags(tags)
    if not normalized:
        return
    existing = await database["tags"].find(
        {"userId": user_id, "name": {"$in": normalized}}
    ).to_list(length=None)
    existing_names = {tag["name"] for tag in existing}
    now = datetime.now(UTC)
    new_tags = [
        {"_id": ObjectId(), "userId": user_id, "name": name, "createdAt": now}
        for name in normalized
        if name not in existing_names
    ]
    if new_tags:
        await database["tags"].insert_many(new_tags)


@router.get("", response_model=TagListResponse)
async def list_tags(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> TagListResponse:
    user_id = current_user["_id"]
    tags = await database["tags"].find({"userId": user_id}).sort("name", 1).to_list(length=None)
    items = []
    for tag in tags:
        count = await _count_problems_with_tag(database, user_id, tag["name"])
        items.append(_serialize_tag(tag, count))
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
    tag = {"_id": ObjectId(), "userId": user_id, "name": name, "createdAt": now}
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
        {"$set": {"name": new_name}},
    )
    problems = await database["problems"].find({"userId": user_id, "tags": old_name}).to_list(length=None)
    for problem in problems:
        updated_tags = [new_name if t == old_name else t for t in problem.get("tags", [])]
        await database["problems"].update_one(
            {"_id": problem["_id"]},
            {"$set": {"tags": updated_tags, "updatedAt": now}},
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
    problems = await database["problems"].find({"userId": user_id, "tags": old_name}).to_list(length=None)
    for problem in problems:
        updated_tags = [t for t in problem.get("tags", []) if t != old_name]
        await database["problems"].update_one(
            {"_id": problem["_id"]},
            {"$set": {"tags": updated_tags, "updatedAt": now}},
        )
    return {"ok": True}
