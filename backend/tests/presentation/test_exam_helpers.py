from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.domain.models import (
    CorrectAnswer,
    ExamItem,
    ExamItemAnswer,
    ExamItemGrading,
    GradingStatus,
    ProblemSnapshot,
    ProblemSubject,
    ProblemType,
)
from app.presentation.errors import ApiError
from app.presentation.exam_helpers import (
    build_exam_summary,
    find_item,
    make_exam_item,
    problem_document_to_model,
)


NOW = datetime(2026, 6, 16, 12, 0, 0)


def _problem_document(**overrides: Any) -> dict[str, Any]:
    base = {
        "_id": ObjectId(),
        "userId": ObjectId(),
        "text": "What is 2 + 2?",
        "problemType": ProblemType.SINGLE_CHOICE.value,
        "subject": ProblemSubject.MATH.value,
        "graphDsl": None,
        "correctAnswer": {
            "display": "4",
            "normalizedText": "4",
            "normalizedSet": [],
            "format": "single",
        },
        "tags": ["algebra"],
        "sourceImage": None,
        "origin": {},
        "tracking": {},
        "isDeleted": False,
        "deletedAt": None,
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    base.update(overrides)
    return base


def test_make_exam_item_shapes_item_with_ungraded_defaults() -> None:
    problem = _problem_document()
    item = make_exam_item(problem, order=3)

    assert item["order"] == 3
    assert item["problemId"] == problem["_id"]
    assert item["problemSnapshot"] == {
        "text": problem["text"],
        "problemType": problem["problemType"],
        "subject": problem["subject"],
        "graphDsl": problem["graphDsl"],
        "correctAnswer": problem["correctAnswer"],
        "sourceImage": problem["sourceImage"],
    }
    assert item["answer"] == {"raw": None, "savedAt": None}
    assert item["grading"] == {
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
    }


def test_make_exam_item_uses_default_subject() -> None:
    problem = _problem_document(subject=None)
    del problem["subject"]
    item = make_exam_item(problem, order=0)
    assert item["problemSnapshot"]["subject"] == "math"


def test_make_exam_item_generates_unique_item_ids() -> None:
    problem = _problem_document()
    item_a = make_exam_item(problem, order=0)
    item_b = make_exam_item(problem, order=1)
    assert item_a["itemId"] != item_b["itemId"]


def test_build_exam_summary_computes_scoring_summary() -> None:
    items = [
        {
            "itemId": "1",
            "order": 0,
            "problemId": "p1",
            "problemSnapshot": {
                "text": "q1",
                "problemType": ProblemType.SINGLE_CHOICE.value,
                "subject": ProblemSubject.MATH.value,
                "correctAnswer": {
                    "display": "a",
                    "normalizedText": "a",
                    "normalizedSet": [],
                    "format": "single",
                },
            },
            "answer": {"raw": "a", "savedAt": NOW},
            "grading": {"status": GradingStatus.CORRECT.value},
        },
        {
            "itemId": "2",
            "order": 1,
            "problemId": "p2",
            "problemSnapshot": {
                "text": "q2",
                "problemType": ProblemType.SINGLE_CHOICE.value,
                "subject": ProblemSubject.MATH.value,
                "correctAnswer": {
                    "display": "b",
                    "normalizedText": "b",
                    "normalizedSet": [],
                    "format": "single",
                },
            },
            "answer": {"raw": "c", "savedAt": NOW},
            "grading": {"status": GradingStatus.INCORRECT.value},
        },
        {
            "itemId": "3",
            "order": 2,
            "problemId": "p3",
            "problemSnapshot": {
                "text": "q3",
                "problemType": ProblemType.SINGLE_CHOICE.value,
                "subject": ProblemSubject.MATH.value,
                "correctAnswer": {
                    "display": "d",
                    "normalizedText": "d",
                    "normalizedSet": [],
                    "format": "single",
                },
            },
            "answer": {"raw": "d", "savedAt": NOW},
            "grading": {"status": GradingStatus.PENDING_REVIEW.value},
        },
    ]
    summary = build_exam_summary(items)
    assert summary == {
        "totalProblems": 3,
        "answeredProblems": 3,
        "gradedProblems": 2,
        "pendingProblems": 1,
        "correctProblems": 1,
        "failedProblems": 1,
        "score": 0.5,
    }


def test_build_exam_summary_casts_problem_id_to_string() -> None:
    items = [
        {
            "itemId": "1",
            "order": 0,
            "problemId": ObjectId(),
            "problemSnapshot": {
                "text": "q1",
                "problemType": ProblemType.SINGLE_CHOICE.value,
                "subject": ProblemSubject.MATH.value,
                "correctAnswer": {
                    "display": "a",
                    "normalizedText": "a",
                    "normalizedSet": [],
                    "format": "single",
                },
            },
            "answer": {"raw": "a", "savedAt": NOW},
            "grading": {"status": GradingStatus.CORRECT.value},
        }
    ]
    summary = build_exam_summary(items)
    assert summary["totalProblems"] == 1
    assert summary["score"] == 1.0


def test_problem_document_to_model_returns_problem() -> None:
    problem = _problem_document()
    model = problem_document_to_model(problem)
    assert model.id == str(problem["_id"])
    assert model.userId == str(problem["userId"])
    assert model.text == problem["text"]
    assert model.problemType == ProblemType.SINGLE_CHOICE


def test_problem_document_to_model_raises_api_error_on_validation_failure() -> None:
    problem = _problem_document(text=None)
    with pytest.raises(ApiError) as exc_info:
        problem_document_to_model(problem)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "INVALID_PROBLEM_DATA"


def test_find_item_returns_index_and_copy() -> None:
    exam = {
        "items": [
            {"itemId": "a", "data": 1},
            {"itemId": "b", "data": 2},
        ]
    }
    index, item = find_item(exam, "b")
    assert index == 1
    assert item["itemId"] == "b"
    item["data"] = 99
    assert exam["items"][1]["data"] == 2


def test_find_item_raises_when_not_found() -> None:
    exam = {"items": [{"itemId": "a"}]}
    with pytest.raises(ApiError) as exc_info:
        find_item(exam, "missing")
    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "NOT_FOUND"
