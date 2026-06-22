from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.config.settings import Settings
from app.main import create_app
from app.presentation.deps import get_app_settings, get_current_user, get_database
from tests.api.conftest import FakeDatabase, make_problem, make_user


@pytest.fixture
def home_app() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    settings = Settings(problem_selection_min_age_days=0)
    user = make_user(ObjectId(), "student")

    application.state.fake_database = database
    application.state.user = user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_current_user] = lambda: deepcopy(user)
    return application


@pytest_asyncio.fixture
async def client(home_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=home_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


def make_practice_attempt(
    user_id: ObjectId,
    problem_id: ObjectId,
    *,
    created_at: datetime,
    grading_status: str = "correct",
) -> dict:
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "problemId": problem_id,
        "submittedAnswer": "answer",
        "gradingStatus": grading_status,
        "gradingMethod": "normalized-match",
        "createdAt": created_at,
    }


def make_submitted_exam(
    user_id: ObjectId,
    *,
    problem_ids: list[ObjectId],
    submitted_at: datetime,
    item_grading_statuses: list[str] | None = None,
) -> dict:
    items = []
    for idx, pid in enumerate(problem_ids):
        status = (
            item_grading_statuses[idx]
            if item_grading_statuses and idx < len(item_grading_statuses)
            else "ungraded"
        )
        items.append({
            "itemId": str(ObjectId()),
            "order": idx + 1,
            "problemId": pid,
            "grading": {"status": status},
        })
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "state": "submitted",
        "items": items,
        "summary": {},
        "createdAt": now,
        "startedAt": now,
        "submittedAt": submitted_at,
        "updatedAt": now,
    }


