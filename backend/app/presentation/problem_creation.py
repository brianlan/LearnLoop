from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

logger = logging.getLogger(__name__)

from app.domain import ProblemSubject, ProblemType, normalize_answer
from app.presentation.errors import ApiError
from app.presentation.helpers import normalize_tags
from app.presentation.solution_generation import enqueue_solution_generation_task_for_problem
from app.presentation.tags import _register_tags


async def create_problem_from_draft(
    database: Any,
    user_id: Any,
    *,
    draft: Mapping[str, Any] | None,
    source_image: Mapping[str, Any] | None,
    origin: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a problem from a draft, enqueue a solution task, and register tags.

    Idempotent: if a problem already exists for the same ``origin`` metadata, it
    is returned instead of creating a duplicate.

    On failure after inserting the problem document, the problem is deleted so
    that partial failures do not leave visible problems.
    """
    current = now or datetime.now(UTC)
    draft_dict = dict(draft or {})

    text = (draft_dict.get("text") or "").strip() or None
    problem_type_value = draft_dict.get("problemType")
    correct_answer_raw = (draft_dict.get("correctAnswer") or "").strip() or None
    if text is None or problem_type_value is None or correct_answer_raw is None:
        raise ApiError(
            400,
            "MISSING_REQUIRED_FIELD",
            "Draft is missing required fields (text, problemType, correctAnswer)",
        )

    try:
        problem_type = ProblemType(problem_type_value)
    except ValueError as exc:
        raise ApiError(400, "INVALID_PROBLEM_TYPE", str(exc)) from exc

    normalized_answer = normalize_answer(correct_answer_raw, problem_type)
    tags = normalize_tags(list(draft_dict.get("tags", [])))

    subject_value = draft_dict.get("subject", ProblemSubject.MATH.value)
    try:
        ProblemSubject(subject_value)
    except ValueError:
        subject_value = ProblemSubject.MATH.value

    origin_dict = dict(origin or {})
    existing_query = _build_origin_query(origin_dict)
    if existing_query:
        existing = await database["problems"].find_one(existing_query)
        if existing is not None:
            await enqueue_solution_generation_task_for_problem(
                database, existing, now=current
            )
            try:
                await _register_tags(database, user_id, tags)
            except Exception:
                pass
            return existing

    problem: dict[str, Any] = {
        "_id": ObjectId(),
        "userId": user_id,
        "text": text,
        "problemType": problem_type.value,
        "subject": str(subject_value),
        "graphDsl": draft_dict.get("graphDsl"),
        "correctAnswer": normalized_answer.model_dump(),
        "tags": tags,
        "sourceImage": dict(source_image) if source_image else None,
        "origin": origin_dict,
        "tracking": {
            "exposureCount": 0,
            "correctCount": 0,
            "failedCount": 0,
            "lastTestedAt": None,
            "lastAttemptCorrect": None,
        },
        "isDeleted": False,
        "deletedAt": None,
        "createdAt": current,
        "updatedAt": current,
    }

    try:
        await database["problems"].insert_one(problem)
        await enqueue_solution_generation_task_for_problem(
            database, problem, now=current
        )
        await _register_tags(database, user_id, tags)
    except Exception:
        logger.exception("Problem creation failed")
        delete_problem = getattr(database["problems"], "delete_one", None)
        if callable(delete_problem):
            try:
                await delete_problem({"_id": problem["_id"]})
            except Exception:
                pass
        raise ApiError(
            500,
            "PROBLEM_CREATION_FAILED",
            "Failed to create problem. Please retry.",
        )

    return problem


def _build_origin_query(origin: dict[str, Any]) -> dict[str, Any] | None:
    query: dict[str, Any] = {}
    if origin.get("previewId"):
        query["origin.previewId"] = origin["previewId"]
    if origin.get("batchId"):
        query["origin.batchId"] = origin["batchId"]
    if origin.get("itemId"):
        query["origin.itemId"] = origin["itemId"]
    return query if query else None
