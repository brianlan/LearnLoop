from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.domain.models import GradingStatus, GradingMethod, ProblemType
from app.domain.normalization import compare_answers, normalize_answer
from app.domain.practice_selection import (
    PracticeSelectionConfig,
    get_eligible_practice_problems,
    select_practice_problem,
)
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.storage.mongo import Document
from app.infrastructure.storage.s3 import S3StorageAdapter
from app.infrastructure.vlm.client import VLMClient, VLMError
from app.presentation.deps import (
    DatabaseDependency,
    get_app_settings,
    get_current_user,
    get_grading_vlm_client,
    get_s3_storage,
)
from app.presentation.helpers import build_problem_image_url, load_source_image_base64, parse_object_id
from app.presentation.exam_helpers import problem_document_to_model
from app.presentation.errors import ApiError

router = APIRouter(prefix="/practice", tags=["practice"])


CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
StorageDependency = Annotated[S3StorageAdapter, Depends(get_s3_storage)]
VLMDependency = Annotated[VLMClient, Depends(get_grading_vlm_client)]


class PracticeNextResponse(BaseModel):
    status: str
    problem: dict[str, Any] | None = None


class PracticeAttemptRequest(BaseModel):
    problemId: str
    submittedAnswer: str


class PracticeAttemptResult(BaseModel):
    gradingStatus: str
    gradingMethod: str
    feedback: str | None = None


class PracticeAttemptDetail(BaseModel):
    submittedAnswer: str
    gradingStatus: str
    gradingMethod: str
    createdAt: datetime
    feedback: str | None = None


class PracticeHistorySummary(BaseModel):
    totalAttempts: int
    correctCount: int
    wrongCount: int
    lastPracticedAt: datetime | None = None
    lastResult: str | None = None


class PracticeHistoryItem(BaseModel):
    problemId: str
    problemText: str
    problemType: str
    imageUrl: str | None = None
    summary: PracticeHistorySummary
    attempts: list[PracticeAttemptDetail]


class PracticeHistoryResponse(BaseModel):
    items: list[PracticeHistoryItem]
    total: int
    hasMore: bool


class PracticeStatsResponse(BaseModel):
    practiceableCount: int


@router.get("/stats", response_model=PracticeStatsResponse)
async def get_practice_stats(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    settings: SettingsDependency,
) -> PracticeStatsResponse:
    problem_documents = await database["problems"].find(
        {"userId": current_user["_id"], "isDeleted": False}
    ).to_list(length=None)

    problem_models = [problem_document_to_model(p) for p in problem_documents]
    config = PracticeSelectionConfig(cooldown_days=settings.practice_cooldown_days)
    now = datetime.now(UTC)
    eligible = get_eligible_practice_problems(problem_models, config, now)

    return PracticeStatsResponse(practiceableCount=len(eligible))


@router.post("/next", response_model=PracticeNextResponse)
async def get_next_practice_problem(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    settings: SettingsDependency,
) -> PracticeNextResponse:
    problem_documents = await database["problems"].find(
        {"userId": current_user["_id"], "isDeleted": False}
    ).to_list(length=None)

    eligible_documents = [
        problem
        for problem in problem_documents
        if problem.get("correctAnswer")
        and str(problem.get("correctAnswer", {}).get("display", "")).strip()
    ]

    if not eligible_documents:
        return PracticeNextResponse(status="no_problems")

    config = PracticeSelectionConfig(
        cooldown_days=settings.practice_cooldown_days,
        last_wrong_weight=settings.practice_last_wrong_weight,
        failure_rate_weight=settings.practice_failure_rate_weight,
        recency_weight=settings.practice_recency_weight,
    )

    problem_models = [problem_document_to_model(p) for p in eligible_documents]
    now = datetime.now(UTC)

    result = select_practice_problem(problem_models, config, now)

    if result.status != "ok" or result.selected_problem is None:
        return PracticeNextResponse(status=result.status)

    selected_id = result.selected_problem.id
    document_by_id = {str(p["_id"]): p for p in eligible_documents}
    selected_document = document_by_id.get(selected_id)

    if selected_document is None:
        return PracticeNextResponse(status="no_problems")

    tracking = selected_document.get("tracking", {})
    tracking["exposureCount"] = tracking.get("exposureCount", 0) + 1

    await database["problems"].update_one(
        {"_id": selected_document["_id"]},
        {"$set": {"tracking": tracking, "updatedAt": now}},
    )

    problem_response = {
        "id": str(selected_document["_id"]),
        "text": selected_document["text"],
        "type": selected_document["problemType"],
    }

    if selected_document.get("sourceImage"):
        problem_response["imageUrl"] = build_problem_image_url(selected_document["_id"])

    if selected_document.get("graphDsl"):
        problem_response["graphDsl"] = selected_document["graphDsl"]

    return PracticeNextResponse(status="ok", problem=problem_response)


