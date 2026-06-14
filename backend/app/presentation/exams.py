from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from random import Random
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query

from app.domain.models import ExamState, GradingStatus, ProblemType, SelectionPolicyConfig
from app.domain.selection import select_problems
from app.domain.state import transition_exam_state
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.storage.mongo import Document, MongoClientAdapter, get_mongo_adapter
from app.presentation.exam_grading import build_tracking_update, grade_item
from app.presentation.exam_helpers import (
    build_exam_summary,
    find_item,
    get_owned_exam,
    make_exam_item,
    problem_document_to_model,
)
from app.presentation.deps import (
    DatabaseDependency,
    GradingVLMDependency,
    StorageDependency,
    get_current_user,
    get_grading_vlm_client,
    get_s3_storage,
)
from app.presentation.errors import ApiError
from app.presentation.exam_serialization import (
    CreateExamRequest,
    CreateExamResponse,
    ExamHistoryItemPayload,
    ExamHistoryResponse,
    ExamResponse,
    SaveAnswerRequest,
    SaveAnswerResponse,
    SelfReportRequest,
    SelfReportResponse,
    serialize_exam,
    serialize_exam_item,
    serialize_exam_summary,
)

router = APIRouter(prefix="/exams", tags=["exams"])


CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


def get_exam_mongo_adapter() -> MongoClientAdapter:
    return get_mongo_adapter()


AdapterDependency = Annotated[MongoClientAdapter, Depends(get_exam_mongo_adapter)]
get_exam_storage = get_s3_storage
VLMDependency = GradingVLMDependency
get_exam_vlm_client = get_grading_vlm_client


@router.post("", response_model=CreateExamResponse, status_code=201)
async def create_exam(
    payload: CreateExamRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
    settings: SettingsDependency,
) -> CreateExamResponse:
    query = {"userId": current_user["_id"], "state": ExamState.IN_PROGRESS.value}
    selection_policy = SelectionPolicyConfig(
        recencyWeight=1.0,
        failureWeight=1.0,
        minProblemAgeDays=settings.problem_selection_min_age_days,
    )

    async def _transaction(session: Any) -> dict[str, Any]:
        existing = await database["exams"].find_one(query, session=session)
        if existing is not None:
            raise ApiError(409, "ACTIVE_EXAM_EXISTS", "An active exam already exists")

        problem_documents = await database["problems"].find(
            {"userId": current_user["_id"], "isDeleted": False},
            session=session,
        ).to_list(length=None)
        eligible_documents = [
            problem
            for problem in problem_documents
            if problem.get("correctAnswer")
            and str(problem.get("correctAnswer", {}).get("display", "")).strip()
        ]
        if not eligible_documents:
            raise ApiError(422, "NO_ELIGIBLE_PROBLEMS", "No eligible problems available")

        selected_models = select_problems(
            [problem_document_to_model(problem) for problem in eligible_documents],
            payload.maxProblemCount,
            selection_policy,
            rng=Random(),
        )
        if not selected_models:
            raise ApiError(422, "NO_ELIGIBLE_PROBLEMS", "No eligible problems available")

        document_by_id = {str(problem["_id"]): problem for problem in eligible_documents}
        selected_documents = [
            document_by_id[problem.id]
            for problem in selected_models
            if problem.id is not None and problem.id in document_by_id
        ]

        now = datetime.now(UTC)
        items = [
            make_exam_item(problem, order=index)
            for index, problem in enumerate(selected_documents, start=1)
        ]
        exam = {
            "_id": ObjectId(),
            "userId": current_user["_id"],
            "state": ExamState.IN_PROGRESS.value,
            "configSnapshot": {
                "maxProblemCount": payload.maxProblemCount,
                "selectionPolicy": selection_policy.model_dump(),
                "generatedAt": now,
            },
            "items": items,
            "summary": build_exam_summary(items),
            "createdAt": now,
            "startedAt": None,
            "submittedAt": None,
            "updatedAt": now,
        }
        await database["exams"].insert_one(exam, session=session)
        return exam

    async with adapter.start_session() as session:
        exam = await session.with_transaction(_transaction)
    return CreateExamResponse(exam=serialize_exam(exam))


