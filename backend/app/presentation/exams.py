from __future__ import annotations

from base64 import b64encode
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from random import Random
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, ValidationError
from pymongo.asynchronous.database import AsyncDatabase

from app.domain.models import (
    ExamItem,
    ExamState,
    GradingStatus,
    Problem,
    ProblemType,
    SelectionPolicyConfig,
)
from app.domain.normalization import normalize_answer
from app.domain.scoring import compute_summary
from app.domain.selection import select_problems
from app.domain.state import transition_exam_state
from app.infrastructure.storage.mongo import Document, MongoClientAdapter, get_mongo_adapter
from app.infrastructure.storage.s3 import S3StorageAdapter, StorageObjectNotFoundError
from app.infrastructure.vlm.client import VLMClient, VLMError
from app.presentation.deps import get_current_user, get_database
from app.presentation.errors import ApiError
from app.presentation.schemas import CorrectAnswerPayload, SourceImagePayload

router = APIRouter(prefix="/exams", tags=["exams"])

DEFAULT_SELECTION_POLICY = SelectionPolicyConfig(recencyWeight=1.0, failureWeight=1.0)


class CreateExamRequest(BaseModel):
    maxProblemCount: int = Field(ge=1, le=100)


class SaveAnswerRequest(BaseModel):
    answer: str | None = None


class SelfReportRequest(BaseModel):
    isCorrect: bool


class ExamProblemPayload(BaseModel):
    text: str
    problemType: ProblemType
    graphDsl: str | None = None
    correctAnswer: CorrectAnswerPayload | None = None
    sourceImage: SourceImagePayload | None = None
    imageUrl: str | None = None


class ExamAnswerPayload(BaseModel):
    raw: str | None = None
    savedAt: datetime | None = None


class ExamGradingPayload(BaseModel):
    status: GradingStatus
    method: str | None = None
    isCorrect: bool | None = None
    score: float | None = None
    feedback: str | None = None
    providerModel: str | None = None
    rawProviderResponse: dict[str, Any] | None = None
    gradedAt: datetime | None = None
    retryCount: int = 0
    selfReportedCorrect: bool | None = None


class ExamItemPayload(BaseModel):
    itemId: str
    order: int
    problemId: str
    problem: ExamProblemPayload
    answer: ExamAnswerPayload
    grading: ExamGradingPayload


class SelectionPolicyPayload(BaseModel):
    recencyWeight: float
    failureWeight: float


class ExamConfigSnapshotPayload(BaseModel):
    maxProblemCount: int
    selectionPolicy: SelectionPolicyPayload
    generatedAt: datetime


class ExamSummaryPayload(BaseModel):
    totalProblems: int
    answeredProblems: int
    gradedProblems: int
    pendingProblems: int
    correctProblems: int
    failedProblems: int
    score: float | None = None


class ExamPayload(BaseModel):
    id: str
    state: ExamState
    configSnapshot: ExamConfigSnapshotPayload
    items: list[ExamItemPayload]
    summary: ExamSummaryPayload
    createdAt: datetime
    startedAt: datetime | None = None
    submittedAt: datetime | None = None
    updatedAt: datetime


class CreateExamResponse(BaseModel):
    exam: ExamPayload


class ExamResponse(BaseModel):
    exam: ExamPayload


class SaveAnswerResponse(BaseModel):
    item: ExamItemPayload


class SelfReportResponse(BaseModel):
    item: ExamItemPayload
    summary: ExamSummaryPayload


class ExamHistoryItemPayload(BaseModel):
    id: str
    state: ExamState
    createdAt: datetime
    submittedAt: datetime
    summary: ExamSummaryPayload


class ExamHistoryResponse(BaseModel):
    items: list[ExamHistoryItemPayload]
    page: int
    pageSize: int
    total: int


DatabaseDependency = Annotated[AsyncDatabase[Document], Depends(get_database)]
CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]


def get_exam_mongo_adapter() -> MongoClientAdapter:
    return get_mongo_adapter()


async def get_exam_vlm_client() -> AsyncIterator[VLMClient]:
    client = VLMClient()
    try:
        yield client
    finally:
        await client.aclose()


def get_exam_storage() -> S3StorageAdapter:
    return S3StorageAdapter()


AdapterDependency = Annotated[MongoClientAdapter, Depends(get_exam_mongo_adapter)]
VLMDependency = Annotated[VLMClient, Depends(get_exam_vlm_client)]
StorageDependency = Annotated[S3StorageAdapter, Depends(get_exam_storage)]


