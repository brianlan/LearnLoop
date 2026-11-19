from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.exam_grading import build_exam_summary, build_tracking_update
from app.infrastructure.vlm.client import VLMError
from app.main import create_app
from app.infrastructure.config.settings import Settings
from app.presentation.deps import (
    get_app_settings,
    get_current_user,
    get_database,
    get_grading_vlm_client,
    get_mongo_adapter,
    get_s3_storage,
)
from tests.api.conftest import FakeDatabase
from tests.test_utils.db_fakes import FakeCollection


class FakeSession:
    async def with_transaction(self, callback: Any) -> Any:
        return await callback(self)


class FakeMongoAdapter:
    @asynccontextmanager
    async def start_session(self) -> AsyncIterator[FakeSession]:
        yield FakeSession()


class _TransientTransactionError(Exception):
    """Stands in for Mongo's TransientTransactionError abort."""


class _ExamFindProbe:
    """Proxies the fake exams collection to model concurrent writes and
    transient aborts deterministically (#712): runs ``side_effect`` after
    read ``side_effect_after`` and aborts read ``abort_on`` before anything
    is written."""

    def __init__(
        self,
        inner: FakeCollection,
        *,
        side_effect_after: int | None = None,
        side_effect: Callable[[], None] | None = None,
        abort_on: int | None = None,
    ) -> None:
        self._inner = inner
        self._side_effect_after = side_effect_after
        self._side_effect = side_effect
        self._abort_on = abort_on
        self._reads = 0

    async def find_one(
        self,
        query: dict[str, Any],
        sort: Any | None = None,
        session: Any | None = None,
    ) -> dict[str, Any] | None:
        self._reads += 1
        if self._abort_on is not None and self._reads == self._abort_on:
            raise _TransientTransactionError()
        document = await self._inner.find_one(query, sort=sort, session=session)
        if self._side_effect_after is not None and self._reads == self._side_effect_after:
            assert self._side_effect is not None
            self._side_effect()
        return document

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RetryingSession(FakeSession):
    """with_transaction that aborts the first attempt before any write and
    retries once after ``on_retry`` runs, mirroring Mongo's transient-error
    retry where the callback reloads its snapshot."""

    def __init__(self, on_retry: Callable[[], None]) -> None:
        self._on_retry = on_retry

    async def with_transaction(self, callback: Any) -> Any:
        try:
            return await callback(self)
        except _TransientTransactionError:
            self._on_retry()
            return await callback(self)


class _RetryingOnceAdapter(FakeMongoAdapter):
    def __init__(self, on_retry: Callable[[], None]) -> None:
        self._on_retry = on_retry

    @asynccontextmanager
    async def start_session(self) -> AsyncIterator[_RetryingSession]:
        yield _RetryingSession(self._on_retry)


def _apply_concurrent_self_report(
    database: FakeDatabase,
    exam_id: ObjectId,
    item_id: str,
    user_id: ObjectId,
    *,
    is_correct: bool,
    resolved_at: datetime,
) -> None:
    """Commit the writes a concurrent self-report of ``item_id`` would
    persist: item grading, exam summary, and problem tracking (#712)."""
    stored = next(doc for doc in database["exams"]._documents if doc["_id"] == exam_id)
    for item in stored["items"]:
        if item["itemId"] != item_id:
            continue
        item["grading"] = {
            "status": "correct" if is_correct else "incorrect",
            "method": "self-report",
            "isCorrect": is_correct,
            "score": 1.0 if is_correct else 0.0,
            "gradedAt": resolved_at,
            "selfReportedCorrect": is_correct,
        }
        problem = next(
            doc for doc in database["problems"]._documents if doc["_id"] == item["problemId"]
        )
        problem["tracking"] = build_tracking_update(
            dict(problem.get("tracking", {})),
            now=resolved_at,
            is_correct=is_correct,
        )
        problem["updatedAt"] = resolved_at
    stored["summary"] = build_exam_summary(stored["items"])
    stored["updatedAt"] = resolved_at


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
        self.last_call_kwargs: dict[str, Any] = {}

    async def grade_short_answer(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
        problem_text: str,
        user_answer: str,
        correct_answer: str,
        subject: str = "math",
        graph_dsl: str | None = None,
    ) -> FakeGradingResult:
        self.calls += 1
        self.last_call_kwargs = {
            "image_url": image_url,
            "image_base64": image_base64,
            "problem_text": problem_text,
            "user_answer": user_answer,
            "correct_answer": correct_answer,
            "subject": subject,
            "graph_dsl": graph_dsl,
        }
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response





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
    subject: str = "math",
    normalized_text: str | None = None,
    normalized_set: list[str] | None = None,
    is_deleted: bool = False,
    is_disabled: bool = False,
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
        "subject": subject,
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
        "isDisabled": is_disabled,
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
    application.state.fake_grading_vlm = vlm
    application.state.fake_vlm = vlm
    application.state.primary_user = user

    settings = Settings(
        problem_selection_min_age_days=0,
        problem_selection_cooldown_days=7,
        problem_selection_last_wrong_weight=1.0,
        problem_selection_failure_rate_weight=1.0,
        problem_selection_recency_weight=1.0,
    )
    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_current_user] = lambda: deepcopy(user)
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_mongo_adapter] = lambda: adapter
    application.dependency_overrides[get_s3_storage] = lambda: storage
    application.dependency_overrides[get_grading_vlm_client] = lambda: vlm
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
    assert body["configSnapshot"]["selectionPolicy"] == {"cooldownDays": 7, "lastWrongWeight": 1.0, "failureRateWeight": 1.0, "recencyWeight": 1.0, "minProblemAgeDays": 0}
    assert len(body["items"]) == 2
    assert body["items"][0]["problem"]["correctAnswer"] is None
    stored_exam = database["exams"]._documents[0]
    assert stored_exam["items"][0]["problemSnapshot"]["correctAnswer"]["display"] in {"4", "Paris"}


