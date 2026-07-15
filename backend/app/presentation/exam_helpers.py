from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from bson import ObjectId
from pydantic import ValidationError
from pymongo.asynchronous.database import AsyncDatabase

from app.domain.models import ExamItem, GradingStatus, ProblemType
from app.domain.scoring import compute_summary
from app.infrastructure.storage.mongo import Document
from app.presentation.errors import ApiError
from app.presentation.helpers import parse_object_id

# Terminal item grading statuses: once set, the item should not be re-graded.
TERMINAL_GRADING_STATUSES = frozenset({
    GradingStatus.CORRECT.value,
    GradingStatus.INCORRECT.value,
    GradingStatus.PENDING_REVIEW.value,
})


def requires_vlm_grading(item: Mapping[str, Any]) -> bool:
    """A short-answer item with a stored non-null answer requires VLM grading."""
    snapshot = dict(item.get("problemSnapshot", {}))
    if snapshot.get("problemType") != ProblemType.SHORT_ANSWER.value:
        return False
    answer = dict(item.get("answer", {}))
    return answer.get("raw") is not None


def exam_requires_vlm_grading(items: list[Mapping[str, Any]]) -> bool:
    return any(requires_vlm_grading(item) for item in items)

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
