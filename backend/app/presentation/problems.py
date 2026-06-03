from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import re

from typing import Any, Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.domain.models import ProblemType
from app.domain.normalization import normalize_answer
from app.presentation.deps import DatabaseDependency, get_current_user
from app.presentation.errors import ApiError
from app.presentation.helpers import build_problem_image_url, get_owned_problem, normalize_tags, parse_object_id
from app.presentation.schemas import CorrectAnswerPayload
from app.presentation.tags import _register_tags

router = APIRouter(prefix="/problems", tags=["problems"])


class UpdateProblemRequest(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    problemType: ProblemType | None = None
    graphDsl: str | None = None
    correctAnswer: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = None


class TrackingPayload(BaseModel):
    exposureCount: int
    correctCount: int
    failedCount: int
    lastTestedAt: datetime | None
    lastAttemptCorrect: bool | None


class OriginPayload(BaseModel):
    previewId: str | None = None
    vlmModel: str | None = None
    rawExtractedText: str | None = None
    rawExtractedProblemType: str | None = None
    rawExtractedGraphDsl: str | None = None


class ProblemSummaryPayload(BaseModel):
    id: str
    text: str
    problemType: ProblemType
    graphDsl: str | None = None
    tags: list[str]
    tracking: TrackingPayload
    isDeleted: bool
    deletedAt: datetime | None
    createdAt: datetime
    updatedAt: datetime
    imageUrl: str | None = None
    folderId: str | None = None


class ProblemDetailPayload(ProblemSummaryPayload):
    correctAnswer: CorrectAnswerPayload
    origin: OriginPayload


class ProblemResponse(BaseModel):
    problem: ProblemDetailPayload


class ProblemListResponse(BaseModel):
    items: list[ProblemSummaryPayload]
    page: int
    pageSize: int
    total: int


class ProblemDeleteResponse(BaseModel):
    ok: bool


class ProblemTrackingResponse(BaseModel):
    problemId: str
    tracking: TrackingPayload


class ProblemTagsResponse(BaseModel):
    items: list[str]


class SolutionStatusResponse(BaseModel):
    status: str


class SetProblemFolderRequest(BaseModel):
    folderId: str | None = None


class BulkSetFolderRequest(BaseModel):
    problemIds: list[str] = Field(min_length=1)
    folderId: str | None = None


class BulkSetFolderResponse(BaseModel):
    ok: bool


CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]


def _serialize_tracking(problem: dict[str, Any]) -> TrackingPayload:
    tracking = dict(problem.get("tracking", {}))
    return TrackingPayload(
        exposureCount=int(tracking.get("exposureCount", 0)),
        correctCount=int(tracking.get("correctCount", 0)),
        failedCount=int(tracking.get("failedCount", 0)),
        lastTestedAt=tracking.get("lastTestedAt"),
        lastAttemptCorrect=tracking.get("lastAttemptCorrect"),
    )


def _serialize_origin(problem: dict[str, Any]) -> OriginPayload:
    origin = dict(problem.get("origin") or {})
    preview_id = origin.get("previewId")
    return OriginPayload(
        previewId=str(preview_id) if preview_id is not None else None,
        vlmModel=origin.get("vlmModel"),
        rawExtractedText=origin.get("rawExtractedText"),
        rawExtractedProblemType=origin.get("rawExtractedProblemType"),
        rawExtractedGraphDsl=origin.get("rawExtractedGraphDsl"),
    )


def _serialize_correct_answer(problem: dict[str, Any]) -> CorrectAnswerPayload:
    correct_answer = dict(problem.get("correctAnswer", {}))
    return CorrectAnswerPayload(
        display=str(correct_answer.get("display", "")),
        normalizedText=str(correct_answer.get("normalizedText", "")),
        normalizedSet=[str(item) for item in correct_answer.get("normalizedSet", [])],
        format=str(correct_answer.get("format", "single")),
    )


def _serialize_problem_summary(problem: dict[str, Any]) -> ProblemSummaryPayload:
    folder_id = problem.get("folderId")
    return ProblemSummaryPayload(
        id=str(problem["_id"]),
        text=str(problem["text"]),
        problemType=ProblemType(problem["problemType"]),
        graphDsl=problem.get("graphDsl"),
        tags=[str(tag) for tag in problem.get("tags", [])],
        tracking=_serialize_tracking(problem),
        isDeleted=bool(problem.get("isDeleted", False)),
        deletedAt=problem.get("deletedAt"),
        createdAt=problem["createdAt"],
        updatedAt=problem["updatedAt"],
        imageUrl=build_problem_image_url(str(problem["_id"]))
        if problem.get("sourceImage")
        else None,
        folderId=folder_id if folder_id else None,
    )


