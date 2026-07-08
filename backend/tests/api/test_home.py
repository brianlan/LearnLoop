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