def _parse_object_id(raw_id: str, *, resource_name: str) -> ObjectId:
    if not ObjectId.is_valid(raw_id):
        raise ApiError(404, "NOT_FOUND", f"{resource_name} not found")
    return ObjectId(raw_id)


def _serialize_exam_summary(summary: Mapping[str, Any]) -> ExamSummaryPayload:
    return ExamSummaryPayload(
        totalProblems=int(summary.get("totalProblems", 0)),
        answeredProblems=int(summary.get("answeredProblems", 0)),
        gradedProblems=int(summary.get("gradedProblems", 0)),
        pendingProblems=int(summary.get("pendingProblems", 0)),
        correctProblems=int(summary.get("correctProblems", 0)),
        failedProblems=int(summary.get("failedProblems", 0)),
        score=summary.get("score"),
    )


def _build_problem_image_url(problem_id: Any) -> str:
    return f"/api/v1/problems/{problem_id}/image"


def _serialize_exam_item(
    item: Mapping[str, Any],
    *,
    include_correct_answer: bool,
) -> ExamItemPayload:
    snapshot = dict(item.get("problemSnapshot", {}))
    source_image = snapshot.get("sourceImage")
    grading = dict(item.get("grading", {}))
    answer = dict(item.get("answer", {}))
    return ExamItemPayload(
        itemId=str(item["itemId"]),
        order=int(item["order"]),
        problemId=str(item["problemId"]),
        problem=ExamProblemPayload(
            text=str(snapshot.get("text", "")),
            problemType=ProblemType(snapshot["problemType"]),
            graphDsl=snapshot.get("graphDsl"),
            correctAnswer=(
                CorrectAnswerPayload.model_validate(snapshot.get("correctAnswer", {}))
                if include_correct_answer and snapshot.get("correctAnswer") is not None
                else None
            ),
            sourceImage=(
                SourceImagePayload.model_validate(source_image)
                if include_correct_answer and source_image is not None
                else None
            ),
            imageUrl=_build_problem_image_url(item["problemId"]) if source_image is not None else None,
        ),
        answer=ExamAnswerPayload(
            raw=answer.get("raw"),
            savedAt=answer.get("savedAt"),
        ),
        grading=ExamGradingPayload(
            status=GradingStatus(grading.get("status", GradingStatus.UNGRADED.value)),
            method=grading.get("method"),
            isCorrect=grading.get("isCorrect"),
            score=grading.get("score"),
            feedback=grading.get("feedback"),
            providerModel=grading.get("providerModel"),
            rawProviderResponse=grading.get("rawProviderResponse"),
            gradedAt=grading.get("gradedAt"),
            retryCount=int(grading.get("retryCount", 0)),
            selfReportedCorrect=grading.get("selfReportedCorrect"),
        ),
    )


def _serialize_exam(exam: Mapping[str, Any]) -> ExamPayload:
    include_correct_answer = str(exam.get("state")) == ExamState.SUBMITTED.value
    config_snapshot = dict(exam.get("configSnapshot", {}))
    selection_policy = dict(config_snapshot.get("selectionPolicy", {}))
    return ExamPayload(
        id=str(exam["_id"]),
        state=ExamState(exam["state"]),
        configSnapshot=ExamConfigSnapshotPayload(
            maxProblemCount=int(config_snapshot.get("maxProblemCount", 0)),
            selectionPolicy=SelectionPolicyPayload(
                recencyWeight=float(selection_policy.get("recencyWeight", 1.0)),
                failureWeight=float(selection_policy.get("failureWeight", 1.0)),
            ),
            generatedAt=config_snapshot["generatedAt"],
        ),
        items=[
            _serialize_exam_item(item, include_correct_answer=include_correct_answer)
            for item in exam.get("items", [])
        ],
        summary=_serialize_exam_summary(dict(exam.get("summary", {}))),
        createdAt=exam["createdAt"],
        startedAt=exam.get("startedAt"),
        submittedAt=exam.get("submittedAt"),
        updatedAt=exam["updatedAt"],
    )


