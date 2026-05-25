from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.domain.models import GradingStatus, ProblemType
from app.domain.normalization import compare_answers, normalize_answer
from app.infrastructure.storage.s3 import S3StorageAdapter
from app.infrastructure.vlm.client import VLMClient, VLMError
from app.presentation.helpers import load_source_image_base64


def build_grading_result(
    *,
    status: GradingStatus,
    method: str,
    is_correct: bool | None = None,
    score: float | None = None,
    feedback: str | None = None,
    provider_model: str | None = None,
    raw_provider_response: Any = None,
    graded_at: datetime,
    retry_count: int = 0,
    self_reported_correct: bool | None = None,
) -> dict[str, Any]:
    return {
        "status": status.value,
        "method": method,
        "isCorrect": is_correct,
        "score": score,
        "feedback": feedback,
        "providerModel": provider_model,
        "rawProviderResponse": raw_provider_response,
        "gradedAt": graded_at,
        "retryCount": retry_count,
        "selfReportedCorrect": self_reported_correct,
    }


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
        is_correct = compare_answers(normalized, correct_answer, problem_type)

    grading = dict(item.get("grading", {}))
    grading.update(build_grading_result(
        status=GradingStatus.CORRECT if is_correct else GradingStatus.INCORRECT,
        method="normalized-match",
        is_correct=is_correct,
        score=1.0 if is_correct else 0.0,
        graded_at=now,
    ))
    graded_item = deepcopy(dict(item))
    graded_item["grading"] = grading
    return graded_item


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
        graded_item["grading"] = build_grading_result(
            status=GradingStatus.INCORRECT,
            method="normalized-match",
            is_correct=False,
            score=0.0,
            graded_at=now,
        )
        return graded_item

    snapshot = dict(graded_item.get("problemSnapshot", {}))
    correct_answer = dict(snapshot.get("correctAnswer", {}))
    image_base64 = load_source_image_base64(snapshot.get("sourceImage"), storage)

    retry_count = 0
    while True:
        try:
            result = await vlm_client.grade_short_answer(
                image_base64=image_base64,
                user_answer=str(answer_raw),
                correct_answer=str(correct_answer.get("display", "")),
            )
            graded_item["grading"] = build_grading_result(
                status=GradingStatus.CORRECT if result.is_correct else GradingStatus.INCORRECT,
                method="vlm",
                is_correct=result.is_correct,
                score=1.0 if result.is_correct else 0.0,
                feedback=result.feedback,
                provider_model=result.model,
                raw_provider_response=result.raw_provider_response,
                graded_at=now,
                retry_count=retry_count,
            )
            return graded_item
        except VLMError as exc:
            if exc.retryable and retry_count < 1:
                retry_count += 1
                continue
            graded_item["grading"] = build_grading_result(
                status=GradingStatus.PENDING_REVIEW,
                method="vlm",
                feedback=str(exc),
                raw_provider_response=exc.raw_provider_response,
                graded_at=now,
                retry_count=retry_count,
            )
            return graded_item
        except Exception as exc:
            graded_item["grading"] = build_grading_result(
                status=GradingStatus.PENDING_REVIEW,
                method="vlm",
                feedback=str(exc),
                graded_at=now,
                retry_count=retry_count,
            )
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
