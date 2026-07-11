from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from tests.conftest import FakeStorage as FakeStorage
from tests.test_utils.db_fakes import (
    FakeCursor as FakeCursor,
    FakeDatabase as FakeDatabase,
    matches_query as matches_query,
)


def make_user(user_id: ObjectId, username: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": user_id,
        "username": username,
        "status": "active",
        "createdAt": now,
        "updatedAt": now,
        "lastLoginAt": None,
    }


def make_problem(
    user_id: ObjectId,
    *,
    text: str = "What is 2+2?",
    problem_type: str = "short-answer",
    correct_answer_display: str = "4",
    subject: str = "math",
    is_deleted: bool = False,
    is_disabled: bool = False,
    last_tested_at: datetime | None = None,
    last_attempt_correct: bool | None = None,
    exposure_count: int = 0,
    correct_count: int = 0,
    failed_count: int = 0,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    now = created_at if created_at is not None else datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": text,
        "problemType": problem_type,
        "subject": subject,
        "graphDsl": None,
        "correctAnswer": {
            "display": correct_answer_display,
            "normalizedText": correct_answer_display,
            "normalizedSet": [],
            "format": "single",
        },
        "tags": [],
        "sourceImage": None,
        "origin": {},
        "tracking": {
            "exposureCount": exposure_count,
            "correctCount": correct_count,
            "failedCount": failed_count,
            "lastTestedAt": last_tested_at,
            "lastAttemptCorrect": last_attempt_correct,
        },
        "isDeleted": is_deleted,
        "deletedAt": None,
        "isDisabled": is_disabled,
        "createdAt": now,
        "updatedAt": now,
    }
