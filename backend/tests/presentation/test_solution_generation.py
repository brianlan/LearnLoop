"""Characterization tests for solution-generation orchestration.

These tests lock down the current enqueue/backfill/regenerate behavior of the
solution-generation orchestration module so a module move can be verified to
preserve behavior. They assert task document shape, idempotency, unavailable
collection handling, backfill cases, and exact ``ApiError`` semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from bson import ObjectId

from app.domain.models import SolutionGenerationStatus
from app.presentation.errors import ApiError
from app.solution_generation import (
    SOLUTION_BACKFILL_BATCH_SIZE,
    backfill_solution_generation_tasks,
    enqueue_solution_generation_task_for_problem,
    regenerate_solution_task_for_problem,
)
from tests.conftest import FakeDatabase


NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_problem(
    *,
    problem_id: ObjectId | None = None,
    user_id: ObjectId | None = None,
    is_deleted: bool = False,
) -> dict[str, Any]:
    return {
        "_id": problem_id or ObjectId(),
        "userId": user_id or ObjectId(),
        "isDeleted": is_deleted,
    }


class _UnavailableDatabase:
    """Database double whose collections are unavailable.

    ``_safe_get_collection`` catches ``KeyError`` and returns ``None``, which
    makes the orchestration surface its unavailable-collections behavior.
    """

    def __getitem__(self, name: str) -> Any:
        raise KeyError(name)


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_inserts_pending_task_with_expected_shape() -> None:
    database = FakeDatabase()
    problem = _make_problem()

    result = await enqueue_solution_generation_task_for_problem(
        database, problem, now=NOW
    )

    assert result is True

    tasks = database["solution_generation_tasks"]._documents
    assert len(tasks) == 1
    task = tasks[0]
    assert task["problem_id"] == str(problem["_id"])
    assert task["user_id"] == str(problem["userId"])
    assert task["status"] == SolutionGenerationStatus.PENDING
    assert task["status"] == "pending"
    assert task["retry_count"] == 0
    assert task["failure_reason"] is None
    assert task["started_at"] is None
    assert task["created_at"] == NOW
    assert task["updated_at"] == NOW
    assert task["process_after"] == NOW
    # _id is the Mongo ObjectId form of the model id string.
    assert isinstance(task["_id"], ObjectId)
    assert str(task["_id"]) == task["id"]


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_when_task_exists() -> None:
    database = FakeDatabase()
    problem = _make_problem()

    first = await enqueue_solution_generation_task_for_problem(database, problem, now=NOW)
    second = await enqueue_solution_generation_task_for_problem(database, problem, now=NOW)

    assert first is True
    assert second is False
    assert len(database["solution_generation_tasks"]._documents) == 1


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_when_solution_exists() -> None:
    database = FakeDatabase()
    problem = _make_problem()
    database["canonical_solutions"].seed({"problem_id": str(problem["_id"])})

    result = await enqueue_solution_generation_task_for_problem(database, problem, now=NOW)

    assert result is False
    assert len(database["solution_generation_tasks"]._documents) == 0


@pytest.mark.asyncio
async def test_enqueue_raises_when_collections_unavailable() -> None:
    database = _UnavailableDatabase()
    problem = _make_problem()

    with pytest.raises(KeyError, match="solution generation collections are unavailable"):
        await enqueue_solution_generation_task_for_problem(database, problem, now=NOW)


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_returns_zero_when_no_problems() -> None:
    database = FakeDatabase()

    result = await backfill_solution_generation_tasks(database, now=NOW)

    assert result == 0
    assert len(database["solution_generation_tasks"]._documents) == 0


@pytest.mark.asyncio
async def test_backfill_skips_deleted_problems() -> None:
    database = FakeDatabase()
    database["problems"].seed(_make_problem(is_deleted=True))

    result = await backfill_solution_generation_tasks(database, now=NOW)

    assert result == 0
    assert len(database["solution_generation_tasks"]._documents) == 0


@pytest.mark.asyncio
async def test_backfill_skips_problems_with_existing_task() -> None:
    database = FakeDatabase()
    problem = _make_problem()
    database["problems"].seed(problem)
    database["solution_generation_tasks"].seed({"problem_id": str(problem["_id"])})

    result = await backfill_solution_generation_tasks(database, now=NOW)

    assert result == 0
    assert len(database["solution_generation_tasks"]._documents) == 1


@pytest.mark.asyncio
async def test_backfill_skips_problems_with_existing_solution() -> None:
    database = FakeDatabase()
    problem = _make_problem()
    database["problems"].seed(problem)
    database["canonical_solutions"].seed({"problem_id": str(problem["_id"])})

    result = await backfill_solution_generation_tasks(database, now=NOW)

    assert result == 0
    assert len(database["solution_generation_tasks"]._documents) == 0


@pytest.mark.asyncio
async def test_backfill_enqueues_missing_problems_across_batch_boundary() -> None:
    database = FakeDatabase()
    problems = [_make_problem() for _ in range(3)]
    for problem in problems:
        database["problems"].seed(problem)

    result = await backfill_solution_generation_tasks(
        database, batch_size=2, now=NOW
    )

    assert result == 3
    tasks = database["solution_generation_tasks"]._documents
    assert len(tasks) == 3
    enqueued_problem_ids = {task["problem_id"] for task in tasks}
    assert enqueued_problem_ids == {str(problem["_id"]) for problem in problems}
    for task in tasks:
        assert task["status"] == SolutionGenerationStatus.PENDING
        assert task["retry_count"] == 0


@pytest.mark.asyncio
async def test_backfill_returns_zero_when_collections_unavailable() -> None:
    database = _UnavailableDatabase()

    result = await backfill_solution_generation_tasks(database, now=NOW)

    assert result == 0


def test_backfill_batch_size_constant() -> None:
    assert SOLUTION_BACKFILL_BATCH_SIZE == 100


# ---------------------------------------------------------------------------
# Regenerate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_ready_with_task_deletes_solution_and_resets_task() -> None:
    database = FakeDatabase()
    problem = _make_problem()
    problem_id = str(problem["_id"])
    database["canonical_solutions"].seed({"problem_id": problem_id})
    database["solution_generation_tasks"].seed(
        {
            "problem_id": problem_id,
            "status": SolutionGenerationStatus.GENERATING.value,
            "retry_count": 2,
            "failure_reason": "boom",
            "started_at": NOW,
        }
    )

    result = await regenerate_solution_task_for_problem(
        database, problem_id, str(problem["userId"]), now=NOW
    )

    assert result == SolutionGenerationStatus.PENDING.value
    assert len(database["canonical_solutions"]._documents) == 0
    tasks = database["solution_generation_tasks"]._documents
    assert len(tasks) == 1
    task = tasks[0]
    assert task["status"] == SolutionGenerationStatus.PENDING
    assert task["retry_count"] == 0
    assert task["failure_reason"] is None
    assert task["started_at"] is None
    assert task["process_after"] == NOW
    assert task["updated_at"] == NOW


@pytest.mark.asyncio
async def test_regenerate_ready_without_task_deletes_solution_and_inserts_task() -> None:
    database = FakeDatabase()
    problem = _make_problem()
    problem_id = str(problem["_id"])
    database["canonical_solutions"].seed({"problem_id": problem_id})

    result = await regenerate_solution_task_for_problem(
        database, problem_id, str(problem["userId"]), now=NOW
    )

    assert result == SolutionGenerationStatus.PENDING.value
    assert len(database["canonical_solutions"]._documents) == 0
    tasks = database["solution_generation_tasks"]._documents
    assert len(tasks) == 1
    task = tasks[0]
    assert task["problem_id"] == problem_id
    assert task["user_id"] == str(problem["userId"])
    assert task["status"] == SolutionGenerationStatus.PENDING
    assert task["created_at"] == NOW


@pytest.mark.asyncio
async def test_regenerate_failed_resets_task_to_pending() -> None:
    database = FakeDatabase()
    problem = _make_problem()
    problem_id = str(problem["_id"])
    database["solution_generation_tasks"].seed(
        {
            "problem_id": problem_id,
            "status": SolutionGenerationStatus.FAILED.value,
            "retry_count": 3,
            "failure_reason": "earlier failure",
            "started_at": NOW,
        }
    )

    result = await regenerate_solution_task_for_problem(
        database, problem_id, str(problem["userId"]), now=NOW
    )

    assert result == SolutionGenerationStatus.PENDING.value
    tasks = database["solution_generation_tasks"]._documents
    assert len(tasks) == 1
    task = tasks[0]
    assert task["status"] == SolutionGenerationStatus.PENDING
    assert task["retry_count"] == 0
    assert task["failure_reason"] is None
    assert task["started_at"] is None
    assert task["process_after"] == NOW
    assert task["updated_at"] == NOW


@pytest.mark.asyncio
async def test_regenerate_pending_conflict_raises_exact_api_error() -> None:
    database = FakeDatabase()
    problem = _make_problem()
    problem_id = str(problem["_id"])
    database["solution_generation_tasks"].seed(
        {"problem_id": problem_id, "status": SolutionGenerationStatus.PENDING.value}
    )

    with pytest.raises(ApiError) as exc_info:
        await regenerate_solution_task_for_problem(
            database, problem_id, str(problem["userId"]), now=NOW
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "SOLUTION_REGENERATION_CONFLICT"
    assert exc_info.value.message == "Solution is already pending or generating."


@pytest.mark.asyncio
async def test_regenerate_generating_conflict_raises_exact_api_error() -> None:
    database = FakeDatabase()
    problem = _make_problem()
    problem_id = str(problem["_id"])
    database["solution_generation_tasks"].seed(
        {"problem_id": problem_id, "status": SolutionGenerationStatus.GENERATING.value}
    )

    with pytest.raises(ApiError) as exc_info:
        await regenerate_solution_task_for_problem(
            database, problem_id, str(problem["userId"]), now=NOW
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "SOLUTION_REGENERATION_CONFLICT"
    assert exc_info.value.message == "Solution is already pending or generating."


@pytest.mark.asyncio
async def test_regenerate_no_solution_no_task_raises_exact_api_error() -> None:
    database = FakeDatabase()
    problem = _make_problem()
    problem_id = str(problem["_id"])

    with pytest.raises(ApiError) as exc_info:
        await regenerate_solution_task_for_problem(
            database, problem_id, str(problem["userId"]), now=NOW
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "SOLUTION_REGENERATION_CONFLICT"
    assert exc_info.value.message == "No solution to regenerate."
