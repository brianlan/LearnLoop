from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.vlm.client import VLMError
from app.main import create_app
from app.presentation.deps import get_current_user, get_database
from app.presentation.exams import (
    get_exam_mongo_adapter,
    get_exam_storage,
    get_exam_vlm_client,
)


class FakeInsertOneResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = [deepcopy(document) for document in documents]
        self._skip = 0
        self._limit: int | None = None

    def sort(self, field: str, direction: int) -> FakeCursor:
        reverse = direction < 0
        self._documents.sort(
            key=lambda document: cast(Any, document.get(field)),
            reverse=reverse,
        )
        return self

    def skip(self, amount: int) -> FakeCursor:
        self._skip = amount
        return self

    def limit(self, amount: int) -> FakeCursor:
        self._limit = amount
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        documents = self._documents[self._skip :]
        effective_limit = self._limit if self._limit is not None else length
        if effective_limit is not None:
            documents = documents[:effective_limit]
        return [deepcopy(document) for document in documents]


class FakeCollection:
    def __init__(self) -> None:
        self._documents: list[dict[str, Any]] = []

    def seed(self, *documents: dict[str, Any]) -> None:
        self._documents.extend(deepcopy(list(documents)))

    async def find_one(self, query: dict[str, Any], session: Any | None = None) -> dict[str, Any] | None:
        for document in self._documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

    async def insert_one(self, document: dict[str, Any], session: Any | None = None) -> FakeInsertOneResult:
        stored = deepcopy(document)
        if "_id" not in stored:
            stored["_id"] = ObjectId()
        self._documents.append(stored)
        return FakeInsertOneResult(stored["_id"])

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        session: Any | None = None,
    ) -> FakeUpdateResult:
        for document in self._documents:
            if not _matches(document, query):
                continue
            for key, value in update.get("$set", {}).items():
                document[key] = deepcopy(value)
            return FakeUpdateResult(1)
        return FakeUpdateResult(0)

    async def count_documents(self, query: dict[str, Any], session: Any | None = None) -> int:
        return sum(1 for document in self._documents if _matches(document, query))

    def find(self, query: dict[str, Any], session: Any | None = None) -> FakeCursor:
        return FakeCursor([document for document in self._documents if _matches(document, query)])


class FakeDatabase:
    def __init__(self) -> None:
        self._collections = {
            "exams": FakeCollection(),
            "problems": FakeCollection(),
            "users": FakeCollection(),
            "sessions": FakeCollection(),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections[name]


class FakeSession:
    async def with_transaction(self, callback: Any) -> Any:
        return await callback(self)


class FakeMongoAdapter:
    @asynccontextmanager
    async def start_session(self) -> AsyncIterator[FakeSession]:
        yield FakeSession()


class FakeStorage:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def seed(self, bucket: str, key: str, payload: bytes) -> None:
        self._objects[(bucket, key)] = payload

    def get_object(self, bucket: str, key: str) -> bytes:
        return self._objects[(bucket, key)]


class FakeGradingResult:
    def __init__(self, *, is_correct: bool, feedback: str = "", model: str = "fake-vlm") -> None:
        self.is_correct = is_correct
        self.feedback = feedback
        self.model = model
        self.raw_provider_response = {"isCorrect": is_correct, "feedback": feedback}


class FakeVLMClient:
    def __init__(self) -> None:
        self.responses: list[Any] = []
        self.calls = 0

    async def grade_short_answer(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
        user_answer: str,
        correct_answer: str,
    ) -> FakeGradingResult:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if document.get(key) != value:
            return False
    return True


def make_user(username: str = "student1") -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "username": username,
        "status": "active",
        "createdAt": now,
        "updatedAt": now,
    }


def make_problem(
    user_id: ObjectId,
    *,
    text: str,
    problem_type: str,
    correct_answer: str,
    normalized_text: str | None = None,
    normalized_set: list[str] | None = None,
    is_deleted: bool = False,
    source_image: bool = True,
    tracking: dict[str, Any] | None = None,
    last_tested_at: datetime | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    object_key = f"users/{user_id}/images/{ObjectId()}.png"
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": text,
        "problemType": problem_type,
        "graphDsl": None,
        "correctAnswer": {
            "display": correct_answer,
            "normalizedText": normalized_text if normalized_text is not None else correct_answer.lower(),
            "normalizedSet": normalized_set or [],
            "format": "set" if normalized_set else "single",
        },
        "tags": ["math"],
        "sourceImage": {
            "bucket": "learnloop-media",
            "objectKey": object_key,
            "contentType": "image/png",
            "sizeBytes": 4,
            "sha256": None,
            "uploadedAt": now,
        }
        if source_image
        else None,
        "origin": {},
        "tracking": tracking
        or {
            "exposureCount": 0,
            "correctCount": 0,
            "failedCount": 0,
            "lastTestedAt": last_tested_at,
            "lastAttemptCorrect": None,
        },
        "isDeleted": is_deleted,
        "deletedAt": now if is_deleted else None,
        "createdAt": now,
        "updatedAt": now,
    }


