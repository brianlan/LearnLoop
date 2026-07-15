"""Characterization tests for problem response serialization.

These tests pin the key order and nested payload shape of the problem
list/detail response models and the ``_serialize_*`` helpers so that the
extraction into ``problem_serialization.py`` is verifiably behavior-
preserving.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone, timedelta
from typing import Any

from bson import ObjectId

from app.domain.models import Problem, ProblemSubject, ProblemType
from app.presentation.errors import ApiError
import pytest
from app.presentation.problem_serialization import (
    OriginPayload,
    ProblemDetailPayload,
    ProblemListResponse,
    ProblemResponse,
    ProblemSummaryPayload,
    TrackingPayload,
    _serialize_problem_detail,
    _serialize_problem_summary,
    problem_document_to_model,
)
from app.presentation.schemas import UTCDatetime, ensure_utc

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _make_problem(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_id": ObjectId(),
        "userId": ObjectId(),
        "text": "What is 2+2?",
        "problemType": "short-answer",
        "subject": "math",
        "graphDsl": None,
        "correctAnswer": {
            "display": "4",
            "normalizedText": "4",
            "normalizedSet": [],
            "format": "single",
        },
        "tags": ["algebra", "chapter-1"],
        "sourceImage": {
            "bucket": "learnloop-media",
            "objectKey": "images/source.png",
            "contentType": "image/png",
            "sizeBytes": 7,
            "sha256": None,
        },
        "origin": {
            "previewId": ObjectId(),
            "vlmModel": "gpt-4.1-mini",
            "rawExtractedText": "raw text",
            "rawExtractedProblemType": "short-answer",
            "rawExtractedGraphDsl": None,
        },
        "tracking": {
            "exposureCount": 3,
            "correctCount": 2,
            "failedCount": 1,
            "lastTestedAt": NOW,
            "lastAttemptCorrect": True,
        },
        "isDeleted": False,
        "deletedAt": None,
        "folderId": None,
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    base.update(overrides)
    return base


# ---- key order ----


def test_problem_summary_payload_key_order() -> None:
    summary = _serialize_problem_summary(_make_problem())

    assert list(summary.model_dump().keys()) == [
        "id",
        "text",
        "problemType",
        "subject",
        "graphDsl",
        "tags",
        "tracking",
        "isDeleted",
        "deletedAt",
        "isDisabled",
        "createdAt",
        "updatedAt",
        "imageUrl",
        "folderId",
    ]


def test_problem_detail_payload_key_order() -> None:
    detail = _serialize_problem_detail(_make_problem())

    assert list(detail.model_dump().keys()) == [
        "id",
        "text",
        "problemType",
        "subject",
        "graphDsl",
        "tags",
        "tracking",
        "isDeleted",
        "deletedAt",
        "isDisabled",
        "createdAt",
        "updatedAt",
        "imageUrl",
        "folderId",
        "correctAnswer",
        "origin",
    ]


def test_problem_list_response_key_order() -> None:
    summary = _serialize_problem_summary(_make_problem())
    response = ProblemListResponse(
        items=[summary],
        page=1,
        pageSize=20,
        total=1,
    )

    assert list(response.model_dump().keys()) == [
        "items",
        "page",
        "pageSize",
        "total",
    ]


def test_problem_response_key_order() -> None:
    detail = _serialize_problem_detail(_make_problem())
    response = ProblemResponse(problem=detail)

    assert list(response.model_dump().keys()) == ["problem"]


def test_tracking_payload_key_order() -> None:
    tracking = TrackingPayload(
        exposureCount=3,
        correctCount=2,
        failedCount=1,
        lastTestedAt=NOW,
        lastAttemptCorrect=True,
    )

    assert list(tracking.model_dump().keys()) == [
        "exposureCount",
        "correctCount",
        "failedCount",
        "lastTestedAt",
        "lastAttemptCorrect",
    ]


def test_origin_payload_key_order() -> None:
    origin = OriginPayload(
        previewId="abc",
        vlmModel="gpt-4.1-mini",
        rawExtractedText="raw",
        rawExtractedProblemType="short-answer",
        rawExtractedGraphDsl=None,
    )

    assert list(origin.model_dump().keys()) == [
        "previewId",
        "vlmModel",
        "rawExtractedText",
        "rawExtractedProblemType",
        "rawExtractedGraphDsl",
    ]


# ---- nested payload shape ----


def test_serialized_summary_nested_shapes() -> None:
    problem = _make_problem()
    summary = _serialize_problem_summary(problem)
    dumped = summary.model_dump()

    tracking = dumped["tracking"]
    assert isinstance(tracking, dict)
    assert tracking == {
        "exposureCount": 3,
        "correctCount": 2,
        "failedCount": 1,
        "lastTestedAt": NOW,
        "lastAttemptCorrect": True,
    }

    assert dumped["tags"] == ["algebra", "chapter-1"]
    assert dumped["imageUrl"] == f"/api/v1/problems/{problem['_id']}/image"
    assert dumped["folderId"] is None
    assert dumped["graphDsl"] is None
    assert dumped["deletedAt"] is None


def test_serialized_detail_nested_shapes() -> None:
    problem = _make_problem()
    detail = _serialize_problem_detail(problem)
    dumped = detail.model_dump()

    correct_answer = dumped["correctAnswer"]
    assert isinstance(correct_answer, dict)
    assert list(correct_answer.keys()) == [
        "display",
        "normalizedText",
        "normalizedSet",
        "format",
    ]
    assert correct_answer == {
        "display": "4",
        "normalizedText": "4",
        "normalizedSet": [],
        "format": "single",
    }

    origin = dumped["origin"]
    assert isinstance(origin, dict)
    assert list(origin.keys()) == [
        "previewId",
        "vlmModel",
        "rawExtractedText",
        "rawExtractedProblemType",
        "rawExtractedGraphDsl",
    ]
    assert origin["previewId"] == str(problem["origin"]["previewId"])
    assert origin["vlmModel"] == "gpt-4.1-mini"


# ---- edge cases that must survive extraction ----


def test_serialize_summary_defaults_missing_is_disabled_to_false() -> None:
    problem = _make_problem()
    summary = _serialize_problem_summary(problem)

    assert summary.model_dump()["isDisabled"] is False


def test_serialize_summary_reflects_disabled_state() -> None:
    summary = _serialize_problem_summary(_make_problem(isDisabled=True))

    assert summary.model_dump()["isDisabled"] is True


def test_serialize_detail_reflects_disabled_state() -> None:
    detail = _serialize_problem_detail(_make_problem(isDisabled=True))

    assert detail.model_dump()["isDisabled"] is True


def test_serialize_summary_without_source_image() -> None:
    problem = _make_problem()
    del problem["sourceImage"]
    summary = _serialize_problem_summary(problem)

    assert summary.model_dump()["imageUrl"] is None


def test_serialize_summary_with_folder_id() -> None:
    folder_id = str(ObjectId())
    summary = _serialize_problem_summary(_make_problem(folderId=folder_id))

    assert summary.model_dump()["folderId"] == folder_id


def test_serialize_detail_origin_none() -> None:
    problem = _make_problem(origin=None)
    detail = _serialize_problem_detail(problem)
    dumped = detail.model_dump()

    assert dumped["origin"] == {
        "previewId": None,
        "vlmModel": None,
        "rawExtractedText": None,
        "rawExtractedProblemType": None,
        "rawExtractedGraphDsl": None,
    }


# ---- UTC timestamp serialization (timezone-naive Mongo datetimes) ----


def test_problem_document_to_model_returns_problem() -> None:
    problem = _make_problem(problemType=ProblemType.SINGLE_CHOICE.value)
    model = problem_document_to_model(problem)
    assert isinstance(model, Problem)
    assert model.id == str(problem["_id"])
    assert model.userId == str(problem["userId"])
    assert model.text == problem["text"]
    assert model.problemType == ProblemType.SINGLE_CHOICE
    assert model.subject == ProblemSubject.MATH.value
    assert model.origin.previewId == str(problem["origin"]["previewId"])


def test_problem_document_to_model_applies_defaults() -> None:
    problem = _make_problem(origin=None)
    del problem["subject"]
    del problem["graphDsl"]
    del problem["deletedAt"]
    problem["tags"] = []
    model = problem_document_to_model(problem)

    assert model.subject == "math"
    assert model.graphDsl is None
    assert model.tags == []
    assert model.tracking.exposureCount == 3
    assert model.isDeleted is False
    assert model.isDisabled is False
    assert model.deletedAt is None
    assert model.origin.previewId is None


def test_problem_document_to_model_uses_empty_tracking_default() -> None:
    problem = _make_problem(tracking={})
    model = problem_document_to_model(problem)

    assert model.tracking.exposureCount == 0
    assert model.tracking.correctCount == 0
    assert model.tracking.failedCount == 0
    assert model.tracking.lastTestedAt is None
    assert model.tracking.lastAttemptCorrect is None


def test_problem_document_to_model_raises_api_error_with_full_details() -> None:
    problem = _make_problem(text=None)
    with pytest.raises(ApiError) as exc_info:
        problem_document_to_model(problem)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "INVALID_PROBLEM_DATA"
    assert "Problem contains invalid data for exam creation" in exc_info.value.message
    assert exc_info.value.details is not None
    assert exc_info.value.details["problemId"] == str(problem["_id"])
    assert any(
        error.get("loc") == ("text",)
        for error in exc_info.value.details["errors"]
    )


def test_ensure_utc_treats_naive_datetime_as_utc() -> None:
    naive = datetime(2026, 5, 25, 13, 51, 4)
    assert ensure_utc(naive) == naive.replace(tzinfo=UTC)


def test_ensure_utc_converts_aware_datetime_to_utc() -> None:
    aware = datetime(2026, 5, 25, 21, 51, 4, tzinfo=timezone(timedelta(hours=8)))
    assert ensure_utc(aware) == datetime(2026, 5, 25, 13, 51, 4, tzinfo=UTC)


def test_ensure_utc_leaves_none_unchanged() -> None:
    assert ensure_utc(None) is None


def test_naive_utc_datetime_serializes_with_utc_timezone() -> None:
    # Simulates a naive UTC datetime returned by Mongo/PyMongo (tz_aware=False).
    naive = datetime(2026, 5, 25, 13, 51, 4)
    problem = _make_problem(createdAt=naive, updatedAt=naive)
    detail = _serialize_problem_detail(problem)

    json = detail.model_dump_json()
    assert '"createdAt":"2026-05-25T13:51:04Z"' in json
    assert '"updatedAt":"2026-05-25T13:51:04Z"' in json


def test_aware_utc_datetime_serializes_with_utc_timezone() -> None:
    problem = _make_problem()
    detail = _serialize_problem_detail(problem)

    assert detail.createdAt.tzinfo is not None
    json = detail.model_dump_json()
    assert '"createdAt":"2026-01-01T00:00:00Z"' in json


def test_naive_optional_datetime_serializes_with_utc_timezone() -> None:
    naive = datetime(2026, 5, 25, 13, 51, 4)
    problem = _make_problem(deletedAt=naive)
    problem["tracking"]["lastTestedAt"] = naive
    summary = _serialize_problem_summary(problem)

    json = summary.model_dump_json()
    assert '"lastTestedAt":"2026-05-25T13:51:04Z"' in json
    assert '"deletedAt":"2026-05-25T13:51:04Z"' in json

