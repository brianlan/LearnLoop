from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from app.domain import SolutionGenerationStatus, SolutionGenerationTask
from app.infrastructure.storage.mongo import (
    CANONICAL_SOLUTIONS_COLLECTION,
    Document,
    SOLUTION_GENERATION_TASKS_COLLECTION,
)

SOLUTION_BACKFILL_BATCH_SIZE = 100


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_get_collection(database: Any, name: str) -> Any | None:
    try:
        return database[name]
    except (KeyError, TypeError, AttributeError):
        return None


def _build_solution_task_document(
    *,
    problem_id: str,
    user_id: str,
    now: datetime,
) -> Document:
    task = SolutionGenerationTask(
        id=str(ObjectId()),
        problem_id=problem_id,
        user_id=user_id,
        status=SolutionGenerationStatus.PENDING,
        created_at=now,
        updated_at=now,
        process_after=now,
    )
    document = task.model_dump()
    document["_id"] = ObjectId(task.id)
    return document


async def enqueue_solution_generation_task_for_problem(
    database: Any,
    problem: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    tasks = _safe_get_collection(database, SOLUTION_GENERATION_TASKS_COLLECTION)
    solutions = _safe_get_collection(database, CANONICAL_SOLUTIONS_COLLECTION)
    if tasks is None or solutions is None:
        raise KeyError("solution generation collections are unavailable")

    current_time = now or _utc_now()
    problem_id = str(problem["_id"])
    user_id = str(problem["userId"])

    existing_task = await tasks.find_one({"problem_id": problem_id})
    if existing_task is not None:
        return False

    existing_solution = await solutions.find_one({"problem_id": problem_id})
    if existing_solution is not None:
        return False

    await tasks.insert_one(
        _build_solution_task_document(
            problem_id=problem_id,
            user_id=user_id,
            now=current_time,
        )
    )
    return True


async def backfill_solution_generation_tasks(
    database: Any,
    *,
    batch_size: int = SOLUTION_BACKFILL_BATCH_SIZE,
    now: datetime | None = None,
) -> int:
    problems = _safe_get_collection(database, "problems")
    tasks = _safe_get_collection(database, SOLUTION_GENERATION_TASKS_COLLECTION)
    solutions = _safe_get_collection(database, CANONICAL_SOLUTIONS_COLLECTION)
    if problems is None or tasks is None or solutions is None:
        return 0

    current_time = now or _utc_now()
    problem_documents = await problems.find({"isDeleted": False}).to_list(length=None)
    task_documents = await tasks.find({}).to_list(length=None)
    solution_documents = await solutions.find({}).to_list(length=None)

    existing_task_problem_ids = {str(document.get("problem_id")) for document in task_documents}
    existing_solution_problem_ids = {str(document.get("problem_id")) for document in solution_documents}

    missing_documents: list[Document] = []
    for problem in problem_documents:
        problem_id = str(problem["_id"])
        if problem_id in existing_task_problem_ids or problem_id in existing_solution_problem_ids:
            continue
        missing_documents.append(
            _build_solution_task_document(
                problem_id=problem_id,
                user_id=str(problem["userId"]),
                now=current_time,
            )
        )

    for start in range(0, len(missing_documents), max(batch_size, 1)):
        await tasks.insert_many(missing_documents[start : start + max(batch_size, 1)], ordered=False)

    return len(missing_documents)