def _problem_document_to_model(problem: Mapping[str, Any]) -> Problem:
    try:
        return Problem.model_validate(
            {
                "id": str(problem["_id"]),
                "userId": str(problem["userId"]),
                "text": problem["text"],
                "problemType": problem["problemType"],
                "graphDsl": problem.get("graphDsl"),
                "correctAnswer": problem["correctAnswer"],
                "tags": list(problem.get("tags", [])),
                "sourceImage": problem.get("sourceImage"),
                "origin": problem.get("origin", {}),
                "tracking": problem.get("tracking", {}),
                "isDeleted": problem.get("isDeleted", False),
                "deletedAt": problem.get("deletedAt"),
                "createdAt": problem["createdAt"],
                "updatedAt": problem["updatedAt"],
            }
        )
    except ValidationError as exc:
        raise ApiError(
            422,
            "INVALID_PROBLEM_DATA",
            "Problem contains invalid data for exam creation",
            details={
                "problemId": str(problem.get("_id", "")),
                "errors": exc.errors(include_url=False),
            },
        ) from exc


def _build_exam_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    models = [
        ExamItem.model_validate(
            {
                **item,
                "problemId": str(item["problemId"]),
            }
        )
        for item in items
    ]
    return compute_summary(models).model_dump()


def _make_exam_item(problem: Mapping[str, Any], *, order: int) -> dict[str, Any]:
    return {
        "itemId": str(ObjectId()),
        "order": order,
        "problemId": problem["_id"],
        "problemSnapshot": {
            "text": problem["text"],
            "problemType": problem["problemType"],
            "graphDsl": problem.get("graphDsl"),
            "correctAnswer": deepcopy(problem["correctAnswer"]),
            "sourceImage": deepcopy(problem.get("sourceImage")),
        },
        "answer": {
            "raw": None,
            "savedAt": None,
        },
        "grading": {
            "status": GradingStatus.UNGRADED.value,
            "method": None,
            "isCorrect": None,
            "score": None,
            "feedback": None,
            "providerModel": None,
            "rawProviderResponse": None,
            "gradedAt": None,
            "retryCount": 0,
            "selfReportedCorrect": None,
        },
    }


async def _get_owned_exam(
    database: AsyncDatabase[Document],
    exam_id: str,
    user_id: ObjectId,
) -> dict[str, Any]:
    object_id = _parse_object_id(exam_id, resource_name="Exam")
    exam = await database["exams"].find_one({"_id": object_id})
    if exam is None:
        raise ApiError(404, "NOT_FOUND", "Exam not found")
    if exam.get("userId") != user_id:
        raise ApiError(403, "FORBIDDEN", "Forbidden")
    return exam


def _find_item(exam: Mapping[str, Any], item_id: str) -> tuple[int, dict[str, Any]]:
    for index, item in enumerate(exam.get("items", [])):
        if str(item.get("itemId")) == item_id:
            return index, deepcopy(item)
    raise ApiError(404, "NOT_FOUND", "Exam item not found")


