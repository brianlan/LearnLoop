from __future__ import annotations

from base64 import b64encode
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.domain.models import GradingStatus, ProblemType
from app.domain.normalization import normalize_answer
from app.infrastructure.storage.s3 import S3StorageAdapter, StorageObjectNotFoundError
from app.infrastructure.vlm.client import VLMClient, VLMError


def grade_objective_item(item: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
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


async def load_item_image_base64(item: Mapping[str, Any], storage: S3StorageAdapter) -> str | None:
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


async def grade_short_answer_item(
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
    image_base64 = await load_item_image_base64(graded_item, storage)

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


async def grade_item(
    item: Mapping[str, Any],
    *,
    vlm_client: VLMClient,
    storage: S3StorageAdapter,
    now: datetime,
) -> dict[str, Any]:
    snapshot = dict(item.get("problemSnapshot", {}))
    problem_type = ProblemType(snapshot["problemType"])
    if problem_type == ProblemType.SHORT_ANSWER:
        return await grade_short_answer_item(item, vlm_client=vlm_client, storage=storage, now=now)
    return grade_objective_item(item, now=now)


def build_tracking_update(tracking: Mapping[str, Any], *, now: datetime, is_correct: bool) -> dict[str, Any]:
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
