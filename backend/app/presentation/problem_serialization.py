from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from app.domain.models import Problem, ProblemType
from app.presentation.errors import ApiError
from app.presentation.helpers import build_problem_image_url
from app.presentation.schemas import CorrectAnswerPayload, UTCDatetime


class TrackingPayload(BaseModel):
    exposureCount: int
    correctCount: int
    failedCount: int
    lastTestedAt: UTCDatetime | None
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
    subject: str = "math"
    graphDsl: str | None = None
    tags: list[str]
    tracking: TrackingPayload
    isDeleted: bool
    deletedAt: UTCDatetime | None
    isDisabled: bool
    createdAt: UTCDatetime
    updatedAt: UTCDatetime
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


class PracticeWeightPayload(BaseModel):
    lastWrong: float
    failure: float
    recency: float
    total: float


class ProblemTrackingResponse(BaseModel):
    problemId: str
    tracking: TrackingPayload
    practiceWeight: PracticeWeightPayload | None = None


class ProblemTagsResponse(BaseModel):
    items: list[str]


class SolutionStatusResponse(BaseModel):
    status: str


class BulkSetFolderResponse(BaseModel):
    ok: bool


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


def problem_document_to_model(problem: Mapping[str, Any]) -> Problem:
    try:
        origin = dict(problem.get("origin") or {})
        preview_id = origin.get("previewId")
        if preview_id is not None:
            origin["previewId"] = str(preview_id)
        return Problem.model_validate(
            {
                "id": str(problem["_id"]),
                "userId": str(problem["userId"]),
                "text": problem["text"],
                "problemType": problem["problemType"],
                "subject": problem.get("subject", "math"),
                "graphDsl": problem.get("graphDsl"),
                "correctAnswer": problem["correctAnswer"],
                "tags": list(problem.get("tags", [])),
                "sourceImage": problem.get("sourceImage"),
                "origin": origin,
                "tracking": problem.get("tracking", {}),
                "isDeleted": problem.get("isDeleted", False),
                "deletedAt": problem.get("deletedAt"),
                "isDisabled": problem.get("isDisabled", False),
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


def _serialize_problem_summary(problem: dict[str, Any]) -> ProblemSummaryPayload:
    folder_id = problem.get("folderId")
    return ProblemSummaryPayload(
        id=str(problem["_id"]),
        text=str(problem["text"]),
        problemType=ProblemType(problem["problemType"]),
        subject=str(problem.get("subject", "math")),
        graphDsl=problem.get("graphDsl"),
        tags=[str(tag) for tag in problem.get("tags", [])],
        tracking=_serialize_tracking(problem),
        isDeleted=bool(problem.get("isDeleted", False)),
        deletedAt=problem.get("deletedAt"),
        isDisabled=bool(problem.get("isDisabled", False)),
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