def _grade_objective_item(item: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    answer = dict(item.get("answer", {}))
    answer_raw = answer.get("raw")
    snapshot = dict(item.get("problemSnapshot", {}))
    if answer_raw is None:
        is_correct = False
    else:
        problem_type = ProblemType(snapshot["problemType"])
        normalized = normalize_answer(str(answer_raw), problem_type)
        correct_answer = dict(snapshot.get("correctAnswer", {}))
        if problem_type == ProblemType.MULTI_CHOICE:
            is_correct = normalized.normalizedSet == list(correct_answer.get("normalizedSet", []))
        else:
            is_correct = normalized.normalizedText == str(correct_answer.get("normalizedText", ""))

    grading = dict(item.get("grading", {}))
    grading.update(
        {
            "status": GradingStatus.CORRECT.value if is_correct else GradingStatus.INCORRECT.value,
            "method": "normalized-match",
            "isCorrect": is_correct,
            "score": 1.0 if is_correct else 0.0,
            "feedback": None,
            "providerModel": None,
            "rawProviderResponse": None,
            "gradedAt": now,
            "retryCount": 0,
            "selfReportedCorrect": None,
        }
    )
    graded_item = deepcopy(dict(item))
    graded_item["grading"] = grading
    return graded_item


async def _load_item_image_base64(item: Mapping[str, Any], storage: S3StorageAdapter) -> str | None:
    snapshot = dict(item.get("problemSnapshot", {}))
    source_image = dict(snapshot.get("sourceImage") or {})
    bucket = source_image.get("bucket")
    object_key = source_image.get("objectKey")
    if not bucket or not object_key:
        return None
    try:
        image_bytes = storage.get_object(str(bucket), str(object_key))
    except StorageObjectNotFoundError:
        return None
    return b64encode(image_bytes).decode("ascii")


async def _grade_short_answer_item(
    item: Mapping[str, Any],
    *,
    vlm_client: VLMClient,
    storage: S3StorageAdapter,
    now: datetime,
) -> dict[str, Any]:
    graded_item = deepcopy(dict(item))
    answer = dict(graded_item.get("answer", {}))
    answer_raw = answer.get("raw")
    if answer_raw is None:
        graded_item["grading"] = {
            "status": GradingStatus.INCORRECT.value,
            "method": "normalized-match",
            "isCorrect": False,
            "score": 0.0,
            "feedback": None,
            "providerModel": None,
            "rawProviderResponse": None,
            "gradedAt": now,
            "retryCount": 0,
            "selfReportedCorrect": None,
        }
        return graded_item

    snapshot = dict(graded_item.get("problemSnapshot", {}))
    correct_answer = dict(snapshot.get("correctAnswer", {}))
    image_base64 = await _load_item_image_base64(graded_item, storage)

    retry_count = 0
    while True:
        try:
            result = await vlm_client.grade_short_answer(
                image_base64=image_base64,
                user_answer=str(answer_raw),
                correct_answer=str(correct_answer.get("display", "")),
            )
            graded_item["grading"] = {
                "status": GradingStatus.CORRECT.value if result.is_correct else GradingStatus.INCORRECT.value,
                "method": "vlm",
                "isCorrect": result.is_correct,
                "score": 1.0 if result.is_correct else 0.0,
                "feedback": result.feedback,
                "providerModel": result.model,
                "rawProviderResponse": result.raw_provider_response,
                "gradedAt": now,
                "retryCount": retry_count,
                "selfReportedCorrect": None,
            }
            return graded_item
        except VLMError as exc:
            if exc.retryable and retry_count < 1:
                retry_count += 1
                continue
            graded_item["grading"] = {
                "status": GradingStatus.PENDING_REVIEW.value,
                "method": "vlm",
                "isCorrect": None,
                "score": None,
                "feedback": str(exc),
                "providerModel": None,
                "rawProviderResponse": exc.raw_provider_response,
                "gradedAt": now,
                "retryCount": retry_count,
                "selfReportedCorrect": None,
            }
            return graded_item
        except Exception as exc:
            graded_item["grading"] = {
                "status": GradingStatus.PENDING_REVIEW.value,
                "method": "vlm",
                "isCorrect": None,
                "score": None,
                "feedback": str(exc),
                "providerModel": None,
                "rawProviderResponse": None,
                "gradedAt": now,
                "retryCount": retry_count,
                "selfReportedCorrect": None,
            }
            return graded_item


async def _grade_item(
    item: Mapping[str, Any],
    *,
    vlm_client: VLMClient,
    storage: S3StorageAdapter,
    now: datetime,
) -> dict[str, Any]:
    snapshot = dict(item.get("problemSnapshot", {}))
    problem_type = ProblemType(snapshot["problemType"])
    if problem_type == ProblemType.SHORT_ANSWER:
        return await _grade_short_answer_item(item, vlm_client=vlm_client, storage=storage, now=now)
    return _grade_objective_item(item, now=now)


def _build_tracking_update(tracking: Mapping[str, Any], *, now: datetime, is_correct: bool) -> dict[str, Any]:
    exposure_count = int(tracking.get("exposureCount", 0)) + 1
    correct_count = int(tracking.get("correctCount", 0)) + (1 if is_correct else 0)
    failed_count = int(tracking.get("failedCount", 0)) + (0 if is_correct else 1)
    return {
        "exposureCount": exposure_count,
        "correctCount": correct_count,
        "failedCount": failed_count,
        "lastTestedAt": now,
        "lastAttemptCorrect": is_correct,
    }


@router.post("", response_model=CreateExamResponse, status_code=201)
async def create_exam(
    payload: CreateExamRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
) -> CreateExamResponse:
    query = {"userId": current_user["_id"], "state": ExamState.IN_PROGRESS.value}

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
            [_problem_document_to_model(problem) for problem in eligible_documents],
            payload.maxProblemCount,
            DEFAULT_SELECTION_POLICY,
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
            _make_exam_item(problem, order=index)
            for index, problem in enumerate(selected_documents, start=1)
        ]
        exam = {
            "_id": ObjectId(),
            "userId": current_user["_id"],
            "state": ExamState.IN_PROGRESS.value,
            "configSnapshot": {
                "maxProblemCount": payload.maxProblemCount,
                "selectionPolicy": DEFAULT_SELECTION_POLICY.model_dump(),
                "generatedAt": now,
            },
            "items": items,
            "summary": _build_exam_summary(items),
            "createdAt": now,
            "startedAt": None,
            "submittedAt": None,
            "updatedAt": now,
        }
        await database["exams"].insert_one(exam, session=session)
        return exam

    async with adapter.start_session() as session:
        exam = await session.with_transaction(_transaction)
    return CreateExamResponse(exam=_serialize_exam(exam))


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

    return ExamResponse(exam=_serialize_exam(exam))


