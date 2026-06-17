from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.presentation.deps import DatabaseDependency, get_current_user
from app.presentation.errors import ApiError
from app.presentation.helpers import (
    get_all_descendant_folder_ids,
    get_owned_folder,
    parse_object_id,
)

router = APIRouter(prefix="/folders", tags=["folders"])

CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]

MAX_FOLDER_NAME_LENGTH = 200


class FolderNodePayload(BaseModel):
    id: str
    name: str
    parentId: str | None
    problemCount: int
    children: list["FolderNodePayload"]
    createdAt: datetime
    updatedAt: datetime


class FolderTreeResponse(BaseModel):
    allProblemsCount: int
    unfiledCount: int
    items: list[FolderNodePayload]


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_FOLDER_NAME_LENGTH)
    parentId: str | None = None


class UpdateFolderRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_FOLDER_NAME_LENGTH)
    parentId: str | None = None


class FolderPayload(BaseModel):
    id: str
    name: str
    parentId: str | None
    createdAt: datetime
    updatedAt: datetime


class FolderResponse(BaseModel):
    folder: FolderPayload


class DeleteFolderResponse(BaseModel):
    ok: bool


def _normalize_folder_name(name: str) -> str:
    """Trim and validate folder name."""
    trimmed = name.strip()
    if not trimmed:
        raise ApiError(400, "VALIDATION_ERROR", "Folder name cannot be empty")
    if len(trimmed) > MAX_FOLDER_NAME_LENGTH:
        raise ApiError(400, "VALIDATION_ERROR", f"Folder name cannot exceed {MAX_FOLDER_NAME_LENGTH} characters")
    return trimmed


async def _check_sibling_name_conflict(
    database: DatabaseDependency,
    user_id: ObjectId,
    name: str,
    parent_id: ObjectId | None,
    exclude_folder_id: ObjectId | None = None,
) -> None:
    """Check for case-insensitive name conflict among siblings."""
    query: dict[str, Any] = {
        "userId": user_id,
        "parentId": parent_id,
    }
    if exclude_folder_id is not None:
        query["_id"] = {"$ne": exclude_folder_id}

    # Use regex for case-insensitive match since we can't rely on collation in query
    existing = await database["folders"].find_one({
        **query,
        "name": {"$regex": f"^{re.escape(name)}$", "$options": "i"},
    })
    if existing is not None:
        raise ApiError(409, "DUPLICATE_FOLDER_NAME", "A folder with this name already exists in this location")


async def _count_problems_in_folder(
    database: DatabaseDependency,
    user_id: ObjectId,
    folder_id: ObjectId | None,
) -> int:
    """Count non-deleted problems in a folder (None = unfiled)."""
    query: dict[str, Any] = {"userId": user_id, "isDeleted": False}
    if folder_id is None:
        query["folderId"] = None
    else:
        query["folderId"] = str(folder_id)
    return await database["problems"].count_documents(query)


async def _count_problems_in_folder_tree(
    database: DatabaseDependency,
    user_id: ObjectId,
    folder_id: ObjectId,
) -> int:
    """Count non-deleted problems in a folder and all its descendants."""
    # Get all descendants
    descendant_ids = await get_all_descendant_folder_ids(database, folder_id)
    all_folder_ids = {folder_id} | descendant_ids

    # Count problems in all these folders
    query: dict[str, Any] = {
        "userId": user_id,
        "isDeleted": False,
        "folderId": {"$in": [str(fid) for fid in all_folder_ids]},
    }
    return await database["problems"].count_documents(query)


def _build_folder_tree(
    folders: list[dict[str, Any]],
    counts_by_folder: dict[str, int],
    parent_id: ObjectId | None = None,
) -> list[FolderNodePayload]:
    """Build nested folder tree from flat list, sorted alphabetically by name."""
    children = [f for f in folders if f.get("parentId") == parent_id]
    # Sort alphabetically by name
    children = sorted(children, key=lambda f: f["name"].lower())

    result = []
    for folder in children:
        folder_id = str(folder["_id"])
        node = FolderNodePayload(
            id=folder_id,
            name=folder["name"],
            parentId=str(folder["parentId"]) if folder.get("parentId") else None,
            problemCount=counts_by_folder.get(folder_id, 0),
            children=_build_folder_tree(folders, counts_by_folder, folder["_id"]),
            createdAt=folder["createdAt"],
            updatedAt=folder["updatedAt"],
        )
        result.append(node)

    return result


def _serialize_folder(folder: dict[str, Any]) -> FolderPayload:
    return FolderPayload(
        id=str(folder["_id"]),
        name=folder["name"],
        parentId=str(folder["parentId"]) if folder.get("parentId") else None,
        createdAt=folder["createdAt"],
        updatedAt=folder["updatedAt"],
    )