async def _grade_answer(
    problem: dict[str, Any],
    answer: str,
    vlm_client: VLMClient,
    storage: S3StorageAdapter,
    now: datetime,
) -> tuple[GradingStatus, GradingMethod, str | None]:
    problem_type = ProblemType(problem["problemType"])
    correct_answer = problem.get("correctAnswer", {})

    if problem_type != ProblemType.SHORT_ANSWER:
        normalized = normalize_answer(answer, problem_type)
        is_correct = compare_answers(normalized, correct_answer, problem_type)
        status = GradingStatus.CORRECT if is_correct else GradingStatus.INCORRECT
        return status, GradingMethod.NORMALIZED_MATCH, None

    image_base64 = load_source_image_base64(problem.get("sourceImage"), storage)
    for _ in range(2):
        try:
            result = await vlm_client.grade_short_answer(
                image_base64=image_base64,
                user_answer=answer,
                correct_answer=str(correct_answer.get("display", "")),
            )
            status = GradingStatus.CORRECT if result.is_correct else GradingStatus.INCORRECT
            return status, GradingMethod.VLM, result.feedback
        except VLMError as exc:
            if not exc.retryable:
                return GradingStatus.PENDING_REVIEW, GradingMethod.VLM, None
            continue
        except Exception:
            return GradingStatus.PENDING_REVIEW, GradingMethod.VLM, None
    return GradingStatus.PENDING_REVIEW, GradingMethod.VLM, None


@router.post("/attempts", response_model=PracticeAttemptResult, status_code=201)
async def submit_practice_attempt(
    payload: PracticeAttemptRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    vlm_client: VLMDependency,
    storage: StorageDependency,
) -> PracticeAttemptResult:
    problem_id = parse_object_id(payload.problemId, resource_name="Problem")
    problem = await database["problems"].find_one(
        {"_id": problem_id, "userId": current_user["_id"], "isDeleted": False}
    )
    if problem is None:
        raise ApiError(404, "NOT_FOUND", "Problem not found")

    now = datetime.now(UTC)
    grading_status, grading_method, feedback = await _grade_answer(
        problem, payload.submittedAnswer, vlm_client, storage, now
    )

    attempt = {
        "_id": ObjectId(),
        "userId": current_user["_id"],
        "problemId": problem_id,
        "submittedAnswer": payload.submittedAnswer,
        "gradingStatus": grading_status.value,
        "gradingMethod": grading_method.value,
        "createdAt": now,
    }
    if feedback is not None:
        attempt["feedback"] = feedback
    await database["practice_attempts"].insert_one(attempt)

    tracking = problem.get("tracking", {})
    is_correct = grading_status == GradingStatus.CORRECT
    tracking["correctCount"] = tracking.get("correctCount", 0) + (1 if is_correct else 0)
    tracking["failedCount"] = tracking.get("failedCount", 0) + (0 if is_correct else 1)
    tracking["lastTestedAt"] = now
    tracking["lastAttemptCorrect"] = is_correct

    await database["problems"].update_one(
        {"_id": problem_id},
        {"$set": {"tracking": tracking, "updatedAt": now}},
    )

    return PracticeAttemptResult(
        gradingStatus=grading_status.value,
        gradingMethod=grading_method.value,
        feedback=feedback,
    )


@router.get("/history", response_model=PracticeHistoryResponse)
async def get_practice_history(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PracticeHistoryResponse:
    attempts = await database["practice_attempts"].find(
        {"userId": current_user["_id"]}
    ).sort("createdAt", -1).to_list(length=None)

    if not attempts:
        return PracticeHistoryResponse(items=[], total=0, hasMore=False)

    problem_ids = [ObjectId(a["problemId"]) for a in attempts]
    problems = await database["problems"].find(
        {"_id": {"$in": problem_ids}}
    ).to_list(length=None)
    problems_by_id = {str(p["_id"]): p for p in problems}

    attempts_by_problem: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        pid = str(attempt["problemId"])
        attempts_by_problem.setdefault(pid, []).append(attempt)

    items = []
    for problem_id, problem_attempts in attempts_by_problem.items():
        problem = problems_by_id.get(problem_id)
        if problem is None:
            continue

        total = len(problem_attempts)
        correct = sum(1 for a in problem_attempts if a["gradingStatus"] == GradingStatus.CORRECT.value)
        wrong = sum(1 for a in problem_attempts if a["gradingStatus"] == GradingStatus.INCORRECT.value)

        last_practiced = max(a["createdAt"] for a in problem_attempts)
        last_result = problem_attempts[0]["gradingStatus"] if problem_attempts else None

        attempt_details = [
            PracticeAttemptDetail(
                submittedAnswer=a["submittedAnswer"],
                gradingStatus=a["gradingStatus"],
                gradingMethod=a["gradingMethod"],
                createdAt=a["createdAt"],
                feedback=a.get("feedback"),
            )
            for a in problem_attempts
        ]

        item = PracticeHistoryItem(
            problemId=problem_id,
            problemText=problem.get("text", ""),
            problemType=problem.get("problemType", ""),
            imageUrl=build_problem_image_url(problem["_id"]) if problem.get("sourceImage") else None,
            summary=PracticeHistorySummary(
                totalAttempts=total,
                correctCount=correct,
                wrongCount=wrong,
                lastPracticedAt=last_practiced,
                lastResult=last_result,
            ),
            attempts=attempt_details,
        )
        items.append(item)

    items.sort(key=lambda x: x.summary.lastPracticedAt or datetime.min, reverse=True)

    total_count = len(items)
    has_more = offset + limit < total_count
    paginated_items = items[offset : offset + limit]

    return PracticeHistoryResponse(items=paginated_items, total=total_count, hasMore=has_more)
