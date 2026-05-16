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

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> FakeUpdateResult:
        for document in self._documents:
            if _matches(document, query):
                for key, value in update.get("$set", {}).items():
                    document[key] = deepcopy(value)
                return FakeUpdateResult(1)
        return FakeUpdateResult(0)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for document in self._documents if _matches(document, query))

    def find(self, query: dict[str, Any]) -> FakeCursor:
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
            "ingestion_previews": FakeCollection(),
            "users": FakeCollection(),
            "sessions": FakeCollection(),
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
        actual = document.get(key)
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
    database["ingestion_previews"].seed(preview)

    response = await client.post(
        "/api/v1/problems",
        json={
            "previewId": str(preview["_id"]),
            "text": "Choose all correct letters",
            "problemType": "multi-choice",
            "graphDsl": None,
            "correctAnswer": " C, A ,C ",
            "tags": ["geometry", " geometry ", "chapter-3"],
        },
    )

    assert response.status_code == 201
    body = response.json()["problem"]
    assert body["correctAnswer"] == {
        "display": " C, A ,C ",
        "normalizedText": "a,c",
        "normalizedSet": ["a", "c"],
        "format": "set",
    }
    assert body["tags"] == ["geometry", "chapter-3"]
    assert body["origin"]["previewId"] == str(preview["_id"])
    stored_problem = database["problems"]._documents[0]
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
        "/api/v1/problems",
        json={
            "previewId": str(other_user_preview["_id"]),
            "text": "Nope",
            "problemType": "short-answer",
            "graphDsl": None,
            "correctAnswer": "42",
            "tags": ["denied"],
        },
    )
    assert confirm_response.status_code == 403
    assert confirm_response.json() == {
        "error": {"code": "FORBIDDEN", "message": "Forbidden"}
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