@router.get("", response_model=FolderTreeResponse)
async def list_folders_tree(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> FolderTreeResponse:
    """List all folders as a tree with recursive problem counts."""
    user_id = current_user["_id"]

    # Get all user's folders
    cursor = database["folders"].find({"userId": user_id})
    folders = await cursor.to_list(length=None)

    # Get problem counts for each folder (recursive)
    counts_by_folder: dict[str, int] = {}
    for folder in folders:
        folder_id = folder["_id"]
        count = await _count_problems_in_folder_tree(database, user_id, folder_id)
        counts_by_folder[str(folder_id)] = count

    # Build tree
    items = _build_folder_tree(folders, counts_by_folder, parent_id=None)

    # Calculate all problems count and unfiled count
    all_problems_count = await database["problems"].count_documents({
        "userId": user_id,
        "isDeleted": False,
    })
    unfiled_count = await _count_problems_in_folder(database, user_id, None)

    return FolderTreeResponse(
        allProblemsCount=all_problems_count,
        unfiledCount=unfiled_count,
        items=items,
    )


@router.post("", response_model=FolderResponse, status_code=201)
async def create_folder(
    payload: CreateFolderRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> FolderResponse:
    """Create a new folder."""
    user_id = current_user["_id"]
    name = _normalize_folder_name(payload.name)

    # Validate parent if provided
    parent_id: ObjectId | None = None
    if payload.parentId is not None:
        parent = await database["folders"].find_one({"_id": parse_object_id(payload.parentId, resource_name="Folder")})
        if parent is None:
            raise ApiError(404, "NOT_FOUND", "Parent folder not found")
        if parent.get("userId") != user_id:
            raise ApiError(403, "FORBIDDEN", "Forbidden")
        parent_id = parent["_id"]

    # Check for sibling name conflict
    await _check_sibling_name_conflict(database, user_id, name, parent_id)

    now = datetime.now(UTC)
    folder = {
        "_id": ObjectId(),
        "userId": user_id,
        "name": name,
        "parentId": parent_id,
        "createdAt": now,
        "updatedAt": now,
    }

    await database["folders"].insert_one(folder)
    return FolderResponse(folder=_serialize_folder(folder))


@router.get("/{folder_id}", response_model=FolderResponse)
async def get_folder(
    folder_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> FolderResponse:
    """Get a single folder by ID."""
    user_id = current_user["_id"]
    folder = await get_owned_folder(database, folder_id, user_id)
    return FolderResponse(folder=_serialize_folder(folder))


@router.patch("/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: str,
    payload: UpdateFolderRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> FolderResponse:
    """Rename or move a folder."""
    user_id = current_user["_id"]
    folder = await get_owned_folder(database, folder_id, user_id)
    folder_oid = folder["_id"]

    updates: dict[str, Any] = {"updatedAt": datetime.now(UTC)}

    # Handle rename
    if payload.name is not None:
        new_name = _normalize_folder_name(payload.name)
        if new_name.lower() != folder["name"].lower():
            await _check_sibling_name_conflict(
                database, user_id, new_name, folder.get("parentId"), exclude_folder_id=folder_oid
            )
        updates["name"] = new_name

    # Handle move
    if "parentId" in payload.model_fields_set:
        new_parent_id: ObjectId | None = None

        if payload.parentId is not None:
            new_parent = await database["folders"].find_one({
                "_id": parse_object_id(payload.parentId, resource_name="Folder")
            })
            if new_parent is None:
                raise ApiError(404, "NOT_FOUND", "Parent folder not found")
            if new_parent.get("userId") != user_id:
                raise ApiError(403, "FORBIDDEN", "Forbidden")
            new_parent_id = new_parent["_id"]

        # Check for cycle (can't move folder under itself or its descendants)
        if new_parent_id is not None:
            if new_parent_id == folder_oid:
                raise ApiError(400, "INVALID_MOVE", "Cannot move folder under itself")

            descendants = await get_all_descendant_folder_ids(database, folder_oid)
            if new_parent_id in descendants:
                raise ApiError(400, "INVALID_MOVE", "Cannot move folder under its descendant")

        # Check for sibling name conflict in new location
        if payload.name is None:
            # Name not changing, check conflict with current name
            await _check_sibling_name_conflict(
                database, user_id, updates.get("name", folder["name"]), new_parent_id, exclude_folder_id=folder_oid
            )

        updates["parentId"] = new_parent_id

    await database["folders"].update_one({"_id": folder_oid}, {"$set": updates})
    folder.update(updates)
    return FolderResponse(folder=_serialize_folder(folder))


@router.delete("/{folder_id}", response_model=DeleteFolderResponse)
async def delete_folder(
    folder_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> DeleteFolderResponse:
    """Delete an empty folder."""
    user_id = current_user["_id"]
    folder = await get_owned_folder(database, folder_id, user_id)
    folder_oid = folder["_id"]

    # Check for child folders
    child_count = await database["folders"].count_documents({"parentId": folder_oid})
    if child_count > 0:
        raise ApiError(400, "FOLDER_NOT_EMPTY", "Cannot delete folder with subfolders")

    # Check for problems in this folder
    problem_count = await _count_problems_in_folder(database, user_id, folder_oid)
    if problem_count > 0:
        raise ApiError(400, "FOLDER_NOT_EMPTY", "Cannot delete folder with problems")

    await database["folders"].delete_one({"_id": folder_oid})
    return DeleteFolderResponse(ok=True)