@pytest.mark.asyncio
@pytest.mark.parametrize("max_problem_count", [1, 30])
async def test_create_exam_accepts_problem_count_boundaries(
    exams_app: FastAPI,
    client: AsyncClient,
    max_problem_count: int,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    user_id = exams_app.state.primary_user["_id"]
    problems = [
        make_problem(
            user_id,
            text=f"Problem {index}?",
            problem_type="fill-in-the-blank",
            correct_answer=str(index),
        )
        for index in range(max_problem_count)
    ]
    database["problems"].seed(*problems)
    for problem in problems:
        storage.seed(problem["sourceImage"]["bucket"], problem["sourceImage"]["objectKey"], b"img")

    response = await client.post("/api/v1/exams", json={"maxProblemCount": max_problem_count})

    assert response.status_code == 201
    body = response.json()["exam"]
    assert body["configSnapshot"]["maxProblemCount"] == max_problem_count
    assert len(body["items"]) == max_problem_count


@pytest.mark.asyncio
@pytest.mark.parametrize("max_problem_count", [0, 31])
async def test_create_exam_rejects_problem_count_outside_product_limit(
    client: AsyncClient,
    max_problem_count: int,
) -> None:
    response = await client.post("/api/v1/exams", json={"maxProblemCount": max_problem_count})

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("filters", [{}, {"folderIds": ["unfiled"], "tags": ["math"]}])
async def test_create_exam_rejects_when_active_exam_exists(
    exams_app: FastAPI, client: AsyncClient, filters: dict[str, Any]
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    database["problems"].seed(make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4"))
    database["exams"].seed(
        {
            "_id": ObjectId(),
            "userId": user_id,
            "state": "in-progress",
            "configSnapshot": {"maxProblemCount": 1, "selectionPolicy": {"cooldownDays": 7, "lastWrongWeight": 1.0, "failureRateWeight": 1.0, "recencyWeight": 1.0, "minProblemAgeDays": 0}, "generatedAt": datetime.now(UTC)},
            "items": [],
            "summary": {"totalProblems": 0, "answeredProblems": 0, "gradedProblems": 0, "pendingProblems": 0, "correctProblems": 0, "failedProblems": 0, "score": None},
            "createdAt": datetime.now(UTC),
            "startedAt": None,
            "submittedAt": None,
            "updatedAt": datetime.now(UTC),
        }
    )

    response = await client.post("/api/v1/exams", json={"maxProblemCount": 1, **filters})

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
async def test_create_exam_excludes_disabled_problems(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    user_id = exams_app.state.primary_user["_id"]
    disabled = make_problem(
        user_id, text="Disabled", problem_type="fill-in-the-blank", correct_answer="4", is_disabled=True
    )
    enabled = make_problem(
        user_id, text="Enabled", problem_type="short-answer", correct_answer="Paris"
    )
    database["problems"].seed(disabled, enabled)
    storage.seed(disabled["sourceImage"]["bucket"], disabled["sourceImage"]["objectKey"], b"img")
    storage.seed(enabled["sourceImage"]["bucket"], enabled["sourceImage"]["objectKey"], b"img")

    response = await client.post("/api/v1/exams", json={"maxProblemCount": 2})

    assert response.status_code == 201
    body = response.json()["exam"]
    assert len(body["items"]) == 1
    assert body["items"][0]["problem"]["text"] == "Enabled"


@pytest.mark.asyncio
async def test_create_exam_no_eligible_when_all_disabled(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    disabled = make_problem(
        user_id, text="Disabled", problem_type="fill-in-the-blank", correct_answer="4", is_disabled=True
    )
    database["problems"].seed(disabled)

    response = await client.post("/api/v1/exams", json={"maxProblemCount": 2})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_ELIGIBLE_PROBLEMS"


@pytest.mark.asyncio
async def test_create_exam_returns_validation_error_for_malformed_problem_answer(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    malformed_problem = make_problem(
        user_id,
        text="Broken answer",
        problem_type="fill-in-the-blank",
        correct_answer="4",
    )
    malformed_problem["correctAnswer"] = {"display": "4"}
    database["problems"].seed(malformed_problem)

    response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PROBLEM_DATA"
    assert response.json()["error"]["details"]["problemId"] == str(malformed_problem["_id"])


def _seed_random_filter_scope(
    exams_app: FastAPI,
) -> tuple[dict[str, ObjectId], dict[str, dict[str, Any]]]:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    folders = {name: ObjectId() for name in ("parent", "child", "grandchild", "other", "empty")}
    database["folders"].seed(*[
        {"_id": folder_id, "userId": user_id,
         "parentId": folders["parent"] if name == "child" else folders["child"] if name == "grandchild" else None}
        for name, folder_id in folders.items()
    ])
    problems = {}
    for name, folder, tags in [
        ("parent", "parent", ["algebra", "review"]),
        ("child", "child", ["algebra", "review"]),
        ("grandchild", "grandchild", ["algebra", "review"]),
        ("other", "other", ["algebra", "review"]),
        ("one-tag", "child", ["algebra"]),
        ("other-tag", "parent", ["review"]),
        ("case", "parent", ["Algebra", "review"]),
        ("null", None, ["algebra", "review"]),
        ("missing", None, ["algebra", "review"]),
        ("no-tags", "parent", None),
    ]:
        problem = make_problem(user_id, text=name, problem_type="fill-in-the-blank", correct_answer="4")
        if folder is not None:
            problem["folderId"] = str(folders[folder])
        elif name == "null":
            problem["folderId"] = None
        if tags is None:
            problem.pop("tags")
        else:
            problem["tags"] = tags
        problems[name] = problem
    foreign = deepcopy(problems["child"])
    foreign.update({"_id": ObjectId(), "userId": ObjectId(), "text": "foreign"})
    database["problems"].seed(*problems.values(), foreign)
    return folders, problems


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("folder_names", "tags", "expected_names"),
    [
        (None, None, None),
        ([], [], None),
        ([], [" ", ""], None),
        (["parent"], None, ["parent", "child", "grandchild", "one-tag", "other-tag", "case", "no-tags"]),
        (["parent", "other"], [], ["parent", "child", "grandchild", "other", "one-tag", "other-tag", "case", "no-tags"]),
        (["parent", "child", "parent"], ["algebra", "review"], ["parent", "child", "grandchild"]),
        (["unfiled"], [], ["null", "missing"]),
        (["unfiled", "other"], [], ["null", "missing", "other"]),
        ([], ["algebra", "review"], ["parent", "child", "grandchild", "other", "null", "missing"]),
        ([], ["Algebra", "review"], ["case"]),
        (["parent", "unfiled"], [" algebra ", "review", "algebra", " "], ["parent", "child", "grandchild", "null", "missing"]),
        (["empty"], [], []),
        ([], ["nonexistent"], []),
        (["other"], ["Algebra"], []),
    ],
)
async def test_random_exam_filter_membership_and_shortfalls(
    exams_app: FastAPI,
    client: AsyncClient,
    folder_names: list[str] | None,
    tags: list[str] | None,
    expected_names: list[str] | None,
) -> None:
    folders, problems = _seed_random_filter_scope(exams_app)
    payload: dict[str, Any] = {"mode": "random", "maxProblemCount": 30}
    if folder_names is not None:
        payload["folderIds"] = [str(folders[name]) if name != "unfiled" else name for name in folder_names]
    if tags is not None:
        payload["tags"] = tags

    response = await client.post("/api/v1/exams", json=payload)

    database: FakeDatabase = exams_app.state.fake_database
    if expected_names == []:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "NO_ELIGIBLE_PROBLEMS"
        assert database["exams"]._documents == []
        return
    assert response.status_code == 201
    exam = response.json()["exam"]
    expected_ids = sorted(str(problems[name]["_id"]) for name in (expected_names or problems))
    # N exceeds the whole pool: exact membership also catches overlap duplicates,
    # lost candidates, foreign rows, and out-of-scope shortfall filling.
    assert sorted(item["problemId"] for item in exam["items"]) == expected_ids
    assert exam["summary"]["totalProblems"] == len(expected_ids) < 30
    stored = database["exams"]._documents[0]
    assert set(exam["configSnapshot"]) == set(stored["configSnapshot"]) == {
        "maxProblemCount", "selectionPolicy", "generatedAt", "mode",
    }
    assert set(stored) == {
        "_id", "userId", "state", "configSnapshot", "items", "summary",
        "createdAt", "startedAt", "submittedAt", "updatedAt",
    }
    assert all(set(item["problemSnapshot"]) == {
        "text", "problemType", "subject", "graphDsl", "correctAnswer", "sourceImage",
    } for item in stored["items"])


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "status", "code"), [
    ("invalid", 404, "NOT_FOUND"), ("missing", 404, "NOT_FOUND"), ("foreign", 403, "FORBIDDEN"),
])
async def test_random_exam_rejects_invalid_folder_without_broadening(
    exams_app: FastAPI, client: AsyncClient, kind: str, status: int, code: str,
) -> None:
    folders, _ = _seed_random_filter_scope(exams_app)
    database: FakeDatabase = exams_app.state.fake_database
    bad_id = "invalid" if kind == "invalid" else str(ObjectId())
    if kind == "foreign":
        database["folders"].seed({"_id": ObjectId(bad_id), "userId": ObjectId(), "parentId": None})

    response = await client.post("/api/v1/exams", json={
        "maxProblemCount": 30,
        "folderIds": ["unfiled", str(folders["parent"]), bad_id],
    })

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert database["exams"]._documents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("include_positive", [True, False])
async def test_random_filters_preserve_eligibility_and_positive_weights(
    exams_app: FastAPI, client: AsyncClient, include_positive: bool,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    exams_app.dependency_overrides[get_app_settings] = lambda: Settings(
        problem_selection_min_age_days=3, problem_selection_cooldown_days=7,
    )
    now = datetime.now(UTC)
    problems = {}
    for name in ("positive", "disabled", "deleted", "blank-answer", "missing-answer", "no-normalized-answer", "cooldown", "too-new", "zero-weight", "outside"):
        problem = make_problem(user_id, text=name, problem_type="fill-in-the-blank", correct_answer="4")
        problem["createdAt"] = now - timedelta(days=30)
        problem["tags"] = ["match"] if name != "outside" else ["other"]
        problems[name] = problem
    problems["disabled"]["isDisabled"] = True
    problems["deleted"]["isDeleted"] = True
    problems["blank-answer"]["correctAnswer"]["display"] = " "
    problems["missing-answer"]["correctAnswer"] = None
    problems["no-normalized-answer"]["correctAnswer"]["normalizedText"] = ""
    problems["cooldown"]["tracking"]["lastTestedAt"] = now - timedelta(days=1)
    problems["too-new"]["createdAt"] = now
    problems["zero-weight"]["tracking"].update({
        "lastTestedAt": now - timedelta(days=10), "lastAttemptCorrect": True,
        "exposureCount": 100, "correctCount": 100,
    })
    if not include_positive:
        del problems["positive"]
    database["problems"].seed(*problems.values())

    response = await client.post("/api/v1/exams", json={
        "maxProblemCount": 30, "folderIds": ["unfiled"], "tags": ["match"],
    })

    if include_positive:
        assert response.status_code == 201
        assert [item["problemId"] for item in response.json()["exam"]["items"]] == [str(problems["positive"]["_id"])]
    else:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "NO_ELIGIBLE_PROBLEMS"
        assert database["exams"]._documents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["folderIds", "tags"])
@pytest.mark.parametrize("value", [None, "not-a-list", [123]])
async def test_random_filter_fields_require_string_lists(
    client: AsyncClient, field: str, value: Any,
) -> None:
    response = await client.post("/api/v1/exams", json={"maxProblemCount": 5, field: value})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


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
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
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

    # VLM-required exam returns promptly in the grading state.
    assert submit_response.status_code == 200
    grading_exam = submit_response.json()["exam"]
    assert grading_exam["state"] == "grading"
    # A durable task was created.
    assert len(database["exam_grading_tasks"]._documents) == 1
    assert database["exam_grading_tasks"]._documents[0]["status"] == "pending"

    # Run the worker to grade items and finalize.
    from app.infrastructure.worker.exam_grading_worker import process_exam_grading_task
    from app.infrastructure.config.settings import Settings as SettingsCls
    settings = SettingsCls()
    tasks_col = database["exam_grading_tasks"]
    task = deepcopy(tasks_col._documents[0])
    task["claimToken"] = "test-token"
    task["status"] = "processing"
    await tasks_col.update_one(
        {"_id": task["_id"]},
        {"$set": {"status": "processing", "claimToken": "test-token"}},
    )
    await process_exam_grading_task(task, database, vlm, storage, settings, tasks_col, adapter=FakeMongoAdapter())

    detail_response = await client.get(f"/api/v1/exams/{exam['id']}")
    submitted_exam = detail_response.json()["exam"]
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


@pytest.mark.asyncio
async def test_submit_exam_passes_snapshot_subject_to_vlm(exams_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_vlm
    user_id = exams_app.state.primary_user["_id"]

    english = make_problem(
        user_id,
        text="What is the capital of France?",
        problem_type="short-answer",
        correct_answer="Paris",
        subject="english",
    )
    database["problems"].seed(english)
    storage.seed(english["sourceImage"]["bucket"], english["sourceImage"]["objectKey"], b"image")
    vlm.responses = [FakeGradingResult(is_correct=True, feedback="good")]

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam = create_response.json()["exam"]
    item = exam["items"][0]

    await client.patch(f"/api/v1/exams/{exam['id']}/items/{item['itemId']}/answer", json={"answer": "Paris"})
    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")

    assert submit_response.status_code == 200
    assert submit_response.json()["exam"]["state"] == "grading"

    # Run the worker to perform VLM grading.
    from app.infrastructure.worker.exam_grading_worker import process_exam_grading_task
    from app.infrastructure.config.settings import Settings as SettingsCls
    settings = SettingsCls()
    tasks_col = database["exam_grading_tasks"]
    task = deepcopy(tasks_col._documents[0])
    task["claimToken"] = "test-token"
    task["status"] = "processing"
    await tasks_col.update_one(
        {"_id": task["_id"]},
        {"$set": {"status": "processing", "claimToken": "test-token"}},
    )
    await process_exam_grading_task(task, database, vlm, storage, settings, tasks_col, adapter=FakeMongoAdapter())

    assert vlm.calls == 1
    assert vlm.last_call_kwargs["subject"] == "english"


@pytest.mark.asyncio
async def test_submit_exam_rejects_late_answer_save_after_freeze(exams_app: FastAPI, client: AsyncClient) -> None:
    """A save-answer request that reads in-progress but loses the write race with
    submission is rejected after the exam is frozen to grading."""
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
    user_id = exams_app.state.primary_user["_id"]

    problem = make_problem(
        user_id,
        text="Explain photosynthesis",
        problem_type="short-answer",
        correct_answer="Plants convert light into chemical energy",
    )
    database["problems"].seed(problem)
    storage.seed(problem["sourceImage"]["bucket"], problem["sourceImage"]["objectKey"], b"image")

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam = create_response.json()["exam"]
    item_id = exam["items"][0]["itemId"]

    save_response = await client.patch(
        f"/api/v1/exams/{exam['id']}/items/{item_id}/answer",
        json={"answer": "initial answer"},
    )
    assert save_response.status_code == 200

    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert submit_response.status_code == 200
    assert submit_response.json()["exam"]["state"] == "grading"

    # A late answer save after the exam was frozen must be rejected.
    late_save = await client.patch(
        f"/api/v1/exams/{exam['id']}/items/{item_id}/answer",
        json={"answer": "modified after submission"},
    )
    assert late_save.status_code == 409
    assert late_save.json()["error"]["code"] == "INVALID_EXAM_STATE"


@pytest.mark.asyncio
async def test_submit_exam_marks_pending_review_after_vlm_retry_and_self_report_updates_tracking(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
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
    assert submit_response.json()["exam"]["state"] == "grading"

    # Run the worker to grade the item (VLM retry then pending-review).
    from app.infrastructure.worker.exam_grading_worker import process_exam_grading_task
    from app.infrastructure.config.settings import Settings as SettingsCls
    settings = SettingsCls()
    tasks_col = database["exam_grading_tasks"]
    task = deepcopy(tasks_col._documents[0])
    task["claimToken"] = "test-token"
    task["status"] = "processing"
    await tasks_col.update_one(
        {"_id": task["_id"]},
        {"$set": {"status": "processing", "claimToken": "test-token"}},
    )
    await process_exam_grading_task(task, database, vlm, storage, settings, tasks_col, adapter=FakeMongoAdapter())

    detail_response = await client.get(f"/api/v1/exams/{exam['id']}")
    submitted_exam = detail_response.json()["exam"]
    assert submitted_exam["state"] == "submitted"
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
    # pending-review items do not update tracking.
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


@pytest_asyncio.fixture
async def exams_app_with_min_age() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    adapter = FakeMongoAdapter()
    storage = FakeStorage()
    vlm = FakeVLMClient()
    user = make_user()

    settings = Settings(
        problem_selection_min_age_days=3,
        problem_selection_cooldown_days=7,
        problem_selection_last_wrong_weight=1.0,
        problem_selection_failure_rate_weight=1.0,
        problem_selection_recency_weight=1.0,
    )

    application.state.fake_database = database
    application.state.fake_adapter = adapter
    application.state.fake_storage = storage
    application.state.fake_grading_vlm = vlm
    application.state.fake_vlm = vlm
    application.state.primary_user = user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_current_user] = lambda: deepcopy(user)
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_mongo_adapter] = lambda: adapter
    application.dependency_overrides[get_s3_storage] = lambda: storage
    application.dependency_overrides[get_grading_vlm_client] = lambda: vlm
    return application


@pytest_asyncio.fixture
async def client_with_min_age(exams_app_with_min_age: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=exams_app_with_min_age)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_create_exam_rejects_when_all_too_new(
    exams_app_with_min_age: FastAPI,
    client_with_min_age: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app_with_min_age.state.fake_database
    user_id = exams_app_with_min_age.state.primary_user["_id"]
    new_problem = make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    database["problems"].seed(new_problem)

    response = await client_with_min_age.post("/api/v1/exams", json={"maxProblemCount": 1})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_ELIGIBLE_PROBLEMS"


@pytest.mark.asyncio
async def test_create_exam_succeeds_with_old_problems_and_records_min_age(
    exams_app_with_min_age: FastAPI,
    client_with_min_age: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app_with_min_age.state.fake_database
    storage: FakeStorage = exams_app_with_min_age.state.fake_storage
    user_id = exams_app_with_min_age.state.primary_user["_id"]
    old = datetime.now(UTC) - timedelta(days=10)
    old_problem = make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4", last_tested_at=old)
    old_problem["createdAt"] = old
    database["problems"].seed(old_problem)
    storage.seed(old_problem["sourceImage"]["bucket"], old_problem["sourceImage"]["objectKey"], b"img")

    response = await client_with_min_age.post("/api/v1/exams", json={"maxProblemCount": 1})

    assert response.status_code == 201
    body = response.json()["exam"]
    assert body["configSnapshot"]["selectionPolicy"] == {"cooldownDays": 7, "lastWrongWeight": 1.0, "failureRateWeight": 1.0, "recencyWeight": 1.0, "minProblemAgeDays": 3}
    assert len(body["items"]) == 1


@pytest_asyncio.fixture
async def exams_app_with_custom_policy() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    adapter = FakeMongoAdapter()
    storage = FakeStorage()
    vlm = FakeVLMClient()
    user = make_user()

    settings = Settings(
        problem_selection_cooldown_days=5,
        problem_selection_last_wrong_weight=1.5,
        problem_selection_failure_rate_weight=2.0,
        problem_selection_recency_weight=2.5,
        problem_selection_min_age_days=0,
    )

    application.state.fake_database = database
    application.state.fake_adapter = adapter
    application.state.fake_storage = storage
    application.state.fake_grading_vlm = vlm
    application.state.fake_vlm = vlm
    application.state.primary_user = user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_current_user] = lambda: deepcopy(user)
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_mongo_adapter] = lambda: adapter
    application.dependency_overrides[get_s3_storage] = lambda: storage
    application.dependency_overrides[get_grading_vlm_client] = lambda: vlm
    return application


@pytest_asyncio.fixture
async def client_custom_policy(exams_app_with_custom_policy: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=exams_app_with_custom_policy)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_create_exam_snapshots_all_non_default_policy_values(
    exams_app_with_custom_policy: FastAPI,
    client_custom_policy: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app_with_custom_policy.state.fake_database
    storage: FakeStorage = exams_app_with_custom_policy.state.fake_storage
    user_id = exams_app_with_custom_policy.state.primary_user["_id"]
    problem = make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    database["problems"].seed(problem)
    storage.seed(problem["sourceImage"]["bucket"], problem["sourceImage"]["objectKey"], b"img")

    response = await client_custom_policy.post("/api/v1/exams", json={"maxProblemCount": 1})

    assert response.status_code == 201
    body = response.json()["exam"]
    assert body["configSnapshot"]["selectionPolicy"] == {
        "cooldownDays": 5,
        "lastWrongWeight": 1.5,
        "failureRateWeight": 2.0,
        "recencyWeight": 2.5,
        "minProblemAgeDays": 0,
    }
    stored_exam = database["exams"]._documents[0]
    assert stored_exam["configSnapshot"]["selectionPolicy"] == {
        "cooldownDays": 5,
        "lastWrongWeight": 1.5,
        "failureRateWeight": 2.0,
        "recencyWeight": 2.5,
        "minProblemAgeDays": 0,
    }


@pytest.mark.asyncio
async def test_get_exam_deserializes_legacy_selection_policy(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    exam_id = ObjectId()
    database["exams"].seed(
        {
            "_id": exam_id,
            "userId": user_id,
            "state": "submitted",
            "configSnapshot": {
                "maxProblemCount": 1,
                "selectionPolicy": {"recencyWeight": 1.5, "failureWeight": 1.7},
                "generatedAt": datetime.now(UTC),
            },
            "items": [],
            "summary": {"totalProblems": 0, "answeredProblems": 0, "gradedProblems": 0, "pendingProblems": 0, "correctProblems": 0, "failedProblems": 0, "score": None},
            "createdAt": datetime.now(UTC),
            "startedAt": None,
            "submittedAt": None,
            "updatedAt": datetime.now(UTC),
        }
    )

    response = await client.get(f"/api/v1/exams/{exam_id}")

    assert response.status_code == 200
    body = response.json()["exam"]
    assert body["configSnapshot"]["selectionPolicy"] == {
        "cooldownDays": 7,
        "lastWrongWeight": 1.0,
        "failureRateWeight": 1.7,
        "recencyWeight": 1.5,
        "minProblemAgeDays": 3,
    }


@pytest.mark.asyncio
async def test_get_exam_prefers_failure_rate_weight_over_legacy_failure_weight(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    exam_id = ObjectId()
    database["exams"].seed(
        {
            "_id": exam_id,
            "userId": user_id,
            "state": "submitted",
            "configSnapshot": {
                "maxProblemCount": 1,
                "selectionPolicy": {"recencyWeight": 1.2, "failureWeight": 1.7, "failureRateWeight": 2.3},
                "generatedAt": datetime.now(UTC),
            },
            "items": [],
            "summary": {"totalProblems": 0, "answeredProblems": 0, "gradedProblems": 0, "pendingProblems": 0, "correctProblems": 0, "failedProblems": 0, "score": None},
            "createdAt": datetime.now(UTC),
            "startedAt": None,
            "submittedAt": None,
            "updatedAt": datetime.now(UTC),
        }
    )

    response = await client.get(f"/api/v1/exams/{exam_id}")

    assert response.status_code == 200
    body = response.json()["exam"]
    assert body["configSnapshot"]["selectionPolicy"]["failureRateWeight"] == 2.3


# ---------------------------------------------------------------------------
# Async exam grading tests (#490)
# ---------------------------------------------------------------------------

async def _run_grading_worker_once(
    database: FakeDatabase, vlm: FakeVLMClient, storage: FakeStorage, settings: Settings
) -> None:
    """Claim and process the single pending exam grading task in the fake database."""
    from app.infrastructure.worker.exam_grading_worker import process_exam_grading_task
    tasks_col = database["exam_grading_tasks"]
    task = deepcopy(tasks_col._documents[0])
    task["claimToken"] = "test-token"
    task["status"] = "processing"
    await tasks_col.update_one(
        {"_id": task["_id"]},
        {"$set": {"status": "processing", "claimToken": "test-token"}},
    )
    await process_exam_grading_task(task, database, vlm, storage, settings, tasks_col, adapter=FakeMongoAdapter())


@pytest.mark.asyncio
async def test_submit_vlm_exam_returns_grading_and_creates_single_task(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
    user_id = exams_app.state.primary_user["_id"]

    short = make_problem(user_id, text="Explain photosynthesis", problem_type="short-answer", correct_answer="Light energy")
    database["problems"].seed(short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"image")
    vlm.responses = [FakeGradingResult(is_correct=True, feedback="good")]

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam = create_response.json()["exam"]
    item_id = exam["items"][0]["itemId"]
    await client.patch(f"/api/v1/exams/{exam['id']}/items/{item_id}/answer", json={"answer": "my answer"})

    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert submit_response.status_code == 200
    grading_exam = submit_response.json()["exam"]
    assert grading_exam["state"] == "grading"
    # Exactly one task for this exam.
    assert len(database["exam_grading_tasks"]._documents) == 1
    assert str(database["exam_grading_tasks"]._documents[0]["examId"]) == exam["id"]
    assert database["exam_grading_tasks"]._documents[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_repeat_submit_for_grading_exam_is_idempotent(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
    user_id = exams_app.state.primary_user["_id"]

    short = make_problem(user_id, text="Explain X", problem_type="short-answer", correct_answer="X")
    database["problems"].seed(short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"image")
    vlm.responses = [FakeGradingResult(is_correct=True)]

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam = create_response.json()["exam"]
    item_id = exam["items"][0]["itemId"]
    await client.patch(f"/api/v1/exams/{exam['id']}/items/{item_id}/answer", json={"answer": "my answer"})

    first = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert first.status_code == 200
    assert first.json()["exam"]["state"] == "grading"
    tasks_before = len(database["exam_grading_tasks"]._documents)

    second = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert second.status_code == 200
    assert second.json()["exam"]["state"] == "grading"
    # No duplicate task created.
    assert len(database["exam_grading_tasks"]._documents) == tasks_before


@pytest.mark.asyncio
async def test_repeat_submit_for_already_submitted_exam_is_idempotent(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
    user_id = exams_app.state.primary_user["_id"]
    settings: Settings = exams_app.dependency_overrides[get_app_settings]()

    short = make_problem(user_id, text="Explain X", problem_type="short-answer", correct_answer="X")
    database["problems"].seed(short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"image")
    vlm.responses = [FakeGradingResult(is_correct=True)]

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam = create_response.json()["exam"]
    item_id = exam["items"][0]["itemId"]
    await client.patch(f"/api/v1/exams/{exam['id']}/items/{item_id}/answer", json={"answer": "my answer"})

    await client.post(f"/api/v1/exams/{exam['id']}/submit")
    await _run_grading_worker_once(database, vlm, storage, settings)

    # Second submit after completion returns the submitted exam without error.
    second = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert second.status_code == 200
    assert second.json()["exam"]["state"] == "submitted"


@pytest.mark.asyncio
async def test_submit_objective_only_exam_uses_synchronous_path(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    """Exams with no VLM-required items return submitted directly."""
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    objective = make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    database["problems"].seed(objective)

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam = create_response.json()["exam"]
    item_id = exam["items"][0]["itemId"]
    await client.patch(f"/api/v1/exams/{exam['id']}/items/{item_id}/answer", json={"answer": "4"})

    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert submit_response.status_code == 200
    submitted_exam = submit_response.json()["exam"]
    assert submitted_exam["state"] == "submitted"
    # No grading task created.
    assert len(database["exam_grading_tasks"]._documents) == 0
    assert submitted_exam["summary"]["correctProblems"] == 1


@pytest.mark.asyncio
async def test_submit_short_answer_null_answer_uses_synchronous_path(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    """A short-answer item whose stored answer.raw is null is graded incorrect locally."""
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    short = make_problem(user_id, text="Explain X", problem_type="short-answer", correct_answer="X")
    database["problems"].seed(short)

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam = create_response.json()["exam"]
    # Do not save an answer: answer.raw stays null.

    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert submit_response.status_code == 200
    submitted_exam = submit_response.json()["exam"]
    assert submitted_exam["state"] == "submitted"
    assert submitted_exam["items"][0]["grading"]["status"] == "incorrect"
    assert len(database["exam_grading_tasks"]._documents) == 0


@pytest.mark.asyncio
async def test_create_exam_blocked_by_grading_exam(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    database["exams"].seed(
        {
            "_id": ObjectId(),
            "userId": user_id,
            "state": "grading",
            "configSnapshot": {"maxProblemCount": 1, "selectionPolicy": {"cooldownDays": 7, "lastWrongWeight": 1.0, "failureRateWeight": 1.0, "recencyWeight": 1.0, "minProblemAgeDays": 0}, "generatedAt": datetime.now(UTC)},
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
async def test_get_active_returns_grading_exam(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    exam_id = ObjectId()
    database["exams"].seed(
        {
            "_id": exam_id,
            "userId": user_id,
            "state": "grading",
            "configSnapshot": {"maxProblemCount": 1, "selectionPolicy": {"cooldownDays": 7, "lastWrongWeight": 1.0, "failureRateWeight": 1.0, "recencyWeight": 1.0, "minProblemAgeDays": 0}, "generatedAt": datetime.now(UTC)},
            "items": [],
            "summary": {"totalProblems": 0, "answeredProblems": 0, "gradedProblems": 0, "pendingProblems": 0, "correctProblems": 0, "failedProblems": 0, "score": None},
            "createdAt": datetime.now(UTC),
            "startedAt": datetime.now(UTC),
            "submittedAt": None,
            "updatedAt": datetime.now(UTC),
        }
    )

    response = await client.get("/api/v1/exams/active")
    assert response.status_code == 200
    assert response.json()["exam"]["state"] == "grading"
    assert response.json()["exam"]["id"] == str(exam_id)


@pytest.mark.asyncio
async def test_history_includes_grading_exam(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    exam_id = ObjectId()
    database["exams"].seed(
        {
            "_id": exam_id,
            "userId": user_id,
            "state": "grading",
            "configSnapshot": {"maxProblemCount": 1, "selectionPolicy": {"cooldownDays": 7, "lastWrongWeight": 1.0, "failureRateWeight": 1.0, "recencyWeight": 1.0, "minProblemAgeDays": 0}, "generatedAt": datetime.now(UTC)},
            "items": [],
            "summary": {"totalProblems": 0, "answeredProblems": 0, "gradedProblems": 0, "pendingProblems": 0, "correctProblems": 0, "failedProblems": 0, "score": None},
            "createdAt": datetime.now(UTC),
            "startedAt": datetime.now(UTC),
            "submittedAt": None,
            "updatedAt": datetime.now(UTC),
        }
    )

    response = await client.get("/api/v1/exams")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["state"] == "grading"


@pytest.mark.asyncio
async def test_grading_state_serialization_redacts_answer_bearing_fields(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    storage: FakeStorage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
    user_id = exams_app.state.primary_user["_id"]
    settings: Settings = exams_app.dependency_overrides[get_app_settings]()

    short = make_problem(user_id, text="Explain X", problem_type="short-answer", correct_answer="X")
    database["problems"].seed(short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"image")
    vlm.responses = [FakeGradingResult(is_correct=True, feedback="secret feedback")]

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam = create_response.json()["exam"]
    item_id = exam["items"][0]["itemId"]
    await client.patch(f"/api/v1/exams/{exam['id']}/items/{item_id}/answer", json={"answer": "my answer"})
    await client.post(f"/api/v1/exams/{exam['id']}/submit")

    detail = await client.get(f"/api/v1/exams/{exam['id']}")
    grading_exam = detail.json()["exam"]
    assert grading_exam["state"] == "grading"
    item = grading_exam["items"][0]
    # Correct answer hidden.
    assert item["problem"]["correctAnswer"] is None
    # Answer-bearing grading details redacted.
    assert item["grading"]["feedback"] is None
    assert item["grading"]["rawProviderResponse"] is None
    assert item["grading"]["method"] is None
    assert item["grading"]["isCorrect"] is None
    # Status remains visible for progress UI.
    assert item["grading"]["status"] == "ungraded"

    # Complete grading, then verify full details are exposed after submission.
    await _run_grading_worker_once(database, vlm, storage, settings)
    submitted_detail = await client.get(f"/api/v1/exams/{exam['id']}")
    submitted_exam = submitted_detail.json()["exam"]
    assert submitted_exam["state"] == "submitted"
    submitted_item = submitted_exam["items"][0]
    assert submitted_item["problem"]["correctAnswer"]["display"] == "X"
    assert submitted_item["grading"]["feedback"] == "secret feedback"
    assert submitted_item["grading"]["method"] == "vlm"


@pytest.mark.asyncio
async def test_submit_sync_path_rejects_when_answers_modified_during_grading(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    """The synchronous no-VLM path still rejects if answers change between the
    pre-submit read and the transactional finalization."""
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    objective = make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    database["problems"].seed(objective)

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    exam = create_response.json()["exam"]
    item_id = exam["items"][0]["itemId"]

    await client.patch(f"/api/v1/exams/{exam['id']}/items/{item_id}/answer", json={"answer": "4"})

    # Mutate the stored answer between the pre-submit read and the transaction.
    import app.presentation.exams as exams_mod
    original_grade = exams_mod.grade_item

    async def _mutate_then_grade(item, *, vlm_client, storage, now):
        stored = await database["exams"].find_one({"_id": ObjectId(exam["id"])})
        stored["items"][0]["answer"] = {"raw": "changed", "savedAt": datetime.now(UTC)}
        await database["exams"].update_one(
            {"_id": stored["_id"]},
            {"$set": {"items": stored["items"], "updatedAt": datetime.now(UTC)}},
        )
        return await original_grade(item, vlm_client=vlm_client, storage=storage, now=now)

    exams_mod.grade_item = _mutate_then_grade  # type: ignore[assignment]
    try:
        submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    finally:
        exams_mod.grade_item = original_grade  # type: ignore[assignment]

    assert submit_response.status_code == 409
    assert submit_response.json()["error"]["code"] == "ANSWERS_MODIFIED_DURING_GRADING"


# ---------------------------------------------------------------------------
# Cross-user ownership protection characterization (#558)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def foreign_exams_app() -> FastAPI:
    """An app where the authenticated user differs from the exam owner.

    The owner has one problem and one in-progress exam seeded in the database.
    The authenticated user is a second, foreign user.
    """
    application = create_app()
    database = FakeDatabase()
    adapter = FakeMongoAdapter()
    storage = FakeStorage()
    vlm = FakeVLMClient()
    owner = make_user("owner")
    foreign_user = make_user("intruder")

    owner_problem = make_problem(owner["_id"], text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    database["problems"].seed(owner_problem)

    now = datetime.now(UTC)
    owner_exam_id = ObjectId()
    item_id = str(ObjectId())
    database["exams"].seed(
        {
            "_id": owner_exam_id,
            "userId": owner["_id"],
            "state": "in-progress",
            "configSnapshot": {
                "maxProblemCount": 1,
                "selectionPolicy": {
                    "cooldownDays": 7,
                    "lastWrongWeight": 1.0,
                    "failureRateWeight": 1.0,
                    "recencyWeight": 1.0,
                    "minProblemAgeDays": 0,
                },
                "generatedAt": now,
            },
            "items": [
                {
                    "itemId": item_id,
                    "order": 1,
                    "problemId": owner_problem["_id"],
                    "problemSnapshot": {
                        "text": owner_problem["text"],
                        "problemType": owner_problem["problemType"],
                        "subject": owner_problem.get("subject", "math"),
                        "graphDsl": None,
                        "correctAnswer": deepcopy(owner_problem["correctAnswer"]),
                        "sourceImage": deepcopy(owner_problem.get("sourceImage")),
                    },
                    "answer": {"raw": None, "savedAt": None},
                    "grading": {
                        "status": "ungraded",
                        "method": None,
                        "isCorrect": None,
                        "score": None,
                        "feedback": None,
                        "providerModel": None,
                        "rawProviderResponse": None,
                        "gradedAt": None,
                        "retryCount": 0,
                        "selfReportedCorrect": None,
                    },
                }
            ],
            "summary": {
                "totalProblems": 1,
                "answeredProblems": 0,
                "gradedProblems": 0,
                "pendingProblems": 0,
                "correctProblems": 0,
                "failedProblems": 0,
                "score": None,
            },
            "createdAt": now,
            "startedAt": now,
            "submittedAt": None,
            "updatedAt": now,
        }
    )

    settings = Settings(
        problem_selection_min_age_days=0,
        problem_selection_cooldown_days=7,
        problem_selection_last_wrong_weight=1.0,
        problem_selection_failure_rate_weight=1.0,
        problem_selection_recency_weight=1.0,
    )

    application.state.fake_database = database
    application.state.fake_adapter = adapter
    application.state.fake_storage = storage
    application.state.fake_grading_vlm = vlm
    application.state.fake_vlm = vlm
    application.state.owner = owner
    application.state.foreign_user = foreign_user
    application.state.owner_exam_id = owner_exam_id
    application.state.owner_item_id = item_id

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_current_user] = lambda: deepcopy(foreign_user)
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_mongo_adapter] = lambda: adapter
    application.dependency_overrides[get_s3_storage] = lambda: storage
    application.dependency_overrides[get_grading_vlm_client] = lambda: vlm
    return application


@pytest_asyncio.fixture
async def foreign_client(foreign_exams_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=foreign_exams_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_foreign_active_exam_returns_404(
    foreign_exams_app: FastAPI,
    foreign_client: AsyncClient,
) -> None:
    response = await foreign_client.get("/api/v1/exams/active")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_foreign_exam_history_is_empty(
    foreign_exams_app: FastAPI,
    foreign_client: AsyncClient,
) -> None:
    response = await foreign_client.get("/api/v1/exams")
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []


@pytest.mark.asyncio
async def test_foreign_exam_get_returns_403(
    foreign_exams_app: FastAPI,
    foreign_client: AsyncClient,
) -> None:
    exam_id = foreign_exams_app.state.owner_exam_id

    response = await foreign_client.get(f"/api/v1/exams/{exam_id}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_foreign_exam_answer_patch_returns_403(
    foreign_exams_app: FastAPI,
    foreign_client: AsyncClient,
) -> None:
    exam_id = foreign_exams_app.state.owner_exam_id
    item_id = foreign_exams_app.state.owner_item_id

    response = await foreign_client.patch(
        f"/api/v1/exams/{exam_id}/items/{item_id}/answer",
        json={"answer": "intruder guess"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_foreign_exam_submit_returns_403(
    foreign_exams_app: FastAPI,
    foreign_client: AsyncClient,
) -> None:
    exam_id = foreign_exams_app.state.owner_exam_id

    response = await foreign_client.post(f"/api/v1/exams/{exam_id}/submit")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_foreign_exam_discard_returns_403(
    foreign_exams_app: FastAPI,
    foreign_client: AsyncClient,
) -> None:
    exam_id = foreign_exams_app.state.owner_exam_id

    response = await foreign_client.post(f"/api/v1/exams/{exam_id}/discard")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_foreign_exam_self_report_returns_403(
    foreign_exams_app: FastAPI,
    foreign_client: AsyncClient,
) -> None:
    exam_id = foreign_exams_app.state.owner_exam_id
    item_id = foreign_exams_app.state.owner_item_id

    response = await foreign_client.post(
        f"/api/v1/exams/{exam_id}/items/{item_id}/self-report",
        json={"isCorrect": False},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_foreign_exam_operations_leave_owner_state_unchanged(
    foreign_exams_app: FastAPI,
    foreign_client: AsyncClient,
) -> None:
    """All five foreign exam ID operations must not mutate the owner's exam."""
    database: FakeDatabase = foreign_exams_app.state.fake_database
    exam_id = foreign_exams_app.state.owner_exam_id
    item_id = foreign_exams_app.state.owner_item_id

    snapshot = deepcopy(await database["exams"].find_one({"_id": exam_id}))

    # Attempt all five foreign operations.
    await foreign_client.get(f"/api/v1/exams/{exam_id}")
    await foreign_client.patch(f"/api/v1/exams/{exam_id}/items/{item_id}/answer", json={"answer": "x"})
    await foreign_client.post(f"/api/v1/exams/{exam_id}/submit")
    await foreign_client.post(f"/api/v1/exams/{exam_id}/discard")
    await foreign_client.post(f"/api/v1/exams/{exam_id}/items/{item_id}/self-report", json={"isCorrect": False})

    after = await database["exams"].find_one({"_id": exam_id})
    assert after == snapshot


# ---------------------------------------------------------------------------
# Manual exam creation + selection candidates (#680)
# ---------------------------------------------------------------------------


def _seed_picker_problems(exams_app: FastAPI) -> dict[str, dict[str, Any]]:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    now = datetime.now(UTC)
    problems = {
        "alpha": make_problem(
            user_id, text="What is 2+2?", problem_type="fill-in-the-blank",
            correct_answer="4",
            tracking={"exposureCount": 2, "correctCount": 2, "failedCount": 0,
                      "lastTestedAt": now - timedelta(days=30), "lastAttemptCorrect": True},
        ),
        "beta": make_problem(
            user_id, text="Capital of France?", problem_type="short-answer",
            correct_answer="Paris",
            tracking={"exposureCount": 5, "correctCount": 2, "failedCount": 3,
                      "lastTestedAt": now - timedelta(days=30), "lastAttemptCorrect": False},
        ),
        "gamma": make_problem(
            user_id, text="Name a prime number", problem_type="short-answer",
            correct_answer="7",
        ),
    }
    database["problems"].seed(*problems.values())
    return problems


@pytest.mark.asyncio
async def test_manual_create_exam_items_follow_problem_ids_order(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    problems = _seed_picker_problems(exams_app)
    ordered_ids = [str(problems["gamma"]["_id"]), str(problems["alpha"]["_id"])]

    response = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": ordered_ids,
              "folderIds": ["invalid-folder"], "tags": ["nonexistent"]},
    )

    assert response.status_code == 201
    exam = response.json()["exam"]
    assert [item["problemId"] for item in exam["items"]] == ordered_ids
    assert [item["order"] for item in exam["items"]] == [1, 2]
    snapshot = exam["configSnapshot"]
    assert snapshot["mode"] == "manual"
    assert snapshot["maxProblemCount"] == 2
    assert snapshot["selectionPolicy"] == {
        "cooldownDays": 7, "lastWrongWeight": 1.0, "failureRateWeight": 1.0,
        "recencyWeight": 1.0, "minProblemAgeDays": 0,
    }
    assert snapshot["generatedAt"] is not None


@pytest.mark.asyncio
async def test_manual_create_accepts_uppercase_and_mixed_case_ids_in_order(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    """ObjectId spellings are case-insensitive hex: uppercase/mixed-case
    spellings of owned eligible ids must create the exam in submitted order,
    not be reported as missing identities (#680 review B1)."""
    problems = _seed_picker_problems(exams_app)
    gamma_upper = str(problems["gamma"]["_id"]).upper()
    alpha_canonical = str(problems["alpha"]["_id"])
    alpha_mixed = "".join(
        char.upper() if index % 2 else char.lower()
        for index, char in enumerate(alpha_canonical)
    )

    response = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [gamma_upper, alpha_mixed]},
    )

    assert response.status_code == 201
    exam = response.json()["exam"]
    assert [item["problemId"] for item in exam["items"]] == [
        str(problems["gamma"]["_id"]),
        alpha_canonical,
    ]
    assert exam["configSnapshot"]["mode"] == "manual"


@pytest.mark.asyncio
async def test_manual_create_rejects_mixed_case_aliases_as_duplicates(
    client: AsyncClient,
) -> None:
    """Upper/lower spellings of one identity are the same id: mixed-case
    aliases must fail as duplicates before any insert (#680 review B1)."""
    canonical = str(ObjectId())
    alias_upper = canonical.upper()

    response = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [canonical, alias_upper]},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_SELECTION"
    assert error["details"]["problemIds"] == [canonical]


@pytest.mark.asyncio
async def test_manual_create_exam_records_same_policy_and_generated_at_as_random(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    problems = _seed_picker_problems(exams_app)

    manual = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [str(problems["alpha"]["_id"])]},
    )
    assert manual.status_code == 201
    # Discard so the random-mode create passes the active-exam guard.
    exam_id = manual.json()["exam"]["id"]
    await client.post(f"/api/v1/exams/{exam_id}/discard")

    random_create = await client.post("/api/v1/exams", json={"maxProblemCount": 1})

    assert random_create.status_code == 201
    manual_snapshot = manual.json()["exam"]["configSnapshot"]
    random_snapshot = random_create.json()["exam"]["configSnapshot"]
    assert manual_snapshot["selectionPolicy"] == random_snapshot["selectionPolicy"]
    assert set(manual_snapshot.keys()) == set(random_snapshot.keys())
    assert manual_snapshot["mode"] == "manual"
    assert random_snapshot["mode"] == "random"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "manual"},
        {"mode": "manual", "problemIds": []},
        {"mode": "manual", "problemIds": [str(ObjectId()) for _ in range(31)]},
    ],
)
async def test_manual_create_shape_failures_return_invalid_selection(
    client: AsyncClient, payload: dict[str, Any]
) -> None:
    response = await client.post("/api/v1/exams", json=payload)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_SELECTION"
    assert "problemIds" in error["details"]


@pytest.mark.asyncio
async def test_manual_create_duplicate_ids_return_invalid_selection(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    problems = _seed_picker_problems(exams_app)
    pid = str(problems["alpha"]["_id"])

    response = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [pid, pid]},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_SELECTION"
    assert error["details"]["problemIds"] == [pid]


@pytest.mark.asyncio
async def test_manual_create_invalid_and_foreign_ids_share_one_shape(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    invalid = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": ["not-an-objectid"]},
    )
    foreign_user = make_user(username="someoneelse")
    foreign = make_problem(foreign_user["_id"], text="foreign", problem_type="fill-in-the-blank", correct_answer="x")
    exams_app.state.fake_database["problems"].seed(foreign)
    foreign_response = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [str(foreign["_id"])]},
    )

    assert invalid.status_code == foreign_response.status_code == 422
    invalid_error = invalid.json()["error"]
    foreign_error = foreign_response.json()["error"]
    assert invalid_error["code"] == foreign_error["code"] == "INVALID_SELECTION"
    assert invalid_error["details"]["problemIds"] == ["not-an-objectid"]
    assert foreign_error["details"]["problemIds"] == [str(foreign["_id"])]


@pytest.mark.asyncio
async def test_manual_create_unknown_id_returns_invalid_selection(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    _seed_picker_problems(exams_app)
    missing_id = str(ObjectId())

    response = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [missing_id]},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_SELECTION"
    assert error["details"]["problemIds"] == [missing_id]


@pytest.mark.asyncio
async def test_manual_create_rejects_cooldown_problem_as_ineligible(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    now = datetime.now(UTC)
    cooling = make_problem(
        user_id, text="Just tested", problem_type="fill-in-the-blank", correct_answer="4",
        tracking={"exposureCount": 1, "correctCount": 0, "failedCount": 1,
                  "lastTestedAt": now - timedelta(days=1), "lastAttemptCorrect": False},
    )
    database["problems"].seed(cooling)

    response = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [str(cooling["_id"])]},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INELIGIBLE_PROBLEMS"
    assert error["details"]["problemIds"] == [str(cooling["_id"])]


@pytest.mark.asyncio
async def test_manual_create_rejects_min_age_problem_as_ineligible(
    exams_app_with_min_age: FastAPI, client_with_min_age: AsyncClient
) -> None:
    database: FakeDatabase = exams_app_with_min_age.state.fake_database
    user_id = exams_app_with_min_age.state.primary_user["_id"]
    too_new = make_problem(user_id, text="Fresh", problem_type="fill-in-the-blank", correct_answer="4")
    database["problems"].seed(too_new)

    response = await client_with_min_age.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [str(too_new["_id"])]},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INELIGIBLE_PROBLEMS"
    assert error["details"]["problemIds"] == [str(too_new["_id"])]


@pytest.mark.asyncio
async def test_manual_create_still_blocked_by_active_exam(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    problems = _seed_picker_problems(exams_app)

    first = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [str(problems["alpha"]["_id"])]},
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [str(problems["beta"]["_id"])]},
    )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ACTIVE_EXAM_EXISTS"


@pytest.mark.asyncio
async def test_random_mode_missing_max_problem_count_returns_validation_error(
    client: AsyncClient,
) -> None:
    response = await client.post("/api/v1/exams", json={"mode": "random"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_legacy_exam_snapshot_without_mode_serializes_as_random(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    problems = _seed_picker_problems(exams_app)
    created = await client.post(
        "/api/v1/exams",
        json={"mode": "manual", "problemIds": [str(problems["alpha"]["_id"])]},
    )
    exam_id = created.json()["exam"]["id"]
    database: FakeDatabase = exams_app.state.fake_database
    stored = database["exams"]._documents[0]
    del stored["configSnapshot"]["mode"]

    response = await client.get(f"/api/v1/exams/{exam_id}")

    assert response.status_code == 200
    assert response.json()["exam"]["configSnapshot"]["mode"] == "random"


@pytest.mark.asyncio
async def test_selection_candidates_exclude_ineligible_problems(
    exams_app_with_min_age: FastAPI, client_with_min_age: AsyncClient
) -> None:
    database: FakeDatabase = exams_app_with_min_age.state.fake_database
    user_id = exams_app_with_min_age.state.primary_user["_id"]
    now = datetime.now(UTC)
    eligible = make_problem(user_id, text="Eligible?", problem_type="fill-in-the-blank", correct_answer="4")
    eligible["createdAt"] = now - timedelta(days=30)
    eligible["updatedAt"] = now - timedelta(days=30)
    cooling = make_problem(
        user_id, text="Cooling down", problem_type="fill-in-the-blank", correct_answer="5",
        tracking={"exposureCount": 1, "correctCount": 0, "failedCount": 1,
                  "lastTestedAt": now - timedelta(days=1), "lastAttemptCorrect": False},
    )
    cooling["createdAt"] = now - timedelta(days=30)
    cooling["updatedAt"] = now - timedelta(days=30)
    fresh = make_problem(user_id, text="Too new", problem_type="fill-in-the-blank", correct_answer="6")
    fresh["createdAt"] = now - timedelta(hours=1)
    fresh["updatedAt"] = now - timedelta(hours=1)
    # Old enough and outside cooldown so each record's own property — not
    # freshness — is the reason it is excluded (#680 review B2).
    stale_created = now - timedelta(days=30)
    deleted = make_problem(user_id, text="Deleted", problem_type="fill-in-the-blank", correct_answer="7", is_deleted=True)
    deleted["createdAt"] = stale_created
    deleted["updatedAt"] = stale_created
    disabled = make_problem(user_id, text="Disabled", problem_type="fill-in-the-blank", correct_answer="8", is_disabled=True)
    disabled["createdAt"] = stale_created
    disabled["updatedAt"] = stale_created
    answerless = make_problem(user_id, text="No answer", problem_type="fill-in-the-blank", correct_answer="")
    answerless["createdAt"] = stale_created
    answerless["updatedAt"] = stale_created
    database["problems"].seed(eligible, cooling, fresh, deleted, disabled, answerless)

    response = await client_with_min_age.get("/api/v1/exams/selection-candidates")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [str(eligible["_id"])]


@pytest.mark.asyncio
async def test_selection_candidates_keyword_sort_and_pagination(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    now = datetime.now(UTC)
    algebra_one = make_problem(
        user_id, text="algebra one", problem_type="fill-in-the-blank", correct_answer="1",
        tracking={"exposureCount": 3, "correctCount": 3, "failedCount": 0,
                  "lastTestedAt": now - timedelta(days=40), "lastAttemptCorrect": True},
    )
    algebra_two = make_problem(
        user_id, text="algebra two", problem_type="fill-in-the-blank", correct_answer="2",
        tracking={"exposureCount": 6, "correctCount": 3, "failedCount": 3,
                  "lastTestedAt": now - timedelta(days=40), "lastAttemptCorrect": False},
    )
    geography = make_problem(
        user_id, text="Capital of France?", problem_type="short-answer", correct_answer="Paris",
    )
    geography["tags"] = ["algebra", "geography"]
    database["problems"].seed(algebra_one, algebra_two, geography)

    by_tag = await client.get("/api/v1/exams/selection-candidates", params={"q": "ALGEBRA"})
    assert by_tag.status_code == 200
    assert by_tag.json()["total"] == 3

    by_text = await client.get("/api/v1/exams/selection-candidates", params={"q": "capital"})
    assert [item["id"] for item in by_text.json()["items"]] == [str(geography["_id"])]

    failures_desc = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sortBy": "failureCount", "sortOrder": "desc"},
    )
    items = failures_desc.json()["items"]
    assert [item["failedCount"] for item in items] == sorted(
        [item["failedCount"] for item in items], reverse=True
    )
    assert items[0]["id"] == str(algebra_two["_id"])

    failures_asc = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sortBy": "failureCount", "sortOrder": "asc"},
    )
    assert failures_asc.json()["items"][-1]["id"] == str(algebra_two["_id"])

    # Legacy #680 contract kept (#708 B1): cumulative-success sorting still
    # works via sortBy even though the picker UI no longer offers the column.
    success_desc = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sortBy": "successCount", "sortOrder": "desc"},
    )
    assert success_desc.status_code == 200
    assert [item["id"] for item in success_desc.json()["items"]] == [
        str(algebra_one["_id"]),
        str(algebra_two["_id"]),
        str(geography["_id"]),
    ]
    success_asc = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sortBy": "successCount", "sortOrder": "asc"},
    )
    assert success_asc.status_code == 200
    assert [item["id"] for item in success_asc.json()["items"]] == [
        str(geography["_id"]),
        str(algebra_one["_id"]),
        str(algebra_two["_id"]),
    ]
    # The ordered wire format accepts the same legacy field with cumulative
    # semantics (NOT streak values).
    success_wire = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sort": "successCount:desc"},
    )
    assert success_wire.status_code == 200
    assert [item["successCount"] for item in success_wire.json()["items"]] == [3, 3, 0]

    add_date_asc = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sortBy": "addDate", "sortOrder": "asc"},
    )
    assert [item["id"] for item in add_date_asc.json()["items"]] == [
        str(algebra_one["_id"]),
        str(algebra_two["_id"]),
        str(geography["_id"]),
    ]

    score_desc = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sortBy": "selectionScore", "sortOrder": "desc"},
    )
    scores_desc = [item["selectionScore"] for item in score_desc.json()["items"]]
    assert scores_desc == sorted(scores_desc, reverse=True)
    score_asc = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sortBy": "selectionScore", "sortOrder": "asc"},
    )
    scores_asc = [item["selectionScore"] for item in score_asc.json()["items"]]
    assert scores_asc == sorted(scores_asc)
    assert scores_desc[0] == scores_asc[-1]

    add_date_desc = await client.get(
        "/api/v1/exams/selection-candidates", params={"sortBy": "addDate"}
    )
    assert add_date_desc.status_code == 200
    assert [item["id"] for item in add_date_desc.json()["items"]] == [
        str(geography["_id"]),
        str(algebra_two["_id"]),
        str(algebra_one["_id"]),
    ]

    paged = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"page": 2, "pageSize": 2, "sortBy": "addDate"},
    )
    paged_body = paged.json()
    assert paged_body["page"] == 2
    assert paged_body["pageSize"] == 2
    assert paged_body["total"] == 3
    assert [item["id"] for item in paged_body["items"]] == [str(algebra_one["_id"])]


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"sortBy": "bogus"}, {"sortOrder": "sideways"}])
async def test_selection_candidates_reject_bogus_sort_params(
    client: AsyncClient, params: dict[str, str]
) -> None:
    response = await client.get("/api/v1/exams/selection-candidates", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def _make_submitted_exam(
    user_id: ObjectId,
    *,
    submitted_at: datetime,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Submitted-exam fixture trimmed to what streak loading consumes (#708)."""
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "state": "submitted",
        "submittedAt": submitted_at,
        "items": items,
    }


def _make_exam_item(problem_oid: ObjectId, status: str) -> dict[str, Any]:
    return {
        "itemId": str(ObjectId()),
        "problemId": problem_oid,
        "grading": {"status": status},
    }


def _make_practice_attempt(
    user_id: ObjectId,
    problem_oid: ObjectId,
    *,
    created_at: datetime,
    grading_status: str,
) -> dict[str, Any]:
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "problemId": problem_oid,
        "createdAt": created_at,
        "gradingStatus": grading_status,
    }


def _candidate_streaks_by_id(body: dict[str, Any]) -> dict[str, Any]:
    return {item["id"]: item["correctStreak"] for item in body["items"]}


@pytest.mark.asyncio
async def test_selection_candidates_streak_sources_and_missing_history(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    other_user = ObjectId()
    now = datetime.now(UTC)

    mixed = make_problem(user_id, text="mixed", problem_type="fill-in-the-blank", correct_answer="1")
    never = make_problem(user_id, text="never", problem_type="fill-in-the-blank", correct_answer="2")
    pending_only = make_problem(user_id, text="pending only", problem_type="fill-in-the-blank", correct_answer="3")
    aggregate_only = make_problem(
        user_id, text="aggregate only", problem_type="fill-in-the-blank", correct_answer="4",
        tracking={"exposureCount": 4, "correctCount": 3, "failedCount": 1,
                  "lastTestedAt": now - timedelta(days=40), "lastAttemptCorrect": True},
    )
    partial = make_problem(
        user_id, text="partial", problem_type="fill-in-the-blank", correct_answer="5",
        tracking={"exposureCount": 9, "correctCount": 7, "failedCount": 2,
                  "lastTestedAt": now - timedelta(days=40), "lastAttemptCorrect": True},
    )
    foreign = make_problem(user_id, text="foreign", problem_type="fill-in-the-blank", correct_answer="6")
    database["problems"].seed(mixed, never, pending_only, aggregate_only, partial, foreign)

    # mixed: incorrect practice (day 5), correct practice (day 3), correct exam
    # item (day 2, submitted) -> trailing streak 2.
    database["practice_attempts"].seed(
        _make_practice_attempt(user_id, mixed["_id"], created_at=now - timedelta(days=5), grading_status="incorrect"),
        _make_practice_attempt(user_id, mixed["_id"], created_at=now - timedelta(days=3), grading_status="correct"),
    )
    database["exams"].seed(
        _make_submitted_exam(
            user_id,
            submitted_at=now - timedelta(days=2),
            items=[_make_exam_item(mixed["_id"], "correct")],
        ),
        # pending_only: submitted exam whose item still awaits review -> 0.
        _make_submitted_exam(
            user_id,
            submitted_at=now - timedelta(days=2),
            items=[_make_exam_item(pending_only["_id"], "pending-review")],
        ),
        # partial: one surviving correct record despite aggregate claiming 9.
        _make_submitted_exam(
            user_id,
            submitted_at=now - timedelta(days=2),
            items=[_make_exam_item(partial["_id"], "correct")],
        ),
        # Another user's submitted exam must not contribute.
        _make_submitted_exam(
            other_user,
            submitted_at=now - timedelta(days=1),
            items=[_make_exam_item(foreign["_id"], "correct")],
        ),
        # In-progress (non-submitted) exams are not attempts.
        {
            **_make_submitted_exam(
                user_id,
                submitted_at=now,
                items=[_make_exam_item(foreign["_id"], "correct")],
            ),
            "_id": ObjectId(),
            "state": "in-progress",
            "submittedAt": None,
        },
    )

    response = await client.get("/api/v1/exams/selection-candidates")

    assert response.status_code == 200
    streaks = _candidate_streaks_by_id(response.json())
    assert streaks[str(mixed["_id"])] == 2
    assert streaks[str(never["_id"])] == 0
    assert streaks[str(pending_only["_id"])] == 0
    # Legacy row: aggregate counters claim attempts, no saved history -> null.
    assert streaks[str(aggregate_only["_id"])] is None
    # Partial history: counts the surviving record instead of inventing events.
    assert streaks[str(partial["_id"])] == 1
    # Another user's history is invisible; foreign stays at 0 (never attempted).
    assert streaks[str(foreign["_id"])] == 0


@pytest.mark.asyncio
async def test_selection_candidates_ordered_sort_criteria_and_validation(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    now = datetime.now(UTC)
    # Fixed hex ids: tied rows need a deterministic id tiebreak order.
    id_old = ObjectId("000000000000000000000001")
    id_new = ObjectId("000000000000000000000002")
    id_high = ObjectId("000000000000000000000003")
    id_missing = ObjectId("000000000000000000000004")

    low_streak_old = make_problem(
        user_id, text="low streak old", problem_type="fill-in-the-blank", correct_answer="1",
        tracking={"exposureCount": 1, "correctCount": 0, "failedCount": 1,
                  "lastTestedAt": now - timedelta(days=30), "lastAttemptCorrect": False},
    )
    low_streak_old["_id"] = id_old
    low_streak_old["createdAt"] = now - timedelta(days=10)
    low_streak_new = make_problem(
        user_id, text="low streak new", problem_type="fill-in-the-blank", correct_answer="2",
        tracking={"exposureCount": 1, "correctCount": 0, "failedCount": 1,
                  "lastTestedAt": now - timedelta(days=30), "lastAttemptCorrect": False},
    )
    low_streak_new["_id"] = id_new
    low_streak_new["createdAt"] = now - timedelta(days=1)
    missing_history = make_problem(
        user_id, text="missing history", problem_type="fill-in-the-blank", correct_answer="3",
        tracking={"exposureCount": 2, "correctCount": 2, "failedCount": 0,
                  "lastTestedAt": now - timedelta(days=30), "lastAttemptCorrect": True},
    )
    missing_history["_id"] = id_missing
    high_streak = make_problem(
        user_id, text="high streak", problem_type="fill-in-the-blank", correct_answer="4",
        tracking={"exposureCount": 1, "correctCount": 0, "failedCount": 1,
                  "lastTestedAt": now - timedelta(days=30), "lastAttemptCorrect": False},
    )
    high_streak["_id"] = id_high
    database["problems"].seed(low_streak_old, low_streak_new, missing_history, high_streak)
    database["practice_attempts"].seed(
        _make_practice_attempt(user_id, id_old, created_at=now - timedelta(days=5), grading_status="incorrect"),
        _make_practice_attempt(user_id, id_new, created_at=now - timedelta(days=5), grading_status="incorrect"),
        _make_practice_attempt(user_id, id_high, created_at=now - timedelta(days=5), grading_status="correct"),
    )

    # Streak ascending first: the two 0-streaks by Added ascending (old before
    # new), then the 1; missing history (null) last.
    ordered = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sort": "correctStreak:asc,addDate:asc"},
    )
    assert [item["id"] for item in ordered.json()["items"]] == [
        str(id_old),
        str(id_new),
        str(id_high),
        str(id_missing),
    ]

    # Descending streak flips known groups but nulls stay last.
    ordered_desc = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sort": "correctStreak:desc"},
    )
    assert [item["id"] for item in ordered_desc.json()["items"]] == [
        str(id_high),
        str(id_old),
        str(id_new),
        str(id_missing),
    ]

    # Omitted sorting is default Score descending; explicit default matches.
    default = await client.get("/api/v1/exams/selection-candidates")
    explicit_default = await client.get(
        "/api/v1/exams/selection-candidates", params={"sort": "selectionScore:desc"}
    )
    assert [item["id"] for item in default.json()["items"]] == [
        item["id"] for item in explicit_default.json()["items"]
    ]

    # Legacy #680 behavior kept (#708 B2): a bare sortOrder applies to the
    # default selectionScore field instead of being ignored.
    order_only_asc = await client.get(
        "/api/v1/exams/selection-candidates", params={"sortOrder": "asc"}
    )
    assert order_only_asc.status_code == 200
    scores_only = [item["selectionScore"] for item in order_only_asc.json()["items"]]
    assert scores_only == sorted(scores_only)
    order_only_desc = await client.get(
        "/api/v1/exams/selection-candidates", params={"sortOrder": "desc"}
    )
    assert order_only_desc.status_code == 200
    scores_only_desc = [
        item["selectionScore"] for item in order_only_desc.json()["items"]
    ]
    assert scores_only_desc == sorted(scores_only_desc, reverse=True)
    assert scores_only_desc[0] == scores_only[-1]

    # Legacy single-column params normalize to one criterion.
    legacy = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sortBy": "failureCount", "sortOrder": "asc"},
    )
    assert legacy.status_code == 200
    failed_counts = [item["failedCount"] for item in legacy.json()["items"]]
    assert failed_counts == sorted(failed_counts)

    # Malformed sort values reject with the validation convention, including
    # empty list members (#708 B3): trailing/doubled separators and empty
    # values are malformed lists, not defaults.
    for bad in (
        {"sort": "bogus:asc"},
        {"sort": "addDate:sideways"},
        {"sort": "addDate:asc,addDate:desc"},
        {"sort": "addDate"},
        {"sort": ",addDate:asc"},
        {"sort": "addDate:asc,"},
        {"sort": "addDate:asc,,correctStreak:desc"},
        {"sort": ",,,"},
        {"sort": ""},
        {"sort": " "},
    ):
        response = await client.get("/api/v1/exams/selection-candidates", params=bad)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    # Mixing the ordered list with the legacy params is ambiguous -> 422.
    response = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sort": "addDate:asc", "sortBy": "addDate"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_selection_candidates_sort_globally_across_pages(
    exams_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]
    now = datetime.now(UTC)

    problems = []
    for index in range(5):
        problem = make_problem(
            user_id, text=f"problem {index}", problem_type="fill-in-the-blank", correct_answer=str(index),
            tracking={"exposureCount": 1, "correctCount": 0, "failedCount": 1,
                      "lastTestedAt": now - timedelta(days=30), "lastAttemptCorrect": False},
        )
        problems.append(problem)
    database["problems"].seed(*problems)
    # Streaks 0, 1, 2, 3, 4 by creation order; sort streak asc -> p0..p4.
    # problems[0] gets one incorrect attempt so its streak is a real 0 rather
    # than missing-history null (which would sort last).
    for index, problem in enumerate(problems):
        if index == 0:
            database["practice_attempts"].seed(
                _make_practice_attempt(
                    user_id, problem["_id"], created_at=now - timedelta(days=5), grading_status="incorrect"
                )
            )
        for _ in range(index):
            database["practice_attempts"].seed(
                _make_practice_attempt(
                    user_id, problem["_id"], created_at=now - timedelta(days=5), grading_status="correct"
                )
            )

    first_page = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sort": "correctStreak:asc", "page": 1, "pageSize": 2},
    )
    second_page = await client.get(
        "/api/v1/exams/selection-candidates",
        params={"sort": "correctStreak:asc", "page": 2, "pageSize": 2},
    )
    assert [item["correctStreak"] for item in first_page.json()["items"]] == [0, 1]
    assert [item["correctStreak"] for item in second_page.json()["items"]] == [2, 3]
    assert second_page.json()["total"] == 5


