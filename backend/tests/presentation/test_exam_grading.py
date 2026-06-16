from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.domain.models import GradingStatus, ProblemType
from app.infrastructure.storage.s3 import S3StorageAdapter, StorageObjectNotFoundError
from app.infrastructure.vlm.client import VLMClient, VLMError
from app.presentation.exam_grading import (
    build_grading_result,
    build_tracking_update,
    grade_item,
    grade_objective_item,
    grade_short_answer_item,
)


NOW = datetime(2026, 6, 16, 12, 0, 0)


def _objective_item(
    *,
    problem_type: ProblemType,
    answer_raw: str | None,
    correct_answer: dict[str, Any],
) -> dict[str, Any]:
    return {
        "itemId": "item-1",
        "order": 0,
        "problemId": "problem-1",
        "problemSnapshot": {
            "text": "What is the answer?",
            "problemType": problem_type.value,
            "subject": "math",
            "correctAnswer": correct_answer,
        },
        "answer": {"raw": answer_raw, "savedAt": NOW},
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


def test_build_grading_result_includes_all_standard_keys() -> None:
    result = build_grading_result(
        status=GradingStatus.CORRECT,
        method="normalized-match",
        is_correct=True,
        score=1.0,
        feedback="good",
        provider_model="model-x",
        raw_provider_response={"key": "value"},
        graded_at=NOW,
        retry_count=2,
        self_reported_correct=True,
    )
    assert result == {
        "status": "correct",
        "method": "normalized-match",
        "isCorrect": True,
        "score": 1.0,
        "feedback": "good",
        "providerModel": "model-x",
        "rawProviderResponse": {"key": "value"},
        "gradedAt": NOW,
        "retryCount": 2,
        "selfReportedCorrect": True,
    }


def test_build_grading_result_defaults() -> None:
    result = build_grading_result(
        status=GradingStatus.UNGRADED,
        method="vlm",
        graded_at=NOW,
    )
    assert result["status"] == "ungraded"
    assert result["method"] == "vlm"
    assert result["isCorrect"] is None
    assert result["score"] is None
    assert result["feedback"] is None
    assert result["providerModel"] is None
    assert result["rawProviderResponse"] is None
    assert result["gradedAt"] == NOW
    assert result["retryCount"] == 0
    assert result["selfReportedCorrect"] is None


def test_grade_objective_item_single_choice_correct() -> None:
    item = _objective_item(
        problem_type=ProblemType.SINGLE_CHOICE,
        answer_raw="A",
        correct_answer={
            "display": "A",
            "normalizedText": "a",
            "normalizedSet": [],
            "format": "single",
        },
    )
    graded = grade_objective_item(item, now=NOW)
    assert graded["grading"]["status"] == "correct"
    assert graded["grading"]["method"] == "normalized-match"
    assert graded["grading"]["isCorrect"] is True
    assert graded["grading"]["score"] == 1.0
    assert graded["grading"]["gradedAt"] == NOW


def test_grade_objective_item_single_choice_incorrect() -> None:
    item = _objective_item(
        problem_type=ProblemType.SINGLE_CHOICE,
        answer_raw="B",
        correct_answer={
            "display": "A",
            "normalizedText": "a",
            "normalizedSet": [],
            "format": "single",
        },
    )
    graded = grade_objective_item(item, now=NOW)
    assert graded["grading"]["status"] == "incorrect"
    assert graded["grading"]["isCorrect"] is False
    assert graded["grading"]["score"] == 0.0


def test_grade_objective_item_multi_choice_correct() -> None:
    item = _objective_item(
        problem_type=ProblemType.MULTI_CHOICE,
        answer_raw="A, C",
        correct_answer={
            "display": "A, C",
            "normalizedText": "a,c",
            "normalizedSet": ["a", "c"],
            "format": "set",
        },
    )
    graded = grade_objective_item(item, now=NOW)
    assert graded["grading"]["status"] == "correct"
    assert graded["grading"]["isCorrect"] is True


def test_grade_objective_item_fill_in_the_blank_correct() -> None:
    item = _objective_item(
        problem_type=ProblemType.FILL_IN_THE_BLANK,
        answer_raw="42",
        correct_answer={
            "display": "42",
            "normalizedText": "42",
            "normalizedSet": [],
            "format": "single",
        },
    )
    graded = grade_objective_item(item, now=NOW)
    assert graded["grading"]["status"] == "correct"


def test_grade_objective_item_short_answer_correct() -> None:
    item = _objective_item(
        problem_type=ProblemType.SHORT_ANSWER,
        answer_raw="forty two",
        correct_answer={
            "display": "forty two",
            "normalizedText": "forty two",
            "normalizedSet": [],
            "format": "single",
        },
    )
    graded = grade_objective_item(item, now=NOW)
    assert graded["grading"]["status"] == "correct"


def test_grade_objective_item_missing_answer_is_incorrect() -> None:
    item = _objective_item(
        problem_type=ProblemType.SINGLE_CHOICE,
        answer_raw=None,
        correct_answer={
            "display": "A",
            "normalizedText": "a",
            "normalizedSet": [],
            "format": "single",
        },
    )
    graded = grade_objective_item(item, now=NOW)
    assert graded["grading"]["status"] == "incorrect"
    assert graded["grading"]["isCorrect"] is False
    assert graded["grading"]["score"] == 0.0


def test_build_tracking_update_correct_answer() -> None:
    tracking = {
        "exposureCount": 2,
        "correctCount": 1,
        "failedCount": 1,
        "lastTestedAt": None,
        "lastAttemptCorrect": False,
    }
    update = build_tracking_update(tracking, now=NOW, is_correct=True)
    assert update == {
        "exposureCount": 3,
        "correctCount": 2,
        "failedCount": 1,
        "lastTestedAt": NOW,
        "lastAttemptCorrect": True,
    }


def test_build_tracking_update_incorrect_answer() -> None:
    tracking = {}
    update = build_tracking_update(tracking, now=NOW, is_correct=False)
    assert update == {
        "exposureCount": 1,
        "correctCount": 0,
        "failedCount": 1,
        "lastTestedAt": NOW,
        "lastAttemptCorrect": False,
    }


class FakeVLMClient:
    def __init__(
        self,
        *,
        result: Any | None = None,
        error: Exception | None = None,
        fail_once_then_succeed: Any | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self._fail_once_then_succeed = fail_once_then_succeed
        self.call_count = 0
        self.last_call_kwargs: dict[str, Any] = {}

    async def grade_short_answer(self, **kwargs: Any) -> Any:
        self.call_count += 1
        self.last_call_kwargs = kwargs
        if self._error is not None:
            raise self._error
        if self._fail_once_then_succeed is not None and self.call_count == 1:
            raise self._fail_once_then_succeed
        if self._result is None:
            raise RuntimeError("FakeVLMClient configured without a result")
        return self._result


class FakeS3Client:
    def __init__(self, data: bytes = b"fake-image") -> None:
        self._data = data
        self.calls: list[tuple[str, str]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append((Bucket, Key))
        return {"Body": _FakeBody(self._data)}


class MissingS3Client:
    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        raise StorageObjectNotFoundError(Key)


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def _short_answer_item(
    *,
    answer_raw: str | None = "user answer",
    source_image: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "itemId": "item-1",
        "order": 0,
        "problemId": "problem-1",
        "problemSnapshot": {
            "text": "Solve this.",
            "problemType": ProblemType.SHORT_ANSWER.value,
            "subject": "math",
            "correctAnswer": {
                "display": "correct",
                "normalizedText": "correct",
                "normalizedSet": [],
                "format": "single",
            },
            "sourceImage": source_image,
        },
        "answer": {"raw": answer_raw, "savedAt": NOW},
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


async def test_grade_short_answer_item_success_correct() -> None:
    result = type(
        "GradingResult",
        (object,),
        {
            "is_correct": True,
            "feedback": "Looks good",
            "model": "grading-model",
            "provider_metadata": {"cost": 1},
            "raw_provider_response": {"raw": "data"},
        },
    )()
    vlm = FakeVLMClient(result=result)
    storage = S3StorageAdapter(client=FakeS3Client())
    item = _short_answer_item()

    graded = await grade_short_answer_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert graded["grading"]["status"] == "correct"
    assert graded["grading"]["method"] == "vlm"
    assert graded["grading"]["isCorrect"] is True
    assert graded["grading"]["score"] == 1.0
    assert graded["grading"]["feedback"] == "Looks good"
    assert graded["grading"]["providerModel"] == "grading-model"
    assert graded["grading"]["rawProviderResponse"] == {"raw": "data"}
    assert graded["grading"]["gradedAt"] == NOW
    assert graded["grading"]["retryCount"] == 0


async def test_grade_short_answer_item_success_incorrect() -> None:
    result = type(
        "GradingResult",
        (object,),
        {
            "is_correct": False,
            "feedback": "Not quite",
            "model": "grading-model",
            "provider_metadata": {},
            "raw_provider_response": {},
        },
    )()
    vlm = FakeVLMClient(result=result)
    storage = S3StorageAdapter(client=FakeS3Client())
    item = _short_answer_item()

    graded = await grade_short_answer_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert graded["grading"]["status"] == "incorrect"
    assert graded["grading"]["isCorrect"] is False
    assert graded["grading"]["score"] == 0.0


async def test_grade_short_answer_item_missing_answer_is_incorrect() -> None:
    vlm = FakeVLMClient(result=None)
    storage = S3StorageAdapter(client=FakeS3Client())
    item = _short_answer_item(answer_raw=None)

    graded = await grade_short_answer_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert vlm.call_count == 0
    assert graded["grading"]["status"] == "incorrect"
    assert graded["grading"]["method"] == "normalized-match"
    assert graded["grading"]["isCorrect"] is False
    assert graded["grading"]["score"] == 0.0


async def test_grade_short_answer_item_loads_source_image_base64() -> None:
    result = type(
        "GradingResult",
        (object,),
        {
            "is_correct": True,
            "feedback": "",
            "model": "model",
            "provider_metadata": {},
            "raw_provider_response": {},
        },
    )()
    vlm = FakeVLMClient(result=result)
    fake_s3 = FakeS3Client(data=b"image-bytes")
    storage = S3StorageAdapter(client=fake_s3)
    item = _short_answer_item(
        source_image={"bucket": "media", "objectKey": "images/test.png"},
    )

    await grade_short_answer_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert fake_s3.calls == [("media", "images/test.png")]
    assert vlm.last_call_kwargs["image_base64"] == "aW1hZ2UtYnl0ZXM="


async def test_grade_short_answer_item_retryable_error_retries_then_pending_review() -> None:
    error = VLMError("temporary", code="vlm-timeout", retryable=True)
    result = type(
        "GradingResult",
        (object,),
        {
            "is_correct": True,
            "feedback": "",
            "model": "model",
            "provider_metadata": {},
            "raw_provider_response": {},
        },
    )()
    vlm = FakeVLMClient(fail_once_then_succeed=error, result=result)
    storage = S3StorageAdapter(client=FakeS3Client())
    item = _short_answer_item()

    graded = await grade_short_answer_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert vlm.call_count == 2
    assert graded["grading"]["status"] == "correct"
    assert graded["grading"]["retryCount"] == 1


async def test_grade_short_answer_item_retryable_error_exhausts_retries_to_pending_review() -> None:
    error = VLMError("timeout", code="vlm-timeout", retryable=True)
    vlm = FakeVLMClient(error=error)
    storage = S3StorageAdapter(client=FakeS3Client())
    item = _short_answer_item()

    graded = await grade_short_answer_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert vlm.call_count == 2
    assert graded["grading"]["status"] == "pending-review"
    assert graded["grading"]["method"] == "vlm"
    assert graded["grading"]["feedback"] == "timeout"
    assert graded["grading"]["retryCount"] == 1


async def test_grade_short_answer_item_non_retryable_error_is_pending_review() -> None:
    error = VLMError("rejected", code="vlm-provider-rejected", retryable=False)
    vlm = FakeVLMClient(error=error)
    storage = S3StorageAdapter(client=FakeS3Client())
    item = _short_answer_item()

    graded = await grade_short_answer_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert vlm.call_count == 1
    assert graded["grading"]["status"] == "pending-review"
    assert graded["grading"]["retryCount"] == 0


async def test_grade_short_answer_item_unexpected_exception_is_pending_review() -> None:
    vlm = FakeVLMClient(error=RuntimeError("unexpected"))
    storage = S3StorageAdapter(client=FakeS3Client())
    item = _short_answer_item()

    graded = await grade_short_answer_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert vlm.call_count == 1
    assert graded["grading"]["status"] == "pending-review"
    assert graded["grading"]["feedback"] == "unexpected"
    assert graded["grading"]["retryCount"] == 0


async def test_grade_item_dispatches_short_answer() -> None:
    result = type(
        "GradingResult",
        (object,),
        {
            "is_correct": True,
            "feedback": "",
            "model": "model",
            "provider_metadata": {},
            "raw_provider_response": {},
        },
    )()
    vlm = FakeVLMClient(result=result)
    storage = S3StorageAdapter(client=FakeS3Client())
    item = _short_answer_item()

    graded = await grade_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert graded["grading"]["status"] == "correct"
    assert vlm.call_count == 1


async def test_grade_item_dispatches_objective() -> None:
    vlm = FakeVLMClient(result=None)
    storage = S3StorageAdapter(client=FakeS3Client())
    item = _objective_item(
        problem_type=ProblemType.SINGLE_CHOICE,
        answer_raw="A",
        correct_answer={
            "display": "A",
            "normalizedText": "a",
            "normalizedSet": [],
            "format": "single",
        },
    )

    graded = await grade_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert vlm.call_count == 0
    assert graded["grading"]["status"] == "correct"


async def test_grade_short_answer_item_missing_source_image_continues_without_image() -> None:
    result = type(
        "GradingResult",
        (object,),
        {
            "is_correct": True,
            "feedback": "",
            "model": "model",
            "provider_metadata": {},
            "raw_provider_response": {},
        },
    )()
    vlm = FakeVLMClient(result=result)
    storage = S3StorageAdapter(client=MissingS3Client())
    item = _short_answer_item(
        source_image={"bucket": "media", "objectKey": "missing.png"},
    )

    graded = await grade_short_answer_item(item, vlm_client=vlm, storage=storage, now=NOW)

    assert graded["grading"]["status"] == "correct"
    assert vlm.last_call_kwargs["image_base64"] is None