@router.get("/active", response_model=ExamResponse)
async def get_active_exam(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ExamResponse:
    exam = await database["exams"].find_one(
        {"userId": current_user["_id"], "state": ExamState.IN_PROGRESS.value}
    )
    if exam is None:
        raise ApiError(404, "NOT_FOUND", "Active exam not found")

    if exam.get("startedAt") is None:
        now = datetime.now(UTC)
        await database["exams"].update_one(
            {"_id": exam["_id"], "startedAt": None},
            {"$set": {"startedAt": now, "updatedAt": now}},
        )
        exam["startedAt"] = now
        exam["updatedAt"] = now

    return ExamResponse(exam=serialize_exam(exam))


@router.get("/{exam_id}", response_model=ExamResponse)
async def get_exam_detail(
    exam_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ExamResponse:
    exam = await get_owned_exam(database, exam_id, current_user["_id"])
    return ExamResponse(exam=serialize_exam(exam))


@router.patch("/{exam_id}/items/{item_id}/answer", response_model=SaveAnswerResponse)
async def save_exam_answer(
    exam_id: str,
    item_id: str,
    payload: SaveAnswerRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> SaveAnswerResponse:
    exam = await get_owned_exam(database, exam_id, current_user["_id"])
    if exam.get("state") != ExamState.IN_PROGRESS.value:
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

    item_index, item = find_item(exam, item_id)
    now = datetime.now(UTC)
    item["answer"] = {
        "raw": payload.answer,
        "savedAt": now,
    }
    items = deepcopy(list(exam.get("items", [])))
    items[item_index] = item
    await database["exams"].update_one(
        {"_id": exam["_id"]},
        {
            "$set": {
                "items": items,
                "summary": build_exam_summary(items),
                "updatedAt": now,
            }
        },
    )
    return SaveAnswerResponse(item=serialize_exam_item(item, include_correct_answer=False))


@router.post("/{exam_id}/submit", response_model=ExamResponse)
async def submit_exam(
    exam_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
    vlm_client: VLMDependency,
    storage: StorageDependency,
) -> ExamResponse:
    exam = await get_owned_exam(database, exam_id, current_user["_id"])
    if exam.get("state") != ExamState.IN_PROGRESS.value:
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

    grading_time = datetime.now(UTC)
    graded_items: list[dict[str, Any]] = []
    for item in exam.get("items", []):
        graded_items.append(
            await grade_item(item, vlm_client=vlm_client, storage=storage, now=grading_time)
        )
    summary = build_exam_summary(graded_items)

    async def _transaction(session: Any) -> dict[str, Any]:
        current_exam = await database["exams"].find_one(
            {"_id": exam["_id"], "userId": current_user["_id"]},
            session=session,
        )
        if current_exam is None:
            raise ApiError(404, "NOT_FOUND", "Exam not found")
        if current_exam.get("state") != ExamState.IN_PROGRESS.value:
            raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

        original_items = exam.get("items", [])
        current_items_by_id = {
            str(item.get("itemId")): item
            for item in current_exam.get("items", [])
        }
        for original_item in original_items:
            item_id = str(original_item.get("itemId"))
            current_item = current_items_by_id.get(item_id)
            if current_item is None:
                raise ApiError(
                    409,
                    "ANSWERS_MODIFIED_DURING_GRADING",
                    "Answers were modified while grading was in progress. Please retry submission.",
                )
            original_answer = dict(original_item.get("answer", {})).get("raw")
            current_answer = dict(current_item.get("answer", {})).get("raw")
            if original_answer != current_answer:
                raise ApiError(
                    409,
                    "ANSWERS_MODIFIED_DURING_GRADING",
                    "Answers were modified while grading was in progress. Please retry submission.",
                )

        submitted_at = datetime.now(UTC)
        next_state = transition_exam_state(ExamState(current_exam["state"]), ExamState.SUBMITTED)
        await database["exams"].update_one(
            {"_id": current_exam["_id"]},
            {
                "$set": {
                    "state": next_state.value,
                    "items": graded_items,
                    "summary": summary,
                    "submittedAt": submitted_at,
                    "updatedAt": submitted_at,
                }
            },
            session=session,
        )

        for item in graded_items:
            grading = dict(item.get("grading", {}))
            if grading.get("status") == GradingStatus.PENDING_REVIEW.value:
                continue
            is_correct = bool(grading.get("isCorrect"))
            problem = await database["problems"].find_one(
                {"_id": item["problemId"], "userId": current_user["_id"]},
                session=session,
            )
            if problem is None:
                continue
            tracking_update = build_tracking_update(
                dict(problem.get("tracking", {})),
                now=submitted_at,
                is_correct=is_correct,
            )
            await database["problems"].update_one(
                {"_id": problem["_id"]},
                {"$set": {"tracking": tracking_update, "updatedAt": submitted_at}},
                session=session,
            )

        updated_exam = deepcopy(current_exam)
        updated_exam.update(
            {
                "state": next_state.value,
                "items": graded_items,
                "summary": summary,
                "submittedAt": submitted_at,
                "updatedAt": submitted_at,
            }
        )
        return updated_exam

    try:
        async with adapter.start_session() as session:
            submitted_exam = await session.with_transaction(_transaction)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(500, "SUBMISSION_FAILED", "Failed to submit exam") from exc

    return ExamResponse(exam=serialize_exam(submitted_exam))


@router.post("/{exam_id}/discard", response_model=ExamResponse)
async def discard_exam(
    exam_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ExamResponse:
    exam = await get_owned_exam(database, exam_id, current_user["_id"])
    if exam.get("state") != ExamState.IN_PROGRESS.value:
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

    discarded_at = datetime.now(UTC)
    next_state = transition_exam_state(ExamState(exam["state"]), ExamState.DISCARDED)
    await database["exams"].update_one(
        {"_id": exam["_id"]},
        {
            "$set": {
                "state": next_state.value,
                "discardedAt": discarded_at,
                "updatedAt": discarded_at,
            }
        },
    )
    updated_exam = deepcopy(exam)
    updated_exam.update(
        {
            "state": next_state.value,
            "discardedAt": discarded_at,
            "updatedAt": discarded_at,
        }
    )
    return ExamResponse(exam=serialize_exam(updated_exam))


@router.post("/{exam_id}/items/{item_id}/self-report", response_model=SelfReportResponse)
async def self_report_exam_item(
    exam_id: str,
    item_id: str,
    payload: SelfReportRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
) -> SelfReportResponse:
    exam = await get_owned_exam(database, exam_id, current_user["_id"])
    if exam.get("state") != ExamState.SUBMITTED.value:
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not submitted")

    item_index, item = find_item(exam, item_id)
    grading = dict(item.get("grading", {}))
    if grading.get("status") != GradingStatus.PENDING_REVIEW.value:
        raise ApiError(409, "ITEM_NOT_PENDING_REVIEW", "Exam item is not pending review")

    resolved_at = datetime.now(UTC)
    grading.update(
        {
            "status": GradingStatus.CORRECT.value if payload.isCorrect else GradingStatus.INCORRECT.value,
            "method": "self-report",
            "isCorrect": payload.isCorrect,
            "score": 1.0 if payload.isCorrect else 0.0,
            "gradedAt": resolved_at,
            "selfReportedCorrect": payload.isCorrect,
        }
    )
    updated_item = deepcopy(item)
    updated_item["grading"] = grading
    items = deepcopy(list(exam.get("items", [])))
    items[item_index] = updated_item
    summary = build_exam_summary(items)

    async def _transaction(session: Any) -> dict[str, Any]:
        current_exam = await database["exams"].find_one(
            {"_id": exam["_id"], "userId": current_user["_id"]},
            session=session,
        )
        if current_exam is None:
            raise ApiError(404, "NOT_FOUND", "Exam not found")
        if current_exam.get("state") != ExamState.SUBMITTED.value:
            raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not submitted")

        await database["exams"].update_one(
            {"_id": current_exam["_id"]},
            {"$set": {"items": items, "summary": summary, "updatedAt": resolved_at}},
            session=session,
        )

        problem = await database["problems"].find_one(
            {"_id": updated_item["problemId"], "userId": current_user["_id"]},
            session=session,
        )
        if problem is not None:
            tracking_update = build_tracking_update(
                dict(problem.get("tracking", {})),
                now=resolved_at,
                is_correct=payload.isCorrect,
            )
            await database["problems"].update_one(
                {"_id": problem["_id"]},
                {"$set": {"tracking": tracking_update, "updatedAt": resolved_at}},
                session=session,
            )

        updated_exam = deepcopy(current_exam)
        updated_exam.update({"items": items, "summary": summary, "updatedAt": resolved_at})
        return updated_exam

    async with adapter.start_session() as session:
        updated_exam = await session.with_transaction(_transaction)
    return SelfReportResponse(
        item=serialize_exam_item(updated_item, include_correct_answer=True),
        summary=serialize_exam_summary(dict(updated_exam["summary"])),
    )


@router.get("", response_model=ExamHistoryResponse)
async def list_exam_history(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    include_discarded: bool = Query(default=False, alias="includeDiscarded"),
) -> ExamHistoryResponse:
    states = [ExamState.SUBMITTED.value]
    if include_discarded:
        states.append(ExamState.DISCARDED.value)
    query = {
        "userId": current_user["_id"],
        "state": {"$in": states},
    }
    total = await database["exams"].count_documents(query)
    cursor = database["exams"].find(query).sort("updatedAt", -1)
    cursor = cursor.skip((page - 1) * page_size).limit(page_size)
    documents = await cursor.to_list(length=page_size)
    return ExamHistoryResponse(
        items=[
            ExamHistoryItemPayload(
                id=str(document["_id"]),
                state=ExamState(document["state"]),
                createdAt=document["createdAt"],
                submittedAt=document.get("submittedAt"),
                discardedAt=document.get("discardedAt"),
                summary=serialize_exam_summary(dict(document.get("summary", {}))),
            )
            for document in documents
        ],
        page=page,
        pageSize=page_size,
        total=total,
    )