@pytest.mark.asyncio
async def test_selection_candidates_history_loading_is_batched(
    exams_app: FastAPI, client: AsyncClient, monkeypatch
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    problems = [
        make_problem(user_id, text=f"batch {i}", problem_type="fill-in-the-blank", correct_answer=str(i))
        for i in range(6)
    ]
    database["problems"].seed(*problems)

    find_calls = {"practice_attempts": 0, "exams": 0}
    for collection_name in ("practice_attempts", "exams"):
        collection = database[collection_name]
        original_find = collection.find

        def make_counting_find(name: str, original: Any) -> Any:
            def counting_find(query: dict[str, Any]) -> Any:
                find_calls[name] += 1
                return original(query)

            return counting_find

        monkeypatch.setattr(collection, "find", make_counting_find(collection_name, original_find))

    response = await client.get("/api/v1/exams/selection-candidates")

    assert response.status_code == 200
    assert response.json()["total"] == 6
    # Adding candidates must not scale into per-problem history reads (#708).
    assert find_calls == {"practice_attempts": 1, "exams": 1}


from app.presentation.exam_helpers import make_exam_item


def _seed_submitted_exam_with_pending_items(
    database: FakeDatabase,
    user_id: ObjectId,
    problems: list[dict[str, Any]],
) -> dict[str, Any]:
    """Submitted exam whose items are all pending review with saved answers."""
    items = []
    for index, problem in enumerate(problems):
        item = make_exam_item(problem, order=index + 1)
        item["answer"] = {"raw": f"answer {index}", "savedAt": datetime.now(UTC)}
        item["grading"] = {"status": "pending-review"}
        items.append(item)
    now = datetime.now(UTC)
    exam = {
        "_id": ObjectId(),
        "userId": user_id,
        "state": "submitted",
        "items": items,
        "summary": {
            "totalProblems": len(items),
            "answeredProblems": len(items),
            "gradedProblems": 0,
            "pendingProblems": len(items),
            "correctProblems": 0,
            "failedProblems": 0,
            "score": None,
        },
        "createdAt": now,
        "startedAt": now,
        "submittedAt": now,
        "updatedAt": now,
    }
    database["exams"].seed(exam)
    return exam


@pytest.mark.asyncio
async def test_self_report_preserves_concurrent_resolutions_of_distinct_items(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    """A committed concurrent resolution of item B — landing between the
    outer read and the transactional snapshot — must survive item A's
    resolution (#712).

    The old write path stored ``items`` from the pre-transaction read, so the
    concurrent resolution was reverted to pending-review.
    """
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    problems = [
        make_problem(user_id, text=f"p{i}", problem_type="fill-in-the-blank", correct_answer=str(i))
        for i in range(2)
    ]
    database["problems"].seed(*problems)
    exam = _seed_submitted_exam_with_pending_items(database, user_id, problems)
    item_a = exam["items"][0]["itemId"]
    item_b = exam["items"][1]["itemId"]
    resolved_at = datetime.now(UTC)

    def resolve_item_b_concurrently() -> None:
        _apply_concurrent_self_report(
            database, exam["_id"], item_b, user_id, is_correct=False, resolved_at=resolved_at
        )

    # The self-report request reads exams twice: the outer owned read (#1),
    # then the transactional snapshot (#2). The concurrent write lands after
    # #1, so only a snapshot reload inside the transaction can observe it.
    database._collections["exams"] = _ExamFindProbe(
        database._collections["exams"],
        side_effect_after=1,
        side_effect=resolve_item_b_concurrently,
    )

    first = await client.post(
        f"/api/v1/exams/{exam['_id']}/items/{item_a}/self-report",
        json={"isCorrect": True},
    )
    assert first.status_code == 200

    stored = database["exams"]._documents[0]
    stored_by_id = {str(item["itemId"]): item for item in stored["items"]}
    # Both judgments survive: the transactional snapshot reloaded item B's
    # committed resolution instead of rewriting it from the outer read.
    assert stored_by_id[item_a]["grading"]["status"] == "correct"
    assert stored_by_id[item_b]["grading"]["status"] == "incorrect"
    assert stored["summary"]["correctProblems"] == 1
    assert stored["summary"]["failedProblems"] == 1
    # Tracking incremented exactly once per resolution.
    tracking_by_problem = {
        str(doc["_id"]): doc["tracking"] for doc in database["problems"]._documents
    }
    assert tracking_by_problem[str(problems[0]["_id"])]["exposureCount"] == 1
    assert tracking_by_problem[str(problems[0]["_id"])]["correctCount"] == 1
    assert tracking_by_problem[str(problems[1]["_id"])]["exposureCount"] == 1
    assert tracking_by_problem[str(problems[1]["_id"])]["failedCount"] == 1
    # Lifetime XP from the merged history: 5 (correct) + 10 (incorrect).
    home = await client.get("/api/v1/home/summary")
    assert home.status_code == 200
    assert home.json()["xp"]["totalXp"] == 15


@pytest.mark.asyncio
async def test_self_report_retry_reloads_snapshot_and_cannot_overwrite_concurrent_resolutions(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    """A transaction retried after a transient abort reloads its snapshot
    (#712): an already-resolved target fails with 409 instead of overwriting
    the concurrent resolutions of either item, and tracking is incremented
    exactly once per resolution.
    """
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    problems = [
        make_problem(user_id, text=f"p{i}", problem_type="fill-in-the-blank", correct_answer=str(i))
        for i in range(2)
    ]
    database["problems"].seed(*problems)
    exam = _seed_submitted_exam_with_pending_items(database, user_id, problems)
    item_a = exam["items"][0]["itemId"]
    item_b = exam["items"][1]["itemId"]
    resolved_at = datetime.now(UTC)

    def resolve_both_items_concurrently() -> None:
        _apply_concurrent_self_report(
            database, exam["_id"], item_a, user_id, is_correct=True, resolved_at=resolved_at
        )
        _apply_concurrent_self_report(
            database, exam["_id"], item_b, user_id, is_correct=False, resolved_at=resolved_at
        )

    # Attempt 1 aborts at the snapshot read (#2) before writing anything; the
    # concurrent resolutions commit during the retry window, and attempt 2's
    # snapshot (#3) observes them.
    database._collections["exams"] = _ExamFindProbe(database._collections["exams"], abort_on=2)
    exams_app.dependency_overrides[get_mongo_adapter] = lambda: _RetryingOnceAdapter(
        resolve_both_items_concurrently
    )

    replay = await client.post(
        f"/api/v1/exams/{exam['_id']}/items/{item_a}/self-report",
        json={"isCorrect": True},
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "ITEM_NOT_PENDING_REVIEW"

    stored = database["exams"]._documents[0]
    stored_by_id = {str(item["itemId"]): item for item in stored["items"]}
    # The concurrent resolutions survived the aborted retry untouched.
    assert stored_by_id[item_a]["grading"]["status"] == "correct"
    assert stored_by_id[item_b]["grading"]["status"] == "incorrect"
    assert stored["summary"]["correctProblems"] == 1
    assert stored["summary"]["failedProblems"] == 1
    # Exactly-once tracking: only the concurrent resolutions counted.
    tracking_by_problem = {
        str(doc["_id"]): doc["tracking"] for doc in database["problems"]._documents
    }
    assert tracking_by_problem[str(problems[0]["_id"])]["exposureCount"] == 1
    assert tracking_by_problem[str(problems[0]["_id"])]["correctCount"] == 1
    assert tracking_by_problem[str(problems[1]["_id"])]["exposureCount"] == 1
    assert tracking_by_problem[str(problems[1]["_id"])]["failedCount"] == 1
    # Both judged items feed XP once: 5 (correct) + 10 (incorrect).
    home = await client.get("/api/v1/home/summary")
    assert home.status_code == 200
    assert home.json()["xp"]["totalXp"] == 15


@pytest.mark.asyncio
async def test_self_report_replay_does_not_double_apply_resolution(
    exams_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    problems = [
        make_problem(user_id, text=f"p{i}", problem_type="fill-in-the-blank", correct_answer=str(i))
        for i in range(2)
    ]
    database["problems"].seed(*problems)
    exam = _seed_submitted_exam_with_pending_items(database, user_id, problems)
    item_a = exam["items"][0]["itemId"]
    item_b = exam["items"][1]["itemId"]

    await client.post(
        f"/api/v1/exams/{exam['_id']}/items/{item_a}/self-report",
        json={"isCorrect": True},
    )
    await client.post(
        f"/api/v1/exams/{exam['_id']}/items/{item_b}/self-report",
        json={"isCorrect": False},
    )
    tracking_after_resolutions = deepcopy(database["problems"]._documents[0]["tracking"])
    stored_after_resolutions = deepcopy(database["exams"]._documents[0])

    replay = await client.post(
        f"/api/v1/exams/{exam['_id']}/items/{item_a}/self-report",
        json={"isCorrect": False},
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "ITEM_NOT_PENDING_REVIEW"

    # The replay changed nothing: judgments and tracking are untouched.
    assert database["exams"]._documents[0] == stored_after_resolutions
    assert database["problems"]._documents[0]["tracking"] == tracking_after_resolutions
