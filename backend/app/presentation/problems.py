from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import re

from typing import Any, Annotated, Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.domain.models import ProblemType
from app.domain.normalization import normalize_answer
from app.domain.practice_selection import PracticeSelectionConfig, compute_problem_weight_breakdown
from app.infrastructure.auth.password import verify_password
from app.infrastructure.config.settings import Settings, get_settings
from app.observability import log_teacher_password_event
from app.presentation.deps import DatabaseDependency, get_current_user
from app.presentation.errors import ApiError
from app.presentation.exam_helpers import problem_document_to_model
from app.presentation.helpers import get_all_descendant_folder_ids, get_owned_folder, get_owned_problem, normalize_tags, parse_object_id
from app.presentation.problem_serialization import (
    BulkSetFolderResponse,
    PracticeWeightPayload,
    ProblemDeleteResponse,
    ProblemListResponse,
    ProblemResponse,
    ProblemTagsResponse,
    ProblemTrackingResponse,
    SolutionStatusResponse,
    _serialize_problem_detail,
    _serialize_problem_summary,
    _serialize_tracking,
)
from app.solution_generation import regenerate_solution_task_for_problem
from app.presentation.tag_registration import _register_tags
from app.presentation.teacher_password import _ensure_teacher_password_hash

router = APIRouter(prefix="/problems", tags=["problems"])


class UpdateProblemRequest(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    problemType: ProblemType | None = None
    graphDsl: str | None = None
    correctAnswer: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = None


class SetProblemFolderRequest(BaseModel):
    folderId: str | None = None


class BulkSetFolderRequest(BaseModel):
    problemIds: list[str] = Field(min_length=1)
    folderId: str | None = None


class ToggleProblemDisabledRequest(BaseModel):
    isDisabled: bool
    teacherPassword: str = Field(min_length=1, max_length=1024)


CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]
ProblemSortBy = Literal["selectionScore", "addDate", "lastTestDate"]
ProblemSortOrder = Literal["asc", "desc"]
SolutionState = Literal["none", "pending", "generating", "ready", "failed"]


async def _solution_state_problem_ids(
    database: Any,
    user_id: Any,
    solution_state: str,
) -> list[ObjectId] | None:
    """Return problem ``_id``s whose effective solution state matches ``solution_state``.

    A ``None`` return value means no ``_id`` filter is needed (all of the user's
    problems match the requested state). Effective status precedence mirrors
    ``GET /api/v1/problems/{id}/solution-status``: a canonical solution means
    ``ready`` even when a stale task also exists; otherwise the task status wins;
    otherwise the status is ``none``.
    """
    user_id_str = str(user_id)
    solutions = database["canonical_solutions"]
    tasks = database["solution_generation_tasks"]

    ready_docs = await solutions.find({"user_id": user_id_str}).to_list(length=None)
    ready_ids = {str(doc["problem_id"]) for doc in ready_docs}

    if solution_state == "ready":
        return [ObjectId(pid) for pid in ready_ids]

    task_docs = await tasks.find({"user_id": user_id_str}).to_list(length=None)

    if solution_state in ("pending", "generating", "failed"):
        return [
            ObjectId(doc["problem_id"])
            for doc in task_docs
            if str(doc.get("status", "pending")) == solution_state
            and str(doc["problem_id"]) not in ready_ids
        ]

    # solution_state == "none": problems with neither a canonical solution nor a task.
    task_ids = {str(doc["problem_id"]) for doc in task_docs}
    has_state_ids = ready_ids | task_ids
    if not has_state_ids:
        return None

    problem_docs = await database["problems"].find(
        {"userId": user_id, "isDeleted": False}
    ).to_list(length=None)
    return [
        doc["_id"]
        for doc in problem_docs
        if str(doc["_id"]) not in has_state_ids
    ]


def _practice_selection_config(settings: Settings) -> PracticeSelectionConfig:
    return PracticeSelectionConfig(
        cooldown_days=settings.problem_selection_cooldown_days,
        last_wrong_weight=settings.problem_selection_last_wrong_weight,
        failure_rate_weight=settings.problem_selection_failure_rate_weight,
        recency_weight=settings.problem_selection_recency_weight,
        min_problem_age_days=settings.problem_selection_min_age_days,
    )


def _problem_id_sort_value(problem: dict[str, Any]) -> str:
    return str(problem["_id"])


def _sort_by_last_test_date(
    problems: list[dict[str, Any]],
    *,
    reverse: bool,
) -> list[dict[str, Any]]:
    tested = [problem for problem in problems if problem.get("tracking", {}).get("lastTestedAt") is not None]
    never_tested = [problem for problem in problems if problem.get("tracking", {}).get("lastTestedAt") is None]
    tested.sort(key=_problem_id_sort_value)
    tested.sort(
        key=lambda problem: problem.get("tracking", {})["lastTestedAt"],
        reverse=reverse,
    )
    never_tested.sort(key=_problem_id_sort_value)
    return tested + never_tested


