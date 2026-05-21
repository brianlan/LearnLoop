from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.presentation.deps import get_current_user, get_database


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = [deepcopy(document) for document in documents]

    def sort(self, field: str, direction: int) -> FakeCursor:
        reverse = direction < 0
        self._documents.sort(
            key=lambda document: cast(Any, document.get(field)),
            reverse=reverse,
        )
        return self

    def skip(self, amount: int) -> FakeCursor:
        return self

    def limit(self, amount: int) -> FakeCursor:
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return deepcopy(self._documents)


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = deepcopy(documents)

    def seed(self, document: dict[str, Any]) -> None:
        self._documents.append(deepcopy(document))

    def find(self, query: dict[str, Any]) -> FakeCursor:
        matching = [
            doc for doc in self._documents
            if matches_query(doc, query)
        ]
        return FakeCursor(matching)

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for document in self._documents:
            if matches_query(document, query):
                return deepcopy(document)
        return None

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
    ) -> None:
        for document in self._documents:
            if matches_query(document, query):
                if "$set" in update:
                    for key, value in update["$set"].items():
                        document[key] = deepcopy(value)

    async def insert_one(self, document: dict[str, Any]) -> None:
        self._documents.append(deepcopy(document))


class FakeDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection([])
        return self._collections[name]

    def seed(self, collection: str, documents: list[dict[str, Any]]) -> None:
        for document in documents:
            self[collection].seed(document)


def matches_query(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        actual = document.get(key)
        if actual is None:
            return False
        if isinstance(value, dict) and "$in" in value:
            if actual not in value["$in"]:
                return False
            continue
        if isinstance(value, dict) and "$ne" in value:
            if actual == value["$ne"]:
                return False
            continue
        if actual != value:
            return False
    return True


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
    is_deleted: bool = False,
    last_tested_at: datetime | None = None,
    exposure_count: int = 0,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": text,
        "problemType": problem_type,
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
            "correctCount": 0,
            "failedCount": 0,
            "lastTestedAt": last_tested_at,
            "lastAttemptCorrect": None,
        },
        "isDeleted": is_deleted,
        "deletedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }


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
