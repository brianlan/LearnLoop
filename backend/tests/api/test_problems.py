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

from app.infrastructure.storage.s3 import StorageObjectNotFoundError
from app.main import create_app
from app.presentation.deps import get_current_user, get_database
from app.presentation.errors import ApiError
from app.presentation.media import get_problem_storage


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

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for document in self._documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

    async def insert_one(self, document: dict[str, Any]) -> FakeInsertOneResult:
        stored = deepcopy(document)
        if "_id" not in stored:
            stored["_id"] = ObjectId()
        self._documents.append(stored)
        return FakeInsertOneResult(stored["_id"])

    async def insert_many(self, documents: list[dict[str, Any]], ordered: bool = True) -> None:
        for document in documents:
            stored = deepcopy(document)
            if "_id" not in stored:
                stored["_id"] = ObjectId()
            self._documents.append(stored)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> FakeUpdateResult:
        for document in self._documents:
            if _matches(document, query):
                for key, value in update.get("$set", {}).items():
                    document[key] = deepcopy(value)
                return FakeUpdateResult(1)
        return FakeUpdateResult(0)

    async def update_many(self, query: dict[str, Any], update: dict[str, Any]) -> FakeUpdateResult:
        count = 0
        for document in self._documents:
            if _matches(document, query):
                for key, value in update.get("$set", {}).items():
                    document[key] = deepcopy(value)
                count += 1
        return FakeUpdateResult(count)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for document in self._documents if _matches(document, query))

    def find(self, query: dict[str, Any], projection: dict[str, Any] | None = None) -> FakeCursor:
        matches = [document for document in self._documents if _matches(document, query)]
        return FakeCursor(matches)

    async def distinct(self, field: str, query: dict[str, Any]) -> list[Any]:
        values: list[Any] = []
        seen: set[Any] = set()
        for document in self._documents:
            if not _matches(document, query):
                continue
            current = document.get(field, [])
            iterable = current if isinstance(current, list) else [current]
            for value in iterable:
                if value in seen:
                    continue
                seen.add(value)
                values.append(value)
        return values


class FakeDatabase:
    def __init__(self) -> None:
        self._collections = {
            "problems": FakeCollection(),
            "tags": FakeCollection(),
            "folders": FakeCollection(),
            "ingestion_previews": FakeCollection(),
            "users": FakeCollection(),
            "sessions": FakeCollection(),
            "solution_generation_tasks": FakeCollection(),
            "canonical_solutions": FakeCollection(),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections[name]


class FakeStorage:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def seed(self, bucket: str, key: str, payload: bytes) -> None:
        self._objects[(bucket, key)] = payload

    def get_object(self, bucket: str, key: str) -> bytes:
        payload = self._objects.get((bucket, key))
        if payload is None:
            raise StorageObjectNotFoundError(key)
        return payload


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if key == "$or":
            if not any(_matches(document, sub) for sub in value):
                return False
            continue
        actual = document.get(key)
        if isinstance(value, dict):
            if "$in" in value:
                if actual not in value["$in"]:
                    return False
                continue
            if "$regex" in value:
                import re

                pattern = value["$regex"]
                options = value.get("$options", "")
                flags = 0
                if "i" in options:
                    flags |= re.IGNORECASE
                if isinstance(actual, list):
                    if not any(re.search(pattern, str(item), flags) for item in actual):
                        return False
                else:
                    if not re.search(pattern, str(actual or ""), flags):
                        return False
                continue
        if isinstance(actual, list):
            if value not in actual:
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


def make_preview(user_id: ObjectId, *, status: str = "ready") -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "status": status,
        "sourceImage": {
            "bucket": "learnloop-media",
            "objectKey": f"users/{user_id}/images/source.png",
            "contentType": "image/png",
            "sizeBytes": 4,
            "sha256": "abc123",
        },
        "extraction": {
            "requestModel": "gpt-4.1-mini",
            "rawText": "raw extracted text",
            "rawProblemType": "short-answer",
            "rawGraphDsl": "graph TD; A-->B",
        },
        "editableDraft": {
            "text": "draft text",
            "problemType": "short-answer",
            "graphDsl": None,
            "correctAnswer": "draft",
            "tags": ["draft"],
        },
        "createdAt": now,
        "updatedAt": now,
        "expiresAt": now + timedelta(hours=24),
    }


