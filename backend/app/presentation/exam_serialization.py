from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import ExamState, GradingStatus, ProblemType
from app.presentation.helpers import build_problem_image_url
from app.presentation.schemas import CorrectAnswerPayload, SourceImagePayload, UTCDatetime


class CreateExamRequest(BaseModel):
    maxProblemCount: int = Field(ge=1, le=30)


class SaveAnswerRequest(BaseModel):
    answer: str | None = None


class SelfReportRequest(BaseModel):
    isCorrect: bool


class ExamProblemPayload(BaseModel):
    text: str
    problemType: ProblemType
    subject: str = "math"
    graphDsl: str | None = None
    correctAnswer: CorrectAnswerPayload | None = None
    sourceImage: SourceImagePayload | None = None
    imageUrl: str | None = None


class ExamAnswerPayload(BaseModel):
    raw: str | None = None
    savedAt: UTCDatetime | None = None


class ExamGradingPayload(BaseModel):
    status: GradingStatus
    method: str | None = None
    isCorrect: bool | None = None
    score: float | None = None
    feedback: str | None = None
    providerModel: str | None = None
    rawProviderResponse: dict[str, Any] | None = None
    gradedAt: UTCDatetime | None = None
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
    cooldownDays: int
    lastWrongWeight: float
    failureRateWeight: float
    recencyWeight: float
    minProblemAgeDays: int


class ExamConfigSnapshotPayload(BaseModel):
    maxProblemCount: int
    selectionPolicy: SelectionPolicyPayload
    generatedAt: UTCDatetime


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
    createdAt: UTCDatetime
    startedAt: UTCDatetime | None = None
    submittedAt: UTCDatetime | None = None
    discardedAt: UTCDatetime | None = None
    updatedAt: UTCDatetime


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
    createdAt: UTCDatetime
    submittedAt: UTCDatetime | None = None
    discardedAt: UTCDatetime | None = None
    summary: ExamSummaryPayload


class ExamHistoryResponse(BaseModel):
    items: list[ExamHistoryItemPayload]
    page: int
    pageSize: int
    total: int


def serialize_exam_summary(summary: Mapping[str, Any]) -> ExamSummaryPayload:
    return ExamSummaryPayload(
        totalProblems=int(summary.get("totalProblems", 0)),
        answeredProblems=int(summary.get("answeredProblems", 0)),
        gradedProblems=int(summary.get("gradedProblems", 0)),
        pendingProblems=int(summary.get("pendingProblems", 0)),
        correctProblems=int(summary.get("correctProblems", 0)),
        failedProblems=int(summary.get("failedProblems", 0)),
        score=summary.get("score"),
    )


def serialize_exam_item(
    item: Mapping[str, Any],
    *,
    include_correct_answer: bool,
    redact_grading_details: bool = False,
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
            subject=str(snapshot.get("subject", "math")),
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
            imageUrl=build_problem_image_url(item["problemId"]) if source_image is not None else None,
        ),
        answer=ExamAnswerPayload(
            raw=answer.get("raw"),
            savedAt=answer.get("savedAt"),
        ),
        grading=ExamGradingPayload(
            status=GradingStatus(grading.get("status", GradingStatus.UNGRADED.value)),
            method=grading.get("method") if not redact_grading_details else None,
            isCorrect=grading.get("isCorrect") if not redact_grading_details else None,
            score=grading.get("score") if not redact_grading_details else None,
            feedback=grading.get("feedback") if not redact_grading_details else None,
            providerModel=grading.get("providerModel") if not redact_grading_details else None,
            rawProviderResponse=grading.get("rawProviderResponse") if not redact_grading_details else None,
            gradedAt=grading.get("gradedAt"),
            retryCount=int(grading.get("retryCount", 0)),
            selfReportedCorrect=grading.get("selfReportedCorrect"),
        ),
    )


def serialize_exam(exam: Mapping[str, Any]) -> ExamPayload:
    state_value = str(exam.get("state"))
    include_correct_answer = state_value == ExamState.SUBMITTED.value
    redact_grading_details = state_value == ExamState.GRADING.value
    config_snapshot = dict(exam.get("configSnapshot", {}))
    selection_policy = dict(config_snapshot.get("selectionPolicy", {}))
    # Backward compatibility: historical snapshots may use failureWeight instead of failureRateWeight
    failure_rate_weight = float(
        selection_policy.get("failureRateWeight", selection_policy.get("failureWeight", 1.0))
    )
    return ExamPayload(
        id=str(exam["_id"]),
        state=ExamState(exam["state"]),
        configSnapshot=ExamConfigSnapshotPayload(
            maxProblemCount=int(config_snapshot.get("maxProblemCount", 0)),
            selectionPolicy=SelectionPolicyPayload(
                cooldownDays=int(selection_policy.get("cooldownDays", 7)),
                lastWrongWeight=float(selection_policy.get("lastWrongWeight", 1.0)),
                failureRateWeight=failure_rate_weight,
                recencyWeight=float(selection_policy.get("recencyWeight", 1.0)),
                minProblemAgeDays=int(selection_policy.get("minProblemAgeDays", 3)),
            ),
            generatedAt=config_snapshot["generatedAt"],
        ),
        items=[
            serialize_exam_item(
                item,
                include_correct_answer=include_correct_answer,
                redact_grading_details=redact_grading_details,
            )
            for item in exam.get("items", [])
        ],
        summary=serialize_exam_summary(dict(exam.get("summary", {}))),
        createdAt=exam["createdAt"],
        startedAt=exam.get("startedAt"),
        submittedAt=exam.get("submittedAt"),
        discardedAt=exam.get("discardedAt"),
        updatedAt=exam["updatedAt"],
    )
