from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta

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
    return _build_home_application(Settings(problem_selection_min_age_days=0))


def _build_home_application(settings: Settings) -> FastAPI:
    application = create_app()
    database = FakeDatabase()
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


@pytest.fixture
def home_app_with_min_age() -> FastAPI:
    return _build_home_application(Settings(problem_selection_min_age_days=3))


@pytest_asyncio.fixture
async def client_with_min_age(home_app_with_min_age: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=home_app_with_min_age)
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
    assert data["firstPass"]["attemptedProblems"] == 0
    assert data["firstPass"]["firstPassCorrectProblems"] == 0
    assert data["firstPass"]["percentage"] == 0
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


@pytest.mark.asyncio
async def test_summary_timezone_buckets_by_local_date(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """An event near UTC midnight should land on the next local date for Asia/Shanghai."""
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])

    # 2026-01-15T23:30:00Z is 2026-01-16T07:30:00+08:00 in Asia/Shanghai
    event_time = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, problem["_id"], created_at=event_time),
    ])

    # With UTC (default) the event should be on Jan 15
    response_utc = await client.get("/api/v1/home/summary")
    data_utc = response_utc.json()
    jan15_utc = next(d for d in data_utc["activity"]["days"] if d["date"] == "2026-01-15")
    jan16_utc = next(d for d in data_utc["activity"]["days"] if d["date"] == "2026-01-16")
    assert jan15_utc["count"] == 1
    assert jan16_utc["count"] == 0

    # With Asia/Shanghai the event should be on Jan 16
    response_tz = await client.get("/api/v1/home/summary?timezone=Asia/Shanghai")
    data_tz = response_tz.json()
    jan15_tz = next(d for d in data_tz["activity"]["days"] if d["date"] == "2026-01-15")
    jan16_tz = next(d for d in data_tz["activity"]["days"] if d["date"] == "2026-01-16")
    assert jan15_tz["count"] == 0
    assert jan16_tz["count"] == 1


@pytest.mark.asyncio
async def test_summary_invalid_timezone_returns_400(
    home_app: FastAPI, client: AsyncClient
) -> None:
    response = await client.get("/api/v1/home/summary?timezone=NotARealTimezone")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_summary_omitting_timezone_defaults_to_utc(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """Backward compatibility: omitting timezone should use UTC."""
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    now = datetime.now(UTC)
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, problem["_id"], created_at=now),
    ])

    response_with = await client.get("/api/v1/home/summary?timezone=UTC")
    response_without = await client.get("/api/v1/home/summary")
    assert response_with.status_code == 200
    assert response_without.status_code == 200

    data_with = response_with.json()
    data_without = response_without.json()
    today_str = now.strftime("%Y-%m-%d")
    day_with = next(d for d in data_with["activity"]["days"] if d["date"] == today_str)
    day_without = next(d for d in data_without["activity"]["days"] if d["date"] == today_str)
    assert day_with["count"] == day_without["count"]