def make_problem(
    user_id: ObjectId,
    *,
    text: str = "What is 2+2?",
    problem_type: str = "short-answer",
    updated_at: datetime | None = None,
    tags: list[str] | None = None,
    is_deleted: bool = False,
    folder_id: str | None = None,
) -> dict[str, Any]:
    now = updated_at or datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": text,
        "problemType": problem_type,
        "graphDsl": None,
        "correctAnswer": {
            "display": "4",
            "normalizedText": "4",
            "normalizedSet": [],
            "format": "single",
        },
        "tags": tags or ["math"],
        "sourceImage": {
            "bucket": "learnloop-media",
            "objectKey": f"users/{user_id}/images/{ObjectId()}.png",
            "contentType": "image/png",
            "sizeBytes": 7,
            "sha256": None,
        },
        "origin": {
            "previewId": ObjectId(),
            "vlmModel": "gpt-4.1-mini",
            "rawExtractedText": "raw",
            "rawExtractedProblemType": problem_type,
            "rawExtractedGraphDsl": None,
        },
        "tracking": {
            "exposureCount": 3,
            "correctCount": 2,
            "failedCount": 1,
            "lastTestedAt": now,
            "lastAttemptCorrect": True,
        },
        "isDeleted": is_deleted,
        "deletedAt": now if is_deleted else None,
        "folderId": folder_id,
        "createdAt": now,
        "updatedAt": now,
    }


@pytest_asyncio.fixture
async def problems_app() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    storage = FakeStorage()
    primary_user = make_user(ObjectId(), "student1")
    secondary_user = make_user(ObjectId(), "student2")

    application.state.fake_database = database
    application.state.fake_storage = storage
    application.state.primary_user = primary_user
    application.state.secondary_user = secondary_user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_problem_storage] = lambda: storage
    application.dependency_overrides[get_current_user] = lambda: deepcopy(primary_user)
    return application


