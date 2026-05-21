from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.presentation.deps import get_current_user, get_database
from tests.api.conftest import FakeDatabase, make_user, make_problem


@pytest.fixture
def practice_app() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    user = make_user(ObjectId(), "student")

    application.state.fake_database = database
    application.state.user = user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_current_user] = lambda: deepcopy(user)
    return application


@pytest_asyncio.fixture
async def client(practice_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=practice_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_submit_correct_answer(client: AsyncClient, practice_app: FastAPI) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, problem_type="fill-in-the-blank", correct_answer_display="4")
    database.seed("problems", [problem])

    response = await client.post(
        "/api/v1/practice/attempts",
        json={"problemId": str(problem["_id"]), "submittedAnswer": "4"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["gradingStatus"] == "correct"
    assert data["gradingMethod"] == "normalized-match"


@pytest.mark.asyncio
async def test_submit_wrong_answer(client: AsyncClient, practice_app: FastAPI) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, problem_type="fill-in-the-blank", correct_answer_display="4")
    database.seed("problems", [problem])

    response = await client.post(
        "/api/v1/practice/attempts",
        json={"problemId": str(problem["_id"]), "submittedAnswer": "5"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["gradingStatus"] == "incorrect"


@pytest.mark.asyncio
async def test_tracking_updated_correct(client: AsyncClient, practice_app: FastAPI) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, problem_type="fill-in-the-blank", correct_answer_display="4")
    database.seed("problems", [problem])

    await client.post(
        "/api/v1/practice/attempts",
        json={"problemId": str(problem["_id"]), "submittedAnswer": "4"},
    )

    updated = await database["problems"].find_one({"_id": problem["_id"]})
    tracking = updated["tracking"]
    assert tracking["correctCount"] == 1
    assert tracking["failedCount"] == 0
    assert tracking["lastAttemptCorrect"] is True


@pytest.mark.asyncio
async def test_tracking_updated_wrong(client: AsyncClient, practice_app: FastAPI) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, correct_answer_display="4")
    database.seed("problems", [problem])

    await client.post(
        "/api/v1/practice/attempts",
        json={"problemId": str(problem["_id"]), "submittedAnswer": "5"},
    )

    updated = await database["problems"].find_one({"_id": problem["_id"]})
    tracking = updated["tracking"]
    assert tracking["correctCount"] == 0
    assert tracking["failedCount"] == 1
    assert tracking["lastAttemptCorrect"] is False


@pytest.mark.asyncio
async def test_submit_nonexistent_problem(client: AsyncClient, practice_app: FastAPI) -> None:
    fake_id = str(ObjectId())
    response = await client.post(
        "/api/v1/practice/attempts",
        json={"problemId": fake_id, "submittedAnswer": "answer"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_history_returns_empty(client: AsyncClient, practice_app: FastAPI) -> None:
    response = await client.get("/api/v1/practice/history")
    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.asyncio
async def test_history_returns_data(client: AsyncClient, practice_app: FastAPI) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, text="Test problem", correct_answer_display="answer")
    database.seed("problems", [problem])

    now = datetime.now(UTC)
    attempt = {
        "_id": ObjectId(),
        "userId": user_id,
        "problemId": problem["_id"],
        "submittedAnswer": "answer",
        "gradingStatus": "correct",
        "gradingMethod": "normalized-match",
        "createdAt": now,
    }
    database.seed("practice_attempts", [attempt])

    response = await client.get("/api/v1/practice/history")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["problemId"] == str(problem["_id"])
    assert item["problemText"] == "Test problem"
    assert item["summary"]["totalAttempts"] == 1
    assert item["summary"]["correctCount"] == 1
    assert len(item["attempts"]) == 1


@pytest.mark.asyncio
async def test_no_correct_answer_in_result(client: AsyncClient, practice_app: FastAPI) -> None:
    database: FakeDatabase = practice_app.state.fake_database
    user_id = practice_app.state.user["_id"]
    problem = make_problem(user_id, problem_type="fill-in-the-blank", correct_answer_display="secret")
    database.seed("problems", [problem])

    response = await client.post(
        "/api/v1/practice/attempts",
        json={"problemId": str(problem["_id"]), "submittedAnswer": "guess"},
    )
    data = response.json()
    assert "correctAnswer" not in data
    assert "secret" not in str(data)
