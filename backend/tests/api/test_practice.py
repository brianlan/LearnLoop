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
from tests.api.conftest import FakeDatabase, make_user, make_problem


@pytest.fixture
def practice_app() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    settings = Settings(practice_cooldown_days=7, problem_selection_min_age_days=0)
    user = make_user(ObjectId(), "student")

    application.state.fake_database = database
    application.state.user = user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_current_user] = lambda: deepcopy(user)
    return application


@pytest_asyncio.fixture
async def client(practice_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=practice_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_next_problem_returns_problem(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, text="What is 2+2?", correct_answer_display="4")
    database.seed("problems", [problem])

    response = await client.post("/api/v1/practice/next")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "ok"
    assert data["problem"]["text"] == "What is 2+2?"
    assert data["problem"]["type"] == "short-answer"


@pytest.mark.asyncio
async def test_exposure_count_incremented(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, exposure_count=0)
    database.seed("problems", [problem])

    await client.post("/api/v1/practice/next")

    updated_problem = await database["problems"].find_one({"_id": problem["_id"]})
    assert updated_problem["tracking"]["exposureCount"] == 1


@pytest.mark.asyncio
async def test_last_tested_at_not_updated_by_next(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])

    await client.post("/api/v1/practice/next")

    updated_problem = await database["problems"].find_one({"_id": problem["_id"]})
    assert updated_problem["tracking"]["lastTestedAt"] is None


@pytest.mark.asyncio
async def test_skip_does_not_put_in_cooldown(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id)
    database.seed("problems", [problem])

    response1 = await client.post("/api/v1/practice/next")
    assert response1.json()["status"] == "ok"

    response2 = await client.post("/api/v1/practice/next")
    assert response2.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_no_eligible_when_all_in_cooldown(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    now = datetime.now(UTC)
    problem = make_problem(user_id, last_tested_at=now)
    database.seed("problems", [problem])

    response = await client.post("/api/v1/practice/next")
    assert response.status_code == 200
    assert response.json()["status"] == "no_eligible"


@pytest.mark.asyncio
async def test_no_problems_when_user_has_zero(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    response = await client.post("/api/v1/practice/next")
    assert response.status_code == 200
    assert response.json()["status"] == "no_problems"


@pytest.mark.asyncio
async def test_deleted_problems_excluded(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, is_deleted=True)
    database.seed("problems", [problem])

    response = await client.post("/api/v1/practice/next")
    assert response.status_code == 200
    assert response.json()["status"] == "no_problems"


@pytest.mark.asyncio
async def test_empty_answer_excluded(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, correct_answer_display="")
    database.seed("problems", [problem])

    response = await client.post("/api/v1/practice/next")
    assert response.status_code == 200
    assert response.json()["status"] == "no_problems"


@pytest.mark.asyncio
async def test_no_correct_answer_in_response(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, correct_answer_display="secret")
    database.seed("problems", [problem])

    response = await client.post("/api/v1/practice/next")
    data = response.json()

    assert data["status"] == "ok"
    assert "correctAnswer" not in data["problem"]
    assert "secret" not in str(data["problem"])


@pytest.mark.asyncio
async def test_stats_returns_practiceable_count(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]

    # Create 3 problems with correct answers
    problems = [
        make_problem(user_id, text=f"Problem {i}", correct_answer_display=str(i))
        for i in range(3)
    ]
    # Create 1 problem without correct answer (should not be counted)
    no_answer = make_problem(user_id, text="No answer", correct_answer_display="")
    database.seed("problems", problems + [no_answer])

    response = await client.get("/api/v1/practice/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["practiceableCount"] == 3


@pytest.mark.asyncio
async def test_stats_excludes_cooldown_problems(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    now = datetime.now(UTC)

    recent = make_problem(user_id, text="Recent", correct_answer_display="1", last_tested_at=now)
    old = make_problem(user_id, text="Old", correct_answer_display="2", last_tested_at=now - timedelta(days=10))
    never = make_problem(user_id, text="Never", correct_answer_display="3")

    database.seed("problems", [recent, old, never])

    response = await client.get("/api/v1/practice/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["practiceableCount"] == 2


@pytest.mark.asyncio
async def test_next_problem_includes_graph_dsl(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, text="Triangle problem", correct_answer_display="4")
    problem["graphDsl"] = "board.create('point', [0, 0], {name:'A'});"
    database.seed("problems", [problem])

    response = await client.post("/api/v1/practice/next")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["problem"]["graphDsl"] == "board.create('point', [0, 0], {name:'A'});"
    assert "correctAnswer" not in data["problem"]


@pytest.mark.asyncio
async def test_next_problem_without_graph_dsl(
    practice_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, text="Text only", correct_answer_display="4")
    database.seed("problems", [problem])

    response = await client.post("/api/v1/practice/next")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "graphDsl" not in data["problem"]


@pytest.fixture
def practice_app_with_min_age() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    settings = Settings(practice_cooldown_days=7, problem_selection_min_age_days=3)
    user = make_user(ObjectId(), "student")

    application.state.fake_database = database
    application.state.user = user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_current_user] = lambda: deepcopy(user)
    return application


@pytest_asyncio.fixture
async def client_with_min_age(practice_app_with_min_age: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=practice_app_with_min_age)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_stats_excludes_too_new_problems(
    practice_app_with_min_age: FastAPI,
    client_with_min_age: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app_with_min_age.state.fake_database
    user_id = practice_app_with_min_age.state.user["_id"]
    old_problem = make_problem(user_id, text="Old", correct_answer_display="1")
    old_problem["createdAt"] = datetime.now(UTC) - timedelta(days=10)
    new_problem = make_problem(user_id, text="New", correct_answer_display="2")
    database.seed("problems", [old_problem, new_problem])

    response = await client_with_min_age.get("/api/v1/practice/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["practiceableCount"] == 1


@pytest.mark.asyncio
async def test_next_returns_no_eligible_when_all_too_new(
    practice_app_with_min_age: FastAPI,
    client_with_min_age: AsyncClient,
) -> None:
    database: FakeDatabase = practice_app_with_min_age.state.fake_database
    user_id = practice_app_with_min_age.state.user["_id"]
    new_problem = make_problem(user_id, text="New", correct_answer_display="1")
    database.seed("problems", [new_problem])

    response = await client_with_min_age.post("/api/v1/practice/next")
    assert response.status_code == 200
    assert response.json()["status"] == "no_eligible"