@pytest_asyncio.fixture
async def client(problems_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=problems_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_confirm_preview_creates_problem_and_canonicalizes_answer(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    preview = make_preview(problems_app.state.primary_user["_id"])
    preview["editableDraft"] = {
        "text": "Choose all correct letters",
        "problemType": "multi-choice",
        "graphDsl": None,
        "correctAnswer": " C, A ,C ",
        "tags": ["geometry", " geometry ", "chapter-3"],
    }
    database["ingestion_previews"].seed(preview)

    response = await client.post(f"/api/v1/ingestion-previews/{preview['_id']}/confirm")

    assert response.status_code == 201
    body = response.json()["problem"]
    assert body["correctAnswer"] == {
        "display": "C, A ,C",
        "normalizedText": "a,c",
        "normalizedSet": ["a", "c"],
        "format": "set",
    }
    assert body["tags"] == ["geometry", "chapter-3"]
    stored_problem = database["problems"]._documents[0]
    assert stored_problem["origin"]["previewId"] == str(preview["_id"])
    assert stored_problem["correctAnswer"]["normalizedSet"] == ["a", "c"]
    assert database["ingestion_previews"]._documents[0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_list_detail_update_delete_tags_and_tracking(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    other_problem = make_problem(
        user_id,
        text="Older problem",
        updated_at=datetime.now(UTC) - timedelta(days=2),
        tags=["algebra"],
    )
    newest_problem = make_problem(
        user_id,
        text="Newest problem",
        problem_type="fill-in-the-blank",
        updated_at=datetime.now(UTC) - timedelta(hours=1),
        tags=["geometry", "chapter-3"],
    )
    deleted_problem = make_problem(
        user_id,
        text="Deleted problem",
        updated_at=datetime.now(UTC),
        tags=["deleted"],
        is_deleted=True,
    )
    database["problems"].seed(other_problem, newest_problem, deleted_problem)

    list_response = await client.get("/api/v1/problems")
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert [item["id"] for item in list_body["items"]] == [
        str(newest_problem["_id"]),
        str(other_problem["_id"]),
    ]
    assert list_body["total"] == 2

    filtered_response = await client.get(
        "/api/v1/problems",
        params={"tag": "geometry", "type": "fill-in-the-blank", "page": 1, "pageSize": 1},
    )
    assert filtered_response.status_code == 200
    filtered_body = filtered_response.json()
    assert filtered_body["total"] == 1
    assert filtered_body["items"][0]["id"] == str(newest_problem["_id"])

    detail_response = await client.get(f"/api/v1/problems/{newest_problem['_id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["problem"]["text"] == "Newest problem"

    tracking_response = await client.get(f"/api/v1/problems/{newest_problem['_id']}/tracking")
    assert tracking_response.status_code == 200
    assert tracking_response.json() == {
        "problemId": str(newest_problem["_id"]),
        "tracking": {
            "exposureCount": 3,
            "correctCount": 2,
            "failedCount": 1,
            "lastTestedAt": newest_problem["updatedAt"].isoformat().replace("+00:00", "Z"),
            "lastAttemptCorrect": True,
        },
    }

    tags_response = await client.get("/api/v1/problems/tags")
    assert tags_response.status_code == 200
    assert tags_response.json() == {"items": ["algebra", "chapter-3", "geometry"]}

    update_response = await client.patch(
        f"/api/v1/problems/{newest_problem['_id']}",
        json={
            "text": "Updated text",
            "problemType": "multi-choice",
            "graphDsl": "graph LR; X-->Y",
            "correctAnswer": " B, A ,B ",
            "tags": ["logic", " logic ", "sets"],
        },
    )
    assert update_response.status_code == 200
    updated_body = update_response.json()["problem"]
    assert updated_body["text"] == "Updated text"
    assert updated_body["problemType"] == "multi-choice"
    assert updated_body["graphDsl"] == "graph LR; X-->Y"
    assert updated_body["correctAnswer"] == {
        "display": " B, A ,B ",
        "normalizedText": "a,b",
        "normalizedSet": ["a", "b"],
        "format": "set",
    }
    assert updated_body["tags"] == ["logic", "sets"]

    soft_delete_response = await client.delete(f"/api/v1/problems/{other_problem['_id']}")
    assert soft_delete_response.status_code == 200
    assert soft_delete_response.json() == {"ok": True}

    relist_response = await client.get("/api/v1/problems")
    assert relist_response.status_code == 200
    assert relist_response.json()["total"] == 1
    assert relist_response.json()["items"][0]["id"] == str(newest_problem["_id"])

    deleted_detail_response = await client.get(f"/api/v1/problems/{other_problem['_id']}")
    assert deleted_detail_response.status_code == 404
    assert deleted_detail_response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Problem not found"}
    }


@pytest.mark.asyncio
async def test_problem_detail_handles_none_origin(problems_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    problem = make_problem(problems_app.state.primary_user["_id"])
    problem["origin"] = None
    database["problems"].seed(problem)

    response = await client.get(f"/api/v1/problems/{problem['_id']}")

    assert response.status_code == 200
    assert response.json()["problem"]["origin"] == {
        "previewId": None,
        "vlmModel": None,
        "rawExtractedText": None,
        "rawExtractedProblemType": None,
        "rawExtractedGraphDsl": None,
    }


@pytest.mark.asyncio
async def test_problem_image_streams_for_owner_and_handles_missing_object(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    storage: FakeStorage = problems_app.state.fake_storage
    problem = make_problem(problems_app.state.primary_user["_id"])
    database["problems"].seed(problem)
    storage.seed(problem["sourceImage"]["bucket"], problem["sourceImage"]["objectKey"], b"pngdata")

    response = await client.get(f"/api/v1/problems/{problem['_id']}/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"pngdata"

    missing_problem = make_problem(problems_app.state.primary_user["_id"])
    database["problems"].seed(missing_problem)
    missing_response = await client.get(f"/api/v1/problems/{missing_problem['_id']}/image")
    assert missing_response.status_code == 404
    assert missing_response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Problem image not found"}
    }


@pytest.mark.asyncio
async def test_cross_user_access_is_denied_for_problem_and_media_routes(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    other_user_problem = make_problem(problems_app.state.secondary_user["_id"])
    other_user_preview = make_preview(problems_app.state.secondary_user["_id"])
    database["problems"].seed(other_user_problem)
    database["ingestion_previews"].seed(other_user_preview)

    detail_response = await client.get(f"/api/v1/problems/{other_user_problem['_id']}")
    assert detail_response.status_code == 403
    assert detail_response.json() == {
        "error": {"code": "FORBIDDEN", "message": "Forbidden"}
    }

    image_response = await client.get(f"/api/v1/problems/{other_user_problem['_id']}/image")
    assert image_response.status_code == 403
    assert image_response.json() == {
        "error": {"code": "FORBIDDEN", "message": "Forbidden"}
    }

    confirm_response = await client.post(
        f"/api/v1/ingestion-previews/{other_user_preview['_id']}/confirm"
    )
    assert confirm_response.status_code == 404
    assert confirm_response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Preview not found"}
    }


@pytest.mark.asyncio
async def test_problem_routes_require_authentication(problems_app: FastAPI) -> None:
    async def unauthenticated_user() -> dict[str, Any]:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")

    problems_app.dependency_overrides[get_current_user] = unauthenticated_user
    transport = ASGITransport(app=problems_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as unauthenticated_client:
        response = await unauthenticated_client.get("/api/v1/problems")

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHENTICATED", "message": "Authentication required"}
    }


@pytest.mark.asyncio
async def test_solution_status(problems_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id)
    problem_id_str = str(problem["_id"])
    database["problems"].seed(problem)

    # 1. returns 'none' when no task or solution exists
    response = await client.get(f"/api/v1/problems/{problem_id_str}/solution-status")
    assert response.status_code == 200
    assert response.json() == {"status": "none"}

    # 2. returns 'pending' when task is pending
    task_pending = {
        "_id": ObjectId(),
        "problem_id": problem_id_str,
        "user_id": str(user_id),
        "status": "pending",
        "created_at": datetime.now(UTC)
    }
    database["solution_generation_tasks"].seed(task_pending)
    response = await client.get(f"/api/v1/problems/{problem_id_str}/solution-status")
    assert response.status_code == 200
    assert response.json() == {"status": "pending"}

    # 3. returns 'generating' when task is generating
    database["solution_generation_tasks"]._documents.clear()
    task_generating = {**task_pending, "_id": ObjectId(), "status": "generating"}
    database["solution_generation_tasks"].seed(task_generating)
    response = await client.get(f"/api/v1/problems/{problem_id_str}/solution-status")
    assert response.status_code == 200
    assert response.json() == {"status": "generating"}

    # 4. returns 'failed' when task is failed
    database["solution_generation_tasks"]._documents.clear()
    task_failed = {**task_pending, "_id": ObjectId(), "status": "failed"}
    database["solution_generation_tasks"].seed(task_failed)
    response = await client.get(f"/api/v1/problems/{problem_id_str}/solution-status")
    assert response.status_code == 200
    assert response.json() == {"status": "failed"}

    # 5. returns 'ready' when solution exists
    solution = {
        "_id": ObjectId(),
        "problem_id": problem_id_str,
        "user_id": str(user_id),
        "steps_markdown": "step 1",
        "final_answer": "4",
        "math_level_classification": "basic",
    }
    database["canonical_solutions"].seed(solution)
    response = await client.get(f"/api/v1/problems/{problem_id_str}/solution-status")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}

    # 6. returns 403 for other user problem
    other_problem = make_problem(problems_app.state.secondary_user["_id"])
    database["problems"].seed(other_problem)
    response = await client.get(f"/api/v1/problems/{str(other_problem['_id'])}/solution-status")
    assert response.status_code == 403


async def test_search_by_text(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem1 = make_problem(user_id, text="Solve for x in equation", tags=["algebra"])
    problem2 = make_problem(user_id, text="Find the area of triangle", tags=["geometry"])
    problem3 = make_problem(user_id, text="What is 2+2?", tags=["arithmetic"])
    database["problems"].seed(problem1, problem2, problem3)

    response = await client.get("/api/v1/problems?q=equation")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["text"] == "Solve for x in equation"


async def test_search_by_tag(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem1 = make_problem(user_id, text="Problem A", tags=["algebra"])
    problem2 = make_problem(user_id, text="Problem B", tags=["geometry"])
    database["problems"].seed(problem1, problem2)

    response = await client.get("/api/v1/problems?q=alge")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["tags"] == ["algebra"]


async def test_search_case_insensitive(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="QUADRATIC equation", tags=["Algebra"])
    database["problems"].seed(problem)

    response = await client.get("/api/v1/problems?q=quadratic")
    assert response.status_code == 200
    assert response.json()["total"] == 1

    response = await client.get("/api/v1/problems?q=ALGEBRA")
    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_search_no_match(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="Hello world", tags=["greeting"])
    database["problems"].seed(problem)

    response = await client.get("/api/v1/problems?q=nonexistent")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_search_whitespace_ignored(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="Test problem", tags=["test"])
    database["problems"].seed(problem)

    response = await client.get("/api/v1/problems?q=%20%20%20")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1


async def test_search_regex_special_chars_treated_literally(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="Price is $10.00 (USD)", tags=["money"])
    database["problems"].seed(problem)

    response = await client.get("/api/v1/problems?q=$10.00")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["text"] == "Price is $10.00 (USD)"


async def test_search_composes_with_tag_filter(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem1 = make_problem(user_id, text="Solve for x", tags=["algebra"])
    problem2 = make_problem(user_id, text="Solve for y", tags=["geometry"])
    problem3 = make_problem(user_id, text="Find area", tags=["algebra"])
    database["problems"].seed(problem1, problem2, problem3)

    response = await client.get("/api/v1/problems?q=Solve&tag=algebra")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["text"] == "Solve for x"


async def test_search_composes_with_type_filter(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem1 = make_problem(user_id, text="Solve equation", tags=["algebra"], problem_type="short-answer")
    problem2 = make_problem(user_id, text="Solve equation", tags=["algebra"], problem_type="single-choice")
    database["problems"].seed(problem1, problem2)

    response = await client.get("/api/v1/problems?q=Solve&type=short-answer")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["problemType"] == "short-answer"


async def test_search_pagination_total_reflects_filtered(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    for i in range(5):
        database["problems"].seed(
            make_problem(user_id, text=f"Problem {i} about algebra", tags=["algebra"])
        )
    database["problems"].seed(
        make_problem(user_id, text="Problem about geometry", tags=["geometry"])
    )

    response = await client.get("/api/v1/problems?q=algebra&pageSize=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2


# Folder assignment and filtering tests


def make_folder(
    user_id: ObjectId,
    name: str,
    *,
    parent_id: ObjectId | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "name": name,
        "parentId": parent_id,
        "createdAt": now,
        "updatedAt": now,
    }


async def test_assign_single_problem_to_folder(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    problem = make_problem(user_id, text="Problem to move")
    database["folders"].seed(folder)
    database["problems"].seed(problem)

    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/folder",
        json={"folderId": str(folder["_id"])},
    )
    assert response.status_code == 200
    body = response.json()["problem"]
    assert body["folderId"] == str(folder["_id"])

    # Verify stored
    stored = database["problems"]._documents[0]
    assert stored["folderId"] == str(folder["_id"])


async def test_assign_problem_to_unfiled(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    problem = make_problem(user_id, text="Problem in folder", folder_id=str(folder["_id"]))
    database["folders"].seed(folder)
    database["problems"].seed(problem)

    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/folder",
        json={"folderId": None},
    )
    assert response.status_code == 200
    body = response.json()["problem"]
    assert body["folderId"] is None


async def test_bulk_assign_problems_to_folder(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    p1 = make_problem(user_id, text="P1")
    p2 = make_problem(user_id, text="P2")
    p3 = make_problem(user_id, text="P3")
    database["folders"].seed(folder)
    database["problems"].seed(p1, p2, p3)

    response = await client.patch(
        "/api/v1/problems/bulk-folder",
        json={
            "problemIds": [str(p1["_id"]), str(p2["_id"])],
            "folderId": str(folder["_id"]),
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # Verify only p1 and p2 were updated
    stored_p1 = next(p for p in database["problems"]._documents if p["_id"] == p1["_id"])
    stored_p2 = next(p for p in database["problems"]._documents if p["_id"] == p2["_id"])
    stored_p3 = next(p for p in database["problems"]._documents if p["_id"] == p3["_id"])
    assert stored_p1["folderId"] == str(folder["_id"])
    assert stored_p2["folderId"] == str(folder["_id"])
    assert stored_p3.get("folderId") is None


async def test_bulk_assign_to_unfiled(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    p1 = make_problem(user_id, text="P1", folder_id=str(folder["_id"]))
    p2 = make_problem(user_id, text="P2", folder_id=str(folder["_id"]))
    database["folders"].seed(folder)
    database["problems"].seed(p1, p2)

    response = await client.patch(
        "/api/v1/problems/bulk-folder",
        json={
            "problemIds": [str(p1["_id"]), str(p2["_id"])],
            "folderId": None,
        },
    )
    assert response.status_code == 200

    stored_p1 = next(p for p in database["problems"]._documents if p["_id"] == p1["_id"])
    stored_p2 = next(p for p in database["problems"]._documents if p["_id"] == p2["_id"])
    assert stored_p1["folderId"] is None
    assert stored_p2["folderId"] is None


async def test_reject_assign_to_nonexistent_folder(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    problem = make_problem(user_id, text="P1")
    database["problems"].seed(problem)

    fake_folder_id = str(ObjectId())
    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/folder",
        json={"folderId": fake_folder_id},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_reject_assign_to_other_user_folder(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    other_user_id = problems_app.state.secondary_user["_id"]

    other_folder = make_folder(other_user_id, "Other's folder")
    problem = make_problem(user_id, text="P1")
    database["folders"].seed(other_folder)
    database["problems"].seed(problem)

    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/folder",
        json={"folderId": str(other_folder["_id"])},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_reject_bulk_move_other_user_problems(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    other_user_id = problems_app.state.secondary_user["_id"]

    folder = make_folder(user_id, "My folder")
    other_problem = make_problem(other_user_id, text="Other's problem")
    database["folders"].seed(folder)
    database["problems"].seed(other_problem)

    response = await client.patch(
        "/api/v1/problems/bulk-folder",
        json={
            "problemIds": [str(other_problem["_id"])],
            "folderId": str(folder["_id"]),
        },
    )
    assert response.status_code == 403
    assert "FORBIDDEN" in response.json()["error"]["code"]


async def test_filter_by_folder_includes_descendants(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    # Create folder hierarchy: Chapter 1 -> Section 1.1
    chapter = make_folder(user_id, "Chapter 1")
    section = make_folder(user_id, "Section 1.1", parent_id=chapter["_id"])
    database["folders"].seed(chapter, section)

    # Problems in different folders
    p_chapter = make_problem(user_id, text="In Chapter", folder_id=str(chapter["_id"]))
    p_section = make_problem(user_id, text="In Section", folder_id=str(section["_id"]))
    p_unfiled = make_problem(user_id, text="Unfiled")
    database["problems"].seed(p_chapter, p_section, p_unfiled)

    # Filter by Chapter 1 should include both chapter and section problems
    response = await client.get(f"/api/v1/problems?folderId={chapter['_id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    texts = {item["text"] for item in data["items"]}
    assert texts == {"In Chapter", "In Section"}


async def test_filter_by_unfiled(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    p_in_folder = make_problem(user_id, text="In folder", folder_id=str(folder["_id"]))
    p_unfiled_null = make_problem(user_id, text="Unfiled null", folder_id=None)
    p_unfiled_missing = make_problem(user_id, text="Unfiled missing")
    del p_unfiled_missing["folderId"]  # Remove the field entirely

    database["folders"].seed(folder)
    database["problems"].seed(p_in_folder, p_unfiled_null, p_unfiled_missing)

    response = await client.get("/api/v1/problems?folderId=unfiled")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    texts = {item["text"] for item in data["items"]}
    assert texts == {"Unfiled null", "Unfiled missing"}


async def test_omit_folder_id_returns_all_problems(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    p_in_folder = make_problem(user_id, text="In folder", folder_id=str(folder["_id"]))
    p_unfiled = make_problem(user_id, text="Unfiled")

    database["folders"].seed(folder)
    database["problems"].seed(p_in_folder, p_unfiled)

    response = await client.get("/api/v1/problems")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


async def test_folder_filter_composes_with_tag_and_search(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    p1 = make_problem(user_id, text="Algebra problem", tags=["algebra"], folder_id=str(folder["_id"]))
    p2 = make_problem(user_id, text="Geometry problem", tags=["geometry"], folder_id=str(folder["_id"]))
    p3 = make_problem(user_id, text="Another algebra", tags=["algebra"], folder_id=str(folder["_id"]))

    database["folders"].seed(folder)
    database["problems"].seed(p1, p2, p3)

    # Filter by folder + tag
    response = await client.get(f"/api/v1/problems?folderId={folder['_id']}&tag=algebra")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2

    # Filter by folder + search
    response = await client.get(f"/api/v1/problems?folderId={folder['_id']}&q=Algebra")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


async def test_problem_payload_includes_folder_id(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    problem = make_problem(user_id, text="Problem", folder_id=str(folder["_id"]))
    database["folders"].seed(folder)
    database["problems"].seed(problem)

    # List endpoint
    list_response = await client.get("/api/v1/problems")
    assert list_response.status_code == 200
    item = list_response.json()["items"][0]
    assert item["folderId"] == str(folder["_id"])

    # Detail endpoint
    detail_response = await client.get(f"/api/v1/problems/{problem['_id']}")
    assert detail_response.status_code == 200
    body = detail_response.json()["problem"]
    assert body["folderId"] == str(folder["_id"])