@pytest.mark.asyncio
async def test_summary_first_pass_no_attempts_returns_zero(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """Problems exist but no attempts: firstPass should be zero."""
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["firstPass"]["attemptedProblems"] == 0
    assert data["firstPass"]["firstPassCorrectProblems"] == 0
    assert data["firstPass"]["percentage"] == 0


@pytest.mark.asyncio
async def test_summary_first_pass_correct_practice_attempt(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """First practice attempt correct yields 100%."""
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, problem["_id"], created_at=datetime(2026, 1, 1, tzinfo=UTC), grading_status="correct"),
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["firstPass"]["attemptedProblems"] == 1
    assert data["firstPass"]["firstPassCorrectProblems"] == 1
    assert data["firstPass"]["percentage"] == 100


@pytest.mark.asyncio
async def test_summary_first_pass_incorrect_then_correct_not_first_pass(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """First attempt incorrect, later correct: not first-pass correct."""
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
    assert data["firstPass"]["attemptedProblems"] == 1
    assert data["firstPass"]["firstPassCorrectProblems"] == 0
    assert data["firstPass"]["percentage"] == 0


@pytest.mark.asyncio
async def test_summary_first_pass_exam_correct_item(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """First submitted exam item correct counts as first-pass correct."""
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
    assert data["firstPass"]["attemptedProblems"] == 1
    assert data["firstPass"]["firstPassCorrectProblems"] == 1
    assert data["firstPass"]["percentage"] == 100


@pytest.mark.asyncio
async def test_summary_first_pass_pending_review_counts_in_denominator_only(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """Pending-review first attempt counts in denominator, not numerator."""
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

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["firstPass"]["attemptedProblems"] == 1
    assert data["firstPass"]["firstPassCorrectProblems"] == 0
    assert data["firstPass"]["percentage"] == 0


@pytest.mark.asyncio
async def test_summary_first_pass_ungraded_exam_counts_in_denominator_only(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """Ungraded exam item first attempt counts in denominator, not numerator."""
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    database.seed("exams", [
        make_submitted_exam(
            user_id,
            problem_ids=[problem["_id"]],
            submitted_at=datetime(2026, 1, 15, tzinfo=UTC),
            item_grading_statuses=["ungraded"],
        )
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["firstPass"]["attemptedProblems"] == 1
    assert data["firstPass"]["firstPassCorrectProblems"] == 0
    assert data["firstPass"]["percentage"] == 0


@pytest.mark.asyncio
async def test_summary_first_pass_deleted_problems_excluded(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """Deleted problems' attempts do not count in firstPass."""
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
    assert data["firstPass"]["attemptedProblems"] == 0
    assert data["firstPass"]["firstPassCorrectProblems"] == 0
    assert data["firstPass"]["percentage"] == 0


@pytest.mark.asyncio
async def test_summary_first_pass_other_users_excluded(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """Other users' attempts do not count in firstPass."""
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
    assert data["firstPass"]["attemptedProblems"] == 0
    assert data["firstPass"]["firstPassCorrectProblems"] == 0


@pytest.mark.asyncio
async def test_summary_first_pass_discarded_in_progress_excluded(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """Discarded and in-progress exams do not count in firstPass."""
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])
    now = datetime(2026, 1, 1, tzinfo=UTC)

    in_progress_exam = make_submitted_exam(user_id, problem_ids=[problem["_id"]], submitted_at=now)
    in_progress_exam["state"] = "in-progress"
    in_progress_exam["submittedAt"] = None
    discarded_exam = make_submitted_exam(user_id, problem_ids=[problem["_id"]], submitted_at=now)
    discarded_exam["state"] = "discarded"
    database.seed("exams", [in_progress_exam, discarded_exam])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["firstPass"]["attemptedProblems"] == 0
    assert data["firstPass"]["firstPassCorrectProblems"] == 0


@pytest.mark.asyncio
async def test_summary_first_pass_mixed_correct_and_incorrect(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """One first-correct and one first-incorrect: 50%."""
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    problem_a = make_problem(user_id)
    problem_b = make_problem(user_id)
    database.seed("problems", [problem_a, problem_b])
    database.seed("practice_attempts", [
        make_practice_attempt(user_id, problem_a["_id"], created_at=datetime(2026, 1, 1, tzinfo=UTC), grading_status="correct"),
        make_practice_attempt(user_id, problem_b["_id"], created_at=datetime(2026, 1, 1, tzinfo=UTC), grading_status="incorrect"),
    ])

    response = await client.get("/api/v1/home/summary")
    data = response.json()
    assert data["firstPass"]["attemptedProblems"] == 2
    assert data["firstPass"]["firstPassCorrectProblems"] == 1
    assert data["firstPass"]["percentage"] == 50


@pytest.mark.asyncio
async def test_summary_score_distribution_no_problems_returns_empty(
    home_app: FastAPI, client: AsyncClient
) -> None:
    response = await client.get("/api/v1/home/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["scoreDistribution"]["buckets"] == []


@pytest.mark.asyncio
async def test_summary_score_distribution_negative_zero_positive_buckets(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    now = datetime.now(UTC)
    sixty_days_ago = now - timedelta(days=60)

    # Never-tested: recency=1.0 (createdAt ~now, days_since=0), failure=0.0, last_wrong=1.0 -> raw=2.0 (bucket 2)
    never_tested = make_problem(user_id, created_at=now)

    # Tested correct: base 0.0, rate 1/40 -> recency=0.0+60/40=1.5, failure=0.0, last_wrong=0.5 -> raw=2.0 (bucket 2)
    tested_correct = make_problem(
        user_id,
        last_tested_at=sixty_days_ago,
        last_attempt_correct=True,
        created_at=sixty_days_ago,
    )

    # Tested incorrect, more failures than corrects: base 0.0, rate 1/30 -> recency=0.0+60/30=2.0, failure=2.0, last_wrong=2.0 -> raw=6.0 (bucket 6)
    tested_incorrect = make_problem(
        user_id,
        last_tested_at=sixty_days_ago,
        last_attempt_correct=False,
        correct_count=1,
        failed_count=2,
        created_at=sixty_days_ago,
    )

    database.seed("problems", [never_tested, tested_correct, tested_incorrect])

    response = await client.get("/api/v1/home/summary")
    assert response.status_code == 200
    buckets = response.json()["scoreDistribution"]["buckets"]
    by_start = {b["start"]: b for b in buckets}
    assert list(b["start"] for b in buckets) == sorted(b["start"] for b in buckets)

    # Bucket range spans 2..6 with contiguous empty buckets at 3, 4, 5.
    assert list(by_start.keys()) == [2, 3, 4, 5, 6]

    # Raw score 2.0 (never tested + tested correct) both land in bucket 2.
    assert by_start[2] == {"start": 2, "neverTested": 1, "minAged": 0, "tested": 1, "cooldown": 0}

    # Raw score 6.0 lands in bucket 6 (exact integer boundary).
    assert by_start[6] == {"start": 6, "neverTested": 0, "minAged": 0, "tested": 1, "cooldown": 0}

    # Empty intermediate buckets emitted contiguously.
    assert by_start[3] == {"start": 3, "neverTested": 0, "minAged": 0, "tested": 0, "cooldown": 0}
    assert by_start[4] == {"start": 4, "neverTested": 0, "minAged": 0, "tested": 0, "cooldown": 0}
    assert by_start[5] == {"start": 5, "neverTested": 0, "minAged": 0, "tested": 0, "cooldown": 0}


@pytest.mark.asyncio
async def test_summary_score_distribution_never_tested_vs_tested(
    home_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    now = datetime.now(UTC)
    sixty_days_ago = now - timedelta(days=60)

    tested = make_problem(
        user_id,
        last_tested_at=sixty_days_ago,
        last_attempt_correct=True,
        created_at=sixty_days_ago,
    )
    never = make_problem(user_id, created_at=now)
    database.seed("problems", [tested, never])

    response = await client.get("/api/v1/home/summary")
    buckets = response.json()["scoreDistribution"]["buckets"]
    never_bucket = next(b for b in buckets if b["neverTested"] > 0)
    tested_bucket = next(b for b in buckets if b["tested"] > 0)
    assert never_bucket["neverTested"] == 1
    assert tested_bucket["tested"] == 1


@pytest.mark.asyncio
async def test_summary_score_distribution_includes_disabled_excludes_deleted_and_other_users(
    home_app: FastAPI, client: AsyncClient
) -> None:
    other_user_id = ObjectId()
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    now = datetime.now(UTC)
    sixty_days_ago = now - timedelta(days=60)

    # Disabled but tested problem must be included.
    disabled = make_problem(
        user_id,
        is_disabled=True,
        last_tested_at=sixty_days_ago,
        last_attempt_correct=True,
        created_at=sixty_days_ago,
    )
    # Deleted problem must be excluded (query filters out isDeleted).
    deleted = make_problem(
        user_id,
        is_deleted=True,
        last_tested_at=sixty_days_ago,
        last_attempt_correct=True,
        created_at=sixty_days_ago,
    )
    # Other user's problem must be excluded.
    other = make_problem(other_user_id, created_at=now)
    database.seed("problems", [disabled, deleted, other])

    response = await client.get("/api/v1/home/summary")
    buckets = response.json()["scoreDistribution"]["buckets"]
    tested_total = sum(b["tested"] for b in buckets)
    never_total = sum(b["neverTested"] for b in buckets)
    assert tested_total == 1
    assert never_total == 0


@pytest.mark.asyncio
async def test_summary_score_distribution_raw_not_clamped_regression(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """Raw score must equal the pre-clamp component sum, so non-positive buckets appear."""
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    now = datetime.now(UTC)
    sixty_days_ago = now - timedelta(days=60)

    # Tested correct with many more corrects than failures -> negative failure score.
    # correctCount=10, failedCount=0 -> failure = -sqrt(10) ~ -3.162.
    # lastTestedAt 60d ago, lastAttemptCorrect -> base 0.0, rate 1/40 -> recency=0.0+60/40=1.5; last_wrong=0.5.
    # raw = 1.5 + (-3.162) + 0.5 ~ -1.162 -> floor -2.
    problem = make_problem(
        user_id,
        last_tested_at=sixty_days_ago,
        last_attempt_correct=True,
        correct_count=10,
        failed_count=0,
        created_at=sixty_days_ago,
    )
    database.seed("problems", [problem])

    response = await client.get("/api/v1/home/summary")
    buckets = response.json()["scoreDistribution"]["buckets"]
    assert len(buckets) == 1
    assert buckets[0]["start"] == -2
    assert buckets[0]["tested"] == 1


@pytest.mark.asyncio
async def test_summary_score_distribution_single_bucket_for_one_problem(
    home_app: FastAPI, client: AsyncClient
) -> None:
    """Buckets only range across observed problems."""
    database: FakeDatabase = home_app.state.fake_database
    user_id = home_app.state.user["_id"]
    now = datetime.now(UTC)

    # Never-tested -> recency=1.0 (createdAt ~now), failure=0.0, last_wrong=1.0 -> raw=2.0 (bucket 2).
    problem = make_problem(user_id, created_at=now)
    database.seed("problems", [problem])

    response = await client.get("/api/v1/home/summary")
    buckets = response.json()["scoreDistribution"]["buckets"]
    assert len(buckets) == 1
    assert buckets[0]["start"] == 2
    assert buckets[0]["neverTested"] == 1


@pytest.mark.asyncio
async def test_summary_score_distribution_four_categories(
    home_app_with_min_age: FastAPI, client_with_min_age: AsyncClient
) -> None:
    """Each of Cooldown, Tested, Min aged, and Never tested gets one problem."""
    database: FakeDatabase = home_app_with_min_age.state.fake_database
    user_id = home_app_with_min_age.state.user["_id"]
    now = datetime.now(UTC)
    one_day_ago = now - timedelta(days=1)       # newer than now-3d -> min aged (untested only)
    three_days_ago = now - timedelta(days=3)     # newer than now-7d -> cooldown
    thirty_days_ago = now - timedelta(days=30)  # older than now-7d -> tested
    hundred_days_ago = now - timedelta(days=100)  # older than now-3d -> never tested

    cooldown_p = make_problem(
        user_id,
        last_tested_at=three_days_ago,
        last_attempt_correct=True,
        created_at=hundred_days_ago,
    )
    tested_p = make_problem(
        user_id,
        last_tested_at=thirty_days_ago,
        last_attempt_correct=True,
        created_at=thirty_days_ago,
    )
    min_aged_p = make_problem(user_id, created_at=one_day_ago)
    never_tested_p = make_problem(user_id, created_at=hundred_days_ago)
    database.seed("problems", [cooldown_p, tested_p, min_aged_p, never_tested_p])

    response = await client_with_min_age.get("/api/v1/home/summary")
    buckets = response.json()["scoreDistribution"]["buckets"]

    assert sum(b["cooldown"] for b in buckets) == 1
    assert sum(b["tested"] for b in buckets) == 1
    assert sum(b["minAged"] for b in buckets) == 1
    assert sum(b["neverTested"] for b in buckets) == 1
    # Invariant: each problem counted exactly once across all buckets.
    bucket_total = sum(
        b["neverTested"] + b["minAged"] + b["tested"] + b["cooldown"] for b in buckets
    )
    assert bucket_total == 4


@pytest.mark.asyncio
async def test_summary_score_distribution_cooldown_cutoff_boundary(
    home_app_with_min_age: FastAPI, client_with_min_age: AsyncClient
) -> None:
    """At exact cooldown cutoff classify as Tested; just newer -> Cooldown (strict >)."""
    database: FakeDatabase = home_app_with_min_age.state.fake_database
    user_id = home_app_with_min_age.state.user["_id"]
    now = datetime.now(UTC)
    deep_past = now - timedelta(days=100)

    exactly_cutoff = make_problem(
        user_id,
        last_tested_at=now - timedelta(days=7),
        last_attempt_correct=True,
        created_at=deep_past,
    )
    just_inside = make_problem(
        user_id,
        last_tested_at=now - timedelta(days=7) + timedelta(minutes=5),
        last_attempt_correct=True,
        created_at=deep_past,
    )
    database.seed("problems", [exactly_cutoff, just_inside])

    response = await client_with_min_age.get("/api/v1/home/summary")
    buckets = response.json()["scoreDistribution"]["buckets"]
    assert sum(b["tested"] for b in buckets) == 1
    assert sum(b["cooldown"] for b in buckets) == 1
    assert sum(b["minAged"] for b in buckets) == 0
    assert sum(b["neverTested"] for b in buckets) == 0


@pytest.mark.asyncio
async def test_summary_score_distribution_min_age_cutoff_boundary(
    home_app_with_min_age: FastAPI, client_with_min_age: AsyncClient
) -> None:
    """At exact min-age cutoff classify as Never tested; just newer -> Min aged (strict >)."""
    database: FakeDatabase = home_app_with_min_age.state.fake_database
    user_id = home_app_with_min_age.state.user["_id"]
    now = datetime.now(UTC)

    exactly_cutoff = make_problem(user_id, created_at=now - timedelta(days=3))
    just_inside = make_problem(
        user_id,
        created_at=now - timedelta(days=3) + timedelta(minutes=5),
    )
    database.seed("problems", [exactly_cutoff, just_inside])

    response = await client_with_min_age.get("/api/v1/home/summary")
    buckets = response.json()["scoreDistribution"]["buckets"]
    assert sum(b["neverTested"] for b in buckets) == 1
    assert sum(b["minAged"] for b in buckets) == 1
    assert sum(b["tested"] for b in buckets) == 0
    assert sum(b["cooldown"] for b in buckets) == 0


@pytest.mark.asyncio
async def test_summary_score_distribution_recently_created_tested_problem_not_min_aged(
    home_app_with_min_age: FastAPI, client_with_min_age: AsyncClient
) -> None:
    """A recently created and recently tested problem stays in the tested parent split (Cooldown)."""
    database: FakeDatabase = home_app_with_min_age.state.fake_database
    user_id = home_app_with_min_age.state.user["_id"]
    one_day_ago = datetime.now(UTC) - timedelta(days=1)

    problem = make_problem(
        user_id,
        last_tested_at=one_day_ago,
        last_attempt_correct=True,
        created_at=one_day_ago,
    )
    database.seed("problems", [problem])

    response = await client_with_min_age.get("/api/v1/home/summary")
    buckets = response.json()["scoreDistribution"]["buckets"]
    assert sum(b["cooldown"] for b in buckets) == 1
    assert sum(b["minAged"] for b in buckets) == 0
    assert sum(b["tested"] for b in buckets) == 0
    assert sum(b["neverTested"] for b in buckets) == 0


@pytest.mark.asyncio
async def test_summary_score_distribution_disabled_included_deleted_other_users_excluded_categories(
    home_app_with_min_age: FastAPI, client_with_min_age: AsyncClient
) -> None:
    """Disabled problems are bucketed by category; deleted/other-user problems are excluded."""
    database: FakeDatabase = home_app_with_min_age.state.fake_database
    user_id = home_app_with_min_age.state.user["_id"]
    other_user_id = ObjectId()
    one_day_ago = datetime.now(UTC) - timedelta(days=1)

    disabled_cooldown = make_problem(
        user_id,
        is_disabled=True,
        last_tested_at=one_day_ago,
        last_attempt_correct=True,
        created_at=one_day_ago,
    )
    deleted_cooldown = make_problem(
        user_id,
        is_deleted=True,
        last_tested_at=one_day_ago,
        last_attempt_correct=True,
        created_at=one_day_ago,
    )
    other_min_aged = make_problem(other_user_id, created_at=one_day_ago)
    database.seed("problems", [disabled_cooldown, deleted_cooldown, other_min_aged])

    response = await client_with_min_age.get("/api/v1/home/summary")
    buckets = response.json()["scoreDistribution"]["buckets"]
    bucket_total = sum(
        b["neverTested"] + b["minAged"] + b["tested"] + b["cooldown"] for b in buckets
    )
    assert bucket_total == 1
    assert sum(b["cooldown"] for b in buckets) == 1
    assert sum(b["minAged"] for b in buckets) == 0