@pytest.mark.asyncio
async def test_summary_no_problems_returns_zero(home_app: FastAPI, client: AsyncClient) -> None:
    response = await client.get("/api/v1/home/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["coverage"]["totalProblems"] == 0
    assert data["coverage"]["triedProblems"] == 0
    assert data["coverage"]["percentage"] == 0
    assert data["conquest"]["totalProblems"] == 0
    assert data["conquest"]["masteredProblems"] == 0
    assert data["conquest"]["percentage"] == 0
    assert data["activity"]["startDate"]
    assert data["activity"]["endDate"]
    assert len(data["activity"]["days"]) == 365


@pytest.mark.asyncio
async def test_summary_coverage_with_practice_attempt(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, problem["_id"], created_at=datetime.now(UTC))
    ])

    response = await client.get("/api/v1/home/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["coverage"]["totalProblems"] == 1
    assert data["coverage"]["triedProblems"] == 1
    assert data["coverage"]["percentage"] == 100


@pytest.mark.asyncio
async def test_summary_coverage_with_submitted_exam_items(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    database.seed("exams", [
        make_submitted_exam(user_id, problem_ids=[problem["_id"]], submitted_at=datetime.now(UTC))
    ])

    response = await client.get("/api/v1/home/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["coverage"]["triedProblems"] == 1
    assert data["coverage"]["percentage"] == 100


@pytest.mark.asyncio
async def test_summary_pending_review_exam_items_count(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    database.seed("exams", [
        make_submitted_exam(user_id, problem_ids=[problem["_id"]], submitted_at=datetime.now(UTC))
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["coverage"]["triedProblems"] == 1


@pytest.mark.asyncio
async def test_summary_duplicate_events_do_not_double_count_coverage(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    now = datetime.now(UTC)
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, problem["_id"], created_at=now),
        make_practice_attempt(user_id, problem["_id"], created_at=now),
    ])
    database.seed("exams", [
        make_submitted_exam(user_id, problem_ids=[problem["_id"]], submitted_at=now)
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["coverage"]["triedProblems"] == 1
    assert data["coverage"]["percentage"] == 100


@pytest.mark.asyncio
async def test_summary_multiple_events_same_day_increase_activity(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    now = datetime.now(UTC)
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, problem["_id"], created_at=now),
        make_practice_attempt(user_id, problem["_id"], created_at=now),
    ])
    database.seed("exams", [
        make_submitted_exam(user_id, problem_ids=[problem["_id"]], submitted_at=now)
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    today_str = now.strftime("%Y-%m-%d")
    today_day = next(d for d in data["activity"]["days"] if d["date"] == today_str)
    assert today_day["count"] == 3


@pytest.mark.asyncio
async def test_summary_deleted_problems_excluded_from_total(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    active = make_problem(user_id)
    deleted = make_problem(user_id, is_deleted=True)
    database.seed("problems", [active, deleted])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["coverage"]["totalProblems"] == 1


@pytest.mark.asyncio
async def test_summary_excludes_other_users_data(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    other_user_id = ObjectId()
    own_problem = make_problem(user_id)
    other_problem = make_problem(other_user_id)
    database.seed("problems", [own_problem, other_problem])
    now = datetime.now(UTC)
    database.seed("practice_attempts", [
        make_practice_attempt(other_user_id, other_problem["_id"], created_at=now),
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["coverage"]["totalProblems"] == 1
    assert data["coverage"]["triedProblems"] == 0


@pytest.mark.asyncio
async def test_summary_excludes_discarded_and_in_progress_exams(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    now = datetime.now(UTC)

    in_progress_exam = make_submitted_exam(user_id, problem_ids=[problem["_id"]], submitted_at=now)
    in_progress_exam["state"] = "in-progress"
    in_progress_exam["submittedAt"] = None
    discarded_exam = make_submitted_exam(user_id, problem_ids=[problem["_id"]], submitted_at=now)
    discarded_exam["state"] = "discarded"
    database.seed("exams", [in_progress_exam, discarded_exam])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["coverage"]["triedProblems"] == 0
    today_str = now.strftime("%Y-%m-%d")
    today_day = next(d for d in data["activity"]["days"] if d["date"] == today_str)
    assert today_day["count"] == 0


@pytest.mark.asyncio
async def test_summary_conquest_latest_correct_practice_marks_mastered(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 1, tzinfo=UTC)
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, problem["_id"], created_at=older, grading_status="incorrect"),
        make_practice_attempt(user_id, problem["_id"], created_at=newer, grading_status="correct"),
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["conquest"]["totalProblems"] == 1
    assert data["conquest"]["masteredProblems"] == 1
    assert data["conquest"]["percentage"] == 100


@pytest.mark.asyncio
async def test_summary_conquest_latest_incorrect_practice_not_mastered(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 1, tzinfo=UTC)
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, problem["_id"], created_at=older, grading_status="correct"),
        make_practice_attempt(user_id, problem["_id"], created_at=newer, grading_status="incorrect"),
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["conquest"]["masteredProblems"] == 0
    assert data["conquest"]["percentage"] == 0


@pytest.mark.asyncio
async def test_summary_conquest_exam_correct_item_marks_mastered(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    database.seed("exams", [
        make_submitted_exam(
            user_id,
            problem_ids=[problem["_id"]],
            submitted_at=datetime(2026, 1, 15, tzinfo=UTC),
            item_grading_statuses=["correct"],
        )
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["conquest"]["masteredProblems"] == 1
    assert data["conquest"]["percentage"] == 100


@pytest.mark.asyncio
async def test_summary_conquest_exam_incorrect_item_not_mastered(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    database.seed("exams", [
        make_submitted_exam(
            user_id,
            problem_ids=[problem["_id"]],
            submitted_at=datetime(2026, 1, 15, tzinfo=UTC),
            item_grading_statuses=["incorrect"],
        )
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["coverage"]["triedProblems"] == 1
    assert data["conquest"]["masteredProblems"] == 0


@pytest.mark.asyncio
async def test_summary_conquest_pending_review_only_not_mastered(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    database.seed("practice_attempts", [
        make_practice_attempt(
            user_id, problem["_id"],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            grading_status="pending-review",
        ),
    ])
    database.seed("exams", [
        make_submitted_exam(
            user_id,
            problem_ids=[problem["_id"]],
            submitted_at=datetime(2026, 1, 15, tzinfo=UTC),
            item_grading_statuses=["pending-review"],
        )
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["coverage"]["triedProblems"] == 1
    assert data["conquest"]["masteredProblems"] == 0
    assert data["conquest"]["percentage"] == 0


@pytest.mark.asyncio
async def test_summary_conquest_pending_after_correct_does_not_override(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    correct_time = datetime(2026, 1, 1, tzinfo=UTC)
    pending_time = datetime(2026, 2, 1, tzinfo=UTC)
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, problem["_id"], created_at=correct_time, grading_status="correct"),
        make_practice_attempt(user_id, problem["_id"], created_at=pending_time, grading_status="pending-review"),
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["conquest"]["masteredProblems"] == 1


@pytest.mark.asyncio
async def test_summary_conquest_deleted_problems_excluded(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    active = make_problem(user_id)
    deleted = make_problem(user_id, is_deleted=True)
    database.seed("problems", [active, deleted])
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, deleted["_id"], created_at=datetime(2026, 1, 1, tzinfo=UTC), grading_status="correct"),
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["conquest"]["totalProblems"] == 1
    assert data["conquest"]["masteredProblems"] == 0


@pytest.mark.asyncio
async def test_summary_conquest_other_users_excluded(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    other_user_id = ObjectId()
    own = make_problem(user_id)
    other = make_problem(other_user_id)
    database.seed("problems", [own, other])
    database.seed("practice_attempts", [
        make_practice_attempt(other_user_id, other["_id"], created_at=datetime(2026, 1, 1, tzinfo=UTC), grading_status="correct"),
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["conquest"]["totalProblems"] == 1
    assert data["conquest"]["masteredProblems"] == 0


@pytest.mark.asyncio
async def test_summary_conquest_exam_overrides_older_practice(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    database.seed("practice_attempts", [
        make_practice_attempt(
            user_id, problem["_id"],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            grading_status="correct",
        ),
    ])
    database.seed("exams", [
        make_submitted_exam(
            user_id,
            problem_ids=[problem["_id"]],
            submitted_at=datetime(2026, 2, 1, tzinfo=UTC),
            item_grading_statuses=["incorrect"],
        )
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["conquest"]["masteredProblems"] == 0