def _serialize_problem_detail(problem: dict[str, Any]) -> ProblemDetailPayload:
    summary = _serialize_problem_summary(problem)
    return ProblemDetailPayload(
        **summary.model_dump(),
        correctAnswer=_serialize_correct_answer(problem),
        origin=_serialize_origin(problem),
    )


async def _get_owned_folder(
    database: DatabaseDependency,
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


async def _get_all_descendant_folder_ids(
    database: DatabaseDependency,
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



@router.get("/tags", response_model=ProblemTagsResponse)
async def list_problem_tags(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ProblemTagsResponse:
    tags = await database["problems"].distinct(
        "tags",
        {"userId": current_user["_id"], "isDeleted": False},
    )
    return ProblemTagsResponse(items=sorted(str(tag) for tag in tags if str(tag).strip()))


@router.get("", response_model=ProblemListResponse)
async def list_problems(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    tag: str | None = Query(default=None),
    problem_type: ProblemType | None = Query(default=None, alias="type"),
    q: str | None = Query(default=None),
    folder_id: str | None = Query(default=None, alias="folderId"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> ProblemListResponse:
    user_id = current_user["_id"]
    query: dict[str, Any] = {"userId": user_id, "isDeleted": False}
    if tag is not None:
        query["tags"] = tag
    if problem_type is not None:
        query["problemType"] = problem_type.value
    if q is not None:
        trimmed = q.strip()
        if trimmed:
            escaped = re.escape(trimmed)
            query["$or"] = [
                {"text": {"$regex": escaped, "$options": "i"}},
                {"tags": {"$regex": escaped, "$options": "i"}},
            ]

    # Handle folder filtering
    if folder_id is not None:
        if folder_id == "unfiled":
            # Unfiled: problems where folderId is null or absent
            query["folderId"] = None
        else:
            # Real folder: validate ownership and include descendants
            folder = await _get_owned_folder(database, folder_id, user_id)
            folder_oid = folder["_id"]
            descendant_ids = await _get_all_descendant_folder_ids(database, folder_oid)
            all_folder_ids = {folder_oid} | descendant_ids
            query["folderId"] = {"$in": [str(fid) for fid in all_folder_ids]}

    total = await database["problems"].count_documents(query)
    cursor = database["problems"].find(query).sort("updatedAt", -1)
    cursor = cursor.skip((page - 1) * page_size).limit(page_size)
    items = await cursor.to_list(length=page_size)
    return ProblemListResponse(
        items=[_serialize_problem_summary(problem) for problem in items],
        page=page,
        pageSize=page_size,
        total=total,
    )


@router.patch("/bulk-folder", response_model=BulkSetFolderResponse)
async def bulk_set_problem_folder(
    payload: BulkSetFolderRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> BulkSetFolderResponse:
    """Bulk assign problems to a folder or Unfiled."""
    user_id = current_user["_id"]

    # Validate target folder if provided
    target_folder_id: str | None = None
    if payload.folderId is not None:
        folder = await _get_owned_folder(database, payload.folderId, user_id)
        target_folder_id = str(folder["_id"])

    # Validate all problem IDs and ownership
    problem_object_ids: list[ObjectId] = []
    for problem_id_str in payload.problemIds:
        problem_id = parse_object_id(problem_id_str, resource_name="Problem")
        problem = await database["problems"].find_one({"_id": problem_id})
        if problem is None or problem.get("isDeleted", False):
            raise ApiError(404, "NOT_FOUND", f"Problem {problem_id_str} not found")
        if problem.get("userId") != user_id:
            raise ApiError(403, "FORBIDDEN", f"Problem {problem_id_str} not owned")
        problem_object_ids.append(problem_id)

    # Update all problems
    now = datetime.now(UTC)
    await database["problems"].update_many(
        {"_id": {"$in": problem_object_ids}},
        {"$set": {"folderId": target_folder_id, "updatedAt": now}},
    )

    return BulkSetFolderResponse(ok=True)


@router.get("/{problem_id}", response_model=ProblemResponse)
async def get_problem_detail(
    problem_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ProblemResponse:
    problem = await get_owned_problem(
        database,
        problem_id,
        current_user["_id"],
        allow_deleted=False,
    )
    return ProblemResponse(problem=_serialize_problem_detail(problem))


@router.patch("/{problem_id}", response_model=ProblemResponse)
async def update_problem(
    problem_id: str,
    payload: UpdateProblemRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ProblemResponse:
    problem = await get_owned_problem(
        database,
        problem_id,
        current_user["_id"],
        allow_deleted=False,
    )

    next_problem_type = payload.problemType or ProblemType(problem["problemType"])
    raw_correct_answer = (
        payload.correctAnswer
        if payload.correctAnswer is not None
        else str(problem.get("correctAnswer", {}).get("display", ""))
    )

    updates: dict[str, Any] = {"updatedAt": datetime.now(UTC)}
    if payload.text is not None:
        updates["text"] = payload.text
    if payload.problemType is not None:
        updates["problemType"] = payload.problemType.value
    if "graphDsl" in payload.model_fields_set:
        updates["graphDsl"] = payload.graphDsl
    if "tags" in payload.model_fields_set and payload.tags is not None:
        updates["tags"] = normalize_tags(payload.tags)
        await _register_tags(database, current_user["_id"], payload.tags)
    if payload.correctAnswer is not None or payload.problemType is not None:
        updates["correctAnswer"] = normalize_answer(
            raw_correct_answer,
            next_problem_type,
        ).model_dump()

    await database["problems"].update_one({"_id": problem["_id"]}, {"$set": updates})
    updated_problem = deepcopy(problem)
    updated_problem.update(updates)
    return ProblemResponse(problem=_serialize_problem_detail(updated_problem))


@router.patch("/{problem_id}/folder", response_model=ProblemResponse)
async def set_problem_folder(
    problem_id: str,
    payload: SetProblemFolderRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ProblemResponse:
    """Assign a problem to a folder or Unfiled."""
    user_id = current_user["_id"]
    problem = await get_owned_problem(database, problem_id, user_id, allow_deleted=False)

    # Validate target folder if provided
    target_folder_id: str | None = None
    if payload.folderId is not None:
        folder = await _get_owned_folder(database, payload.folderId, user_id)
        target_folder_id = str(folder["_id"])

    # Update the problem
    now = datetime.now(UTC)
    await database["problems"].update_one(
        {"_id": problem["_id"]},
        {"$set": {"folderId": target_folder_id, "updatedAt": now}},
    )

    updated_problem = deepcopy(problem)
    updated_problem["folderId"] = target_folder_id
    updated_problem["updatedAt"] = now
    return ProblemResponse(problem=_serialize_problem_detail(updated_problem))


@router.delete("/{problem_id}", response_model=ProblemDeleteResponse)
async def soft_delete_problem(
    problem_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ProblemDeleteResponse:
    problem = await get_owned_problem(
        database,
        problem_id,
        current_user["_id"],
        allow_deleted=True,
    )
    now = datetime.now(UTC)
    await database["problems"].update_one(
        {"_id": problem["_id"]},
        {"$set": {"isDeleted": True, "deletedAt": now, "updatedAt": now}},
    )
    return ProblemDeleteResponse(ok=True)


@router.get("/{problem_id}/solution-status", response_model=SolutionStatusResponse)
async def get_solution_status(
    problem_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> SolutionStatusResponse:
    problem = await get_owned_problem(
        database,
        problem_id,
        current_user["_id"],
        allow_deleted=False,
    )
    
    solutions = database.get_collection("canonical_solutions") if hasattr(database, "get_collection") else database["canonical_solutions"]
    existing_solution = await solutions.find_one({"problem_id": problem_id})
    if existing_solution is not None:
        return SolutionStatusResponse(status="ready")
        
    tasks = database.get_collection("solution_generation_tasks") if hasattr(database, "get_collection") else database["solution_generation_tasks"]
    existing_task = await tasks.find_one({"problem_id": problem_id})
    if existing_task is not None:
        return SolutionStatusResponse(status=str(existing_task.get("status", "pending")))
        
    return SolutionStatusResponse(status="none")


@router.get("/{problem_id}/tracking", response_model=ProblemTrackingResponse)
async def get_problem_tracking(
    problem_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ProblemTrackingResponse:
    problem = await get_owned_problem(
        database,
        problem_id,
        current_user["_id"],
        allow_deleted=False,
    )
    return ProblemTrackingResponse(
        problemId=str(problem["_id"]),
        tracking=_serialize_tracking(problem),
    )
