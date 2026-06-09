from datetime import datetime

import pytest
from pydantic import ValidationError

from app.domain import CanonicalSolution, SolutionGenerationTask, SolutionGenerationStatus


def test_solution_generation_task_validates_required_fields_and_defaults() -> None:
    task = SolutionGenerationTask(
        problem_id="problem-1",
        user_id="user-1",
        status=SolutionGenerationStatus.PENDING,
    )

    assert task.problem_id == "problem-1"
    assert task.user_id == "user-1"
    assert task.status == SolutionGenerationStatus.PENDING
    assert task.retry_count == 0
    assert task.failure_reason is None
    assert task.started_at is None
    assert isinstance(task.created_at, datetime)
    assert isinstance(task.updated_at, datetime)
    assert isinstance(task.process_after, datetime)


def test_solution_generation_task_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        SolutionGenerationTask(
            problem_id="problem-1",
            user_id="user-1",
            status="queued",
        )


def test_canonical_solution_stores_markdown_content() -> None:
    solution = CanonicalSolution(
        problem_id="problem-1",
        user_id="user-1",
        steps_markdown="1. Start with **x + 2 = 4**.\n2. Subtract 2 from both sides.",
        final_answer="x = 2",
        level_classification="algebra-1",
    )

    assert solution.steps_markdown.startswith("1. Start with **x + 2 = 4**.")
    assert solution.final_answer == "x = 2"
    assert solution.level_classification == "algebra-1"
    assert isinstance(solution.created_at, datetime)


def test_canonical_solution_requires_core_fields() -> None:
    with pytest.raises(ValidationError):
        CanonicalSolution(
            problem_id="problem-1",
            user_id="user-1",
            steps_markdown="step",
            final_answer="answer",
        )