@router.get("/{exam_id}", response_model=ExamResponse)
async def get_exam_detail(
    exam_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ExamResponse:
    exam = await _get_owned_exam(database, exam_id, current_user["_id"])
    return ExamResponse(exam=_serialize_exam(exam))


@router.patch("/{exam_id}/items/{item_id}/answer", response_model=SaveAnswerResponse)
async def save_exam_answer(
    exam_id: str,
    item_id: str,
    payload: SaveAnswerRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> SaveAnswerResponse:
    exam = await _get_owned_exam(database, exam_id, current_user["_id"])
    if exam.get("state") != ExamState.IN_PROGRESS.value:
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

    item_index, item = _find_item(exam, item_id)
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
                "summary": _build_exam_summary(items),
                "updatedAt": now,
            }
        },
    )
    return SaveAnswerResponse(item=_serialize_exam_item(item, include_correct_answer=False))


@router.post("/{exam_id}/submit", response_model=ExamResponse)
async def submit_exam(
    exam_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
    vlm_client: VLMDependency,
    storage: StorageDependency,
) -> ExamResponse:
    exam = await _get_owned_exam(database, exam_id, current_user["_id"])
    if exam.get("state") != ExamState.IN_PROGRESS.value:
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

    grading_time = datetime.now(UTC)
    graded_items: list[dict[str, Any]] = []
    for item in exam.get("items", []):
        graded_items.append(
            await _grade_item(item, vlm_client=vlm_client, storage=storage, now=grading_time)
        )
    summary = _build_exam_summary(graded_items)

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
        current_items = current_exam.get("items", [])
        for i, original_item in enumerate(original_items):
            if i >= len(current_items):
                break
            original_answer = dict(original_item.get("answer", {})).get("raw")
            current_answer = dict(current_items[i].get("answer", {})).get("raw")
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
            tracking_update = _build_tracking_update(
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

    return ExamResponse(exam=_serialize_exam(submitted_exam))


@router.post("/{exam_id}/items/{item_id}/self-report", response_model=SelfReportResponse)
async def self_report_exam_item(
    exam_id: str,
    item_id: str,
    payload: SelfReportRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
) -> SelfReportResponse:
    exam = await _get_owned_exam(database, exam_id, current_user["_id"])
    if exam.get("state") != ExamState.SUBMITTED.value:
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not submitted")

    item_index, item = _find_item(exam, item_id)
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
    summary = _build_exam_summary(items)

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
            tracking_update = _build_tracking_update(
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
        item=_serialize_exam_item(updated_item, include_correct_answer=True),
        summary=_serialize_exam_summary(dict(updated_exam["summary"])),
    )


@router.get("", response_model=ExamHistoryResponse)
async def list_exam_history(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> ExamHistoryResponse:
    query = {"userId": current_user["_id"], "state": ExamState.SUBMITTED.value}
    total = await database["exams"].count_documents(query)
    cursor = database["exams"].find(query).sort("submittedAt", -1)
    cursor = cursor.skip((page - 1) * page_size).limit(page_size)
    documents = await cursor.to_list(length=page_size)
    return ExamHistoryResponse(
        items=[
            ExamHistoryItemPayload(
                id=str(document["_id"]),
                state=ExamState(document["state"]),
                createdAt=document["createdAt"],
                submittedAt=document["submittedAt"],
                summary=_serialize_exam_summary(dict(document.get("summary", {}))),
            )
            for document in documents
        ],
        page=page,
        pageSize=page_size,
        total=total,
    )
