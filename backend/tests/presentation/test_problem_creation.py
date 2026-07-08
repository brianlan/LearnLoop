from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

from app.presentation.errors import ApiError
from app.presentation.problem_creation import create_problem_from_draft
from tests.conftest import FakeDatabase


def _make_draft(
    *,
    text: str = "What is 2+2?",
    problem_type: str = "short-answer",
    correct_answer: str = "4",
    subject: str = "math",
    tags: list[str] | None = None,
    graph_dsl: str | None = None,
) -> dict[str, Any]:
    return {
        "text": text,
        "problemType": problem_type,
        "correctAnswer": correct_answer,
        "subject": subject,
        "tags": tags or [],
        "graphDsl": graph_dsl,
    }


@pytest.mark.asyncio
async def test_create_problem_rejects_missing_required_fields() -> None:
    database = FakeDatabase()
    with pytest.raises(ApiError) as exc_info:
        await create_problem_from_draft(
            database,
            ObjectId(),
            draft={"text": "Only text"},
            source_image=None,
            origin={"previewId": "prev-1"},
        )
    assert exc_info.value.code == "MISSING_REQUIRED_FIELD"


@pytest.mark.asyncio
async def test_create_problem_inserts_problem_and_enqueues_solution() -> None:
    database = FakeDatabase()
    user_id = ObjectId()
    draft = _make_draft(tags=["tag-a", "tag-a"])
    source_image = {"bucket": "bucket", "objectKey": "key"}

    problem = await create_problem_from_draft(
        database,
        user_id,
        draft=draft,
        source_image=source_image,
        origin={"previewId": "prev-2"},
        now=datetime.now(UTC),
    )

    assert problem["userId"] == user_id
    assert problem["text"] == draft["text"]
    assert problem["problemType"] == "short-answer"
    assert problem["sourceImage"] == source_image
    assert problem["tags"] == ["tag-a"]
    assert problem["isDeleted"] is False
    assert problem["isDisabled"] is False

    problems = database["problems"]._documents
    assert len(problems) == 1
    assert problems[0]["isDisabled"] is False

    tasks = database["solution_generation_tasks"]._documents
    assert len(tasks) == 1
    assert tasks[0]["problem_id"] == str(problem["_id"])

    tags = database["tags"]._documents
    assert len(tags) == 1
    assert tags[0]["userId"] == user_id
    assert tags[0]["name"] == "tag-a"


@pytest.mark.asyncio
async def test_create_problem_is_idempotent_by_origin() -> None:
    database = FakeDatabase()
    user_id = ObjectId()
    origin = {"batchId": "batch-1", "itemId": "item-1"}
    draft = _make_draft()

    first = await create_problem_from_draft(
        database, user_id, draft=draft, source_image=None, origin=origin
    )
    second = await create_problem_from_draft(
        database, user_id, draft=draft, source_image=None, origin=origin
    )

    assert first["_id"] == second["_id"]
    assert len(database["problems"]._documents) == 1
    assert len(database["solution_generation_tasks"]._documents) == 1


@pytest.mark.asyncio
async def test_create_problem_deletes_inserted_problem_when_enqueue_fails() -> None:
    database = FakeDatabase()
    user_id = ObjectId()

    with patch(
        "app.presentation.problem_creation.enqueue_solution_generation_task_for_problem",
        new=AsyncMock(side_effect=RuntimeError("enqueue failed")),
    ):
        with pytest.raises(ApiError) as exc_info:
            await create_problem_from_draft(
                database,
                user_id,
                draft=_make_draft(),
                source_image=None,
                origin={"previewId": "prev-3"},
            )

    assert exc_info.value.code == "PROBLEM_CREATION_FAILED"
    assert len(database["problems"]._documents) == 0
    assert len(database["tags"]._documents) == 0