def _sort_by_selection_score(
    problems: list[dict[str, Any]],
    *,
    reverse: bool,
    settings: Settings,
) -> list[dict[str, Any]]:
    config = _practice_selection_config(settings)
    now = datetime.now(UTC)
    scored = [
        (
            compute_problem_weight_breakdown(problem_document_to_model(problem), config, now).total,
            problem,
        )
        for problem in problems
    ]
    scored.sort(key=lambda item: _problem_id_sort_value(item[1]))
    scored.sort(key=lambda item: item[0], reverse=reverse)
    return [problem for _, problem in scored]


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
    settings: Annotated[Settings, Depends(get_settings)],
    tag: str | None = Query(default=None),
    problem_type: ProblemType | None = Query(default=None, alias="type"),
    q: str | None = Query(default=None),
    folder_id: str | None = Query(default=None, alias="folderId"),
    sort_by: ProblemSortBy | None = Query(default=None, alias="sortBy"),
    sort_order: ProblemSortOrder | None = Query(default=None, alias="sortOrder"),
    solution_state: SolutionState | None = Query(default=None, alias="solutionState"),
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
            folder = await get_owned_folder(database, folder_id, user_id)
            folder_oid = folder["_id"]
            descendant_ids = await get_all_descendant_folder_ids(database, folder_oid)
            all_folder_ids = {folder_oid} | descendant_ids
            query["folderId"] = {"$in": [str(fid) for fid in all_folder_ids]}

    if solution_state is not None:
        matching_ids = await _solution_state_problem_ids(database, user_id, solution_state)
        if matching_ids is not None:
            query["_id"] = {"$in": matching_ids}

    total = await database["problems"].count_documents(query)
    skip = (page - 1) * page_size
    effective_sort_order = sort_order or "desc"
    if sort_by is None:
        cursor = database["problems"].find(query).sort("updatedAt", -1)
        items = await cursor.skip(skip).limit(page_size).to_list(length=page_size)
    elif sort_by == "addDate":
        direction = 1 if effective_sort_order == "asc" else -1
        cursor = database["problems"].find(query).sort([("createdAt", direction), ("_id", 1)])
        items = await cursor.skip(skip).limit(page_size).to_list(length=page_size)
    else:
        all_items = await database["problems"].find(query).to_list(length=None)
        reverse = effective_sort_order == "desc"
        if sort_by == "lastTestDate":
            sorted_items = _sort_by_last_test_date(all_items, reverse=reverse)
        else:
            sorted_items = _sort_by_selection_score(all_items, reverse=reverse, settings=settings)
        items = sorted_items[skip : skip + page_size]

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
        folder = await get_owned_folder(database, payload.folderId, user_id)
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
        folder = await get_owned_folder(database, payload.folderId, user_id)
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


@router.patch("/{problem_id}/disabled", response_model=ProblemResponse)
async def set_problem_disabled(
    problem_id: str,
    payload: ToggleProblemDisabledRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProblemResponse:
    """Enable or disable a problem, protected by teacher password validation."""
    problem = await get_owned_problem(
        database, problem_id, current_user["_id"], allow_deleted=False
    )

    teacher_password_hash = await _ensure_teacher_password_hash(
        current_user, database, settings
    )
    ok = verify_password(payload.teacherPassword, teacher_password_hash)
    log_teacher_password_event(
        "disable_verify_attempt",
        user_id=str(current_user["_id"]),
        username=current_user["username"],
        success=ok,
    )
    if not ok:
        raise ApiError(401, "UNAUTHENTICATED", "Incorrect teacher password")

    now = datetime.now(UTC)
    await database["problems"].update_one(
        {"_id": problem["_id"]},
        {"$set": {"isDisabled": payload.isDisabled, "updatedAt": now}},
    )

    updated_problem = deepcopy(problem)
    updated_problem["isDisabled"] = payload.isDisabled
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


@router.post("/{problem_id}/solution-regeneration", response_model=SolutionStatusResponse)
async def regenerate_solution(
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
    status = await regenerate_solution_task_for_problem(
        database,
        str(problem["_id"]),
        str(current_user["_id"]),
    )
    return SolutionStatusResponse(status=status)


@router.get("/{problem_id}/tracking", response_model=ProblemTrackingResponse)
async def get_problem_tracking(
    problem_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProblemTrackingResponse:
    problem_doc = await get_owned_problem(
        database,
        problem_id,
        current_user["_id"],
        allow_deleted=False,
    )
    problem_model = problem_document_to_model(problem_doc)
    config = _practice_selection_config(settings)
    now = datetime.now(UTC)
    breakdown = compute_problem_weight_breakdown(problem_model, config, now)
    return ProblemTrackingResponse(
        problemId=str(problem_doc["_id"]),
        tracking=_serialize_tracking(problem_doc),
        practiceWeight=PracticeWeightPayload(
            lastWrong=breakdown.lastWrong,
            failure=breakdown.failure,
            recency=breakdown.recency,
            total=breakdown.total,
        ),
    )