@pytest_asyncio.fixture
async def exams_app() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    adapter = FakeMongoAdapter()
    storage = FakeStorage()
    vlm = FakeVLMClient()
    user = make_user()

    application.state.fake_database = database
    application.state.fake_adapter = adapter
    application.state.fake_storage = storage
    application.state.fake_vlm = vlm
    application.state.primary_user = user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_current_user] = lambda: deepcopy(user)
    application.dependency_overrides[get_exam_mongo_adapter] = lambda: adapter
    application.dependency_overrides[get_exam_storage] = lambda: storage
    application.dependency_overrides[get_exam_vlm_client] = lambda: vlm
    return application


@pytest_asyncio.fixture
async def client(exams_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=exams_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_create_exam_snapshots_selected_problems(exams_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    user_id = exams_app.state.primary_user["_id"]
    older = datetime.now(UTC) - timedelta(days=10)
    problem_one = make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4", last_tested_at=older)
    problem_two = make_problem(user_id, text="Capital of France?", problem_type="short-answer", correct_answer="Paris")
    database["problems"].seed(problem_one, problem_two)
    storage.seed(problem_one["sourceImage"]["bucket"], problem_one["sourceImage"]["objectKey"], b"img1")
    storage.seed(problem_two["sourceImage"]["bucket"], problem_two["sourceImage"]["objectKey"], b"img2")

    response = await client.post("/api/v1/exams", json={"maxProblemCount": 2})

    assert response.status_code == 201
    body = response.json()["exam"]
    assert body["state"] == "in-progress"
    assert body["configSnapshot"]["selectionPolicy"] == {"recencyWeight": 1.0, "failureWeight": 1.0}
    assert len(body["items"]) == 2
    assert body["items"][0]["problem"]["correctAnswer"] is None
    stored_exam = database["exams"]._documents[0]
    assert stored_exam["items"][0]["problemSnapshot"]["correctAnswer"]["display"] in {"4", "Paris"}


@pytest.mark.asyncio
async def test_create_exam_rejects_when_active_exam_exists(exams_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    database["problems"].seed(make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4"))
    database["exams"].seed(
        {
            "_id": ObjectId(),
            "userId": user_id,
            "state": "in-progress",
            "configSnapshot": {"maxProblemCount": 1, "selectionPolicy": {"recencyWeight": 1.0, "failureWeight": 1.0}, "generatedAt": datetime.now(UTC)},
            "items": [],
            "summary": {"totalProblems": 0, "answeredProblems": 0, "gradedProblems": 0, "pendingProblems": 0, "correctProblems": 0, "failedProblems": 0, "score": None},
            "createdAt": datetime.now(UTC),
            "startedAt": None,
            "submittedAt": None,
            "updatedAt": datetime.now(UTC),
        }
    )

    response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACTIVE_EXAM_EXISTS"


@pytest.mark.asyncio
async def test_create_exam_rejects_when_no_eligible_problems(exams_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    ineligible = make_problem(user_id, text="Deleted", problem_type="fill-in-the-blank", correct_answer="4", is_deleted=True)
    answerless = make_problem(user_id, text="No answer", problem_type="fill-in-the-blank", correct_answer="")
    database["problems"].seed(ineligible, answerless)

    response = await client.post("/api/v1/exams", json={"maxProblemCount": 2})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_ELIGIBLE_PROBLEMS"


@pytest.mark.asyncio
async def test_get_active_exam_sets_started_at_once_and_resume_returns_saved_answer(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    database["problems"].seed(problem)
    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam_id = create_response.json()["exam"]["id"]
    item_id = create_response.json()["exam"]["items"][0]["itemId"]

    first_active = await client.get("/api/v1/exams/active")
    assert first_active.status_code == 200
    started_at = first_active.json()["exam"]["startedAt"]
    assert started_at is not None

    save_response = await client.patch(
        f"/api/v1/exams/{exam_id}/items/{item_id}/answer",
        json={"answer": "4"},
    )
    assert save_response.status_code == 200
    assert save_response.json()["item"]["answer"]["raw"] == "4"

    second_active = await client.get("/api/v1/exams/active")
    assert second_active.status_code == 200
    assert second_active.json()["exam"]["startedAt"] == started_at
    assert second_active.json()["exam"]["items"][0]["answer"]["raw"] == "4"


@pytest.mark.asyncio
async def test_submit_exam_grades_items_updates_tracking_and_history(exams_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_vlm
    user_id = exams_app.state.primary_user["_id"]

    objective = make_problem(
        user_id,
        text="2+2?",
        problem_type="fill-in-the-blank",
        correct_answer="4",
        tracking={"exposureCount": 1, "correctCount": 1, "failedCount": 0, "lastTestedAt": None, "lastAttemptCorrect": True},
    )
    short = make_problem(
        user_id,
        text="Explain Pythagorean theorem",
        problem_type="short-answer",
        correct_answer="A right triangle relation",
        tracking={"exposureCount": 2, "correctCount": 1, "failedCount": 1, "lastTestedAt": None, "lastAttemptCorrect": False},
    )
    database["problems"].seed(objective, short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"image")
    vlm.responses = [FakeGradingResult(is_correct=True, feedback="good")]

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 2})
    exam = create_response.json()["exam"]
    objective_item = next(item for item in exam["items"] if item["problem"]["problemType"] == "fill-in-the-blank")
    short_item = next(item for item in exam["items"] if item["problem"]["problemType"] == "short-answer")

    await client.patch(f"/api/v1/exams/{exam['id']}/items/{objective_item['itemId']}/answer", json={"answer": "4"})
    await client.patch(f"/api/v1/exams/{exam['id']}/items/{short_item['itemId']}/answer", json={"answer": "triangle side rule"})

    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")

    assert submit_response.status_code == 200
    submitted_exam = submit_response.json()["exam"]
    assert submitted_exam["state"] == "submitted"
    assert submitted_exam["summary"] == {
        "totalProblems": 2,
        "answeredProblems": 2,
        "gradedProblems": 2,
        "pendingProblems": 0,
        "correctProblems": 2,
        "failedProblems": 0,
        "score": 1.0,
    }
    submitted_short = next(item for item in submitted_exam["items"] if item["itemId"] == short_item["itemId"])
    assert submitted_short["grading"]["status"] == "correct"
    assert submitted_short["grading"]["method"] == "vlm"
    assert submitted_short["problem"]["correctAnswer"]["display"] == "A right triangle relation"
    assert database["problems"]._documents[0]["tracking"]["exposureCount"] == 2
    assert database["problems"]._documents[1]["tracking"]["exposureCount"] == 3

    history_response = await client.get("/api/v1/exams")
    assert history_response.status_code == 200
    assert history_response.json()["total"] == 1
    assert history_response.json()["items"][0]["id"] == exam["id"]

    detail_response = await client.get(f"/api/v1/exams/{exam['id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["exam"]["summary"]["score"] == 1.0


@pytest.mark.asyncio
async def test_submit_exam_marks_pending_review_after_vlm_retry_and_self_report_updates_tracking(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_vlm
    user_id = exams_app.state.primary_user["_id"]
    short = make_problem(
        user_id,
        text="Explain gravity",
        problem_type="short-answer",
        correct_answer="Mass attracts mass",
        tracking={"exposureCount": 4, "correctCount": 2, "failedCount": 2, "lastTestedAt": None, "lastAttemptCorrect": False},
    )
    database["problems"].seed(short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"image")
    vlm.responses = [
        VLMError("temporary", code="vlm-timeout", retryable=True),
        VLMError("still broken", code="vlm-network-error", retryable=True),
    ]

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam = create_response.json()["exam"]
    item_id = exam["items"][0]["itemId"]
    await client.patch(f"/api/v1/exams/{exam['id']}/items/{item_id}/answer", json={"answer": "my answer"})

    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")

    assert submit_response.status_code == 200
    submitted_exam = submit_response.json()["exam"]
    assert submitted_exam["summary"] == {
        "totalProblems": 1,
        "answeredProblems": 1,
        "gradedProblems": 0,
        "pendingProblems": 1,
        "correctProblems": 0,
        "failedProblems": 0,
        "score": None,
    }
    assert submitted_exam["items"][0]["grading"]["status"] == "pending-review"
    assert submitted_exam["items"][0]["grading"]["retryCount"] == 1
    assert database["problems"]._documents[0]["tracking"]["exposureCount"] == 4

    report_response = await client.post(
        f"/api/v1/exams/{exam['id']}/items/{item_id}/self-report",
        json={"isCorrect": False},
    )

    assert report_response.status_code == 200
    assert report_response.json()["item"]["grading"]["status"] == "incorrect"
    assert report_response.json()["summary"] == {
        "totalProblems": 1,
        "answeredProblems": 1,
        "gradedProblems": 1,
        "pendingProblems": 0,
        "correctProblems": 0,
        "failedProblems": 1,
        "score": 0.0,
    }
    assert database["problems"]._documents[0]["tracking"]["exposureCount"] == 5
    assert database["problems"]._documents[0]["tracking"]["failedCount"] == 3
