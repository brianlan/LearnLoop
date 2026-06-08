from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from bson import ObjectId
from pydantic import ValidationError
from pymongo.asynchronous.database import AsyncDatabase

from app.domain.models import ExamItem, GradingStatus, Problem, SelectionPolicyConfig
from app.domain.scoring import compute_summary
from app.infrastructure.storage.mongo import Document
from app.presentation.errors import ApiError
from app.presentation.helpers import parse_object_id

DEFAULT_SELECTION_POLICY = SelectionPolicyConfig(recencyWeight=1.0, failureWeight=1.0)


def problem_document_to_model(problem: Mapping[str, Any]) -> Problem:
    try:
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


def build_exam_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
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


def make_exam_item(problem: Mapping[str, Any], *, order: int) -> dict[str, Any]:
    return {
        "itemId": str(ObjectId()),
        "order": order,
        "problemId": problem["_id"],
        "problemSnapshot": {
            "text": problem["text"],
            "problemType": problem["problemType"],
            "subject": problem.get("subject", "math"),
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


async def get_owned_exam(
    database: AsyncDatabase[Document],
    exam_id: str,
    user_id: ObjectId,
) -> dict[str, Any]:
    object_id = parse_object_id(exam_id, resource_name="Exam")
    exam = await database["exams"].find_one({"_id": object_id})
    if exam is None:
        raise ApiError(404, "NOT_FOUND", "Exam not found")
    if exam.get("userId") != user_id:
        raise ApiError(403, "FORBIDDEN", "Forbidden")
    return exam


def find_item(exam: Mapping[str, Any], item_id: str) -> tuple[int, dict[str, Any]]:
    for index, item in enumerate(exam.get("items", [])):
        if str(item.get("itemId")) == item_id:
            return index, deepcopy(item)
    raise ApiError(404, "NOT_FOUND", "Exam item not found")
