from __future__ import annotations

import asyncio
import base64
import io
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.config.settings import Settings
from app.infrastructure.vlm.client import ExtractionResult, VLMError
from app.main import create_app
from app.presentation.deps import get_app_settings, get_database
from app.presentation import ingestion as ingestion_presentation


class FakeInsertOneResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class FakeDeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = [deepcopy(document) for document in documents]

    def sort(self, field: str, direction: int) -> FakeCursor:
        reverse = direction < 0
        self._documents.sort(key=lambda document: document.get(field), reverse=reverse)
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        if length is None:
            return [deepcopy(document) for document in self._documents]
        return [deepcopy(document) for document in self._documents[:length]]


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        actual = document.get(key)
        if isinstance(value, dict):
            allowed_values = value.get("$in")
            if allowed_values is not None:
                if actual not in allowed_values:
                    return False
                continue
            return False
        if isinstance(actual, list):
            if value not in actual:
                return False
            continue
        if actual != value:
            return False
    return True


class FakeCollection:
    def __init__(self) -> None:
        self._documents: list[dict[str, Any]] = []
        self._insert_one_error: Exception | None = None

    def seed(self, *documents: dict[str, Any]) -> None:
        self._documents.extend(deepcopy(list(documents)))

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for document in self._documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

    async def insert_one(self, document: dict[str, Any]) -> FakeInsertOneResult:
        if self._insert_one_error is not None:
            raise self._insert_one_error
        stored_document = deepcopy(document)
        if "_id" not in stored_document:
            stored_document["_id"] = ObjectId()
        self._documents.append(stored_document)
        return FakeInsertOneResult(stored_document["_id"])

    async def insert_many(self, documents: list[dict[str, Any]], ordered: bool = True) -> None:
        for document in documents:
            stored_document = deepcopy(document)
            if "_id" not in stored_document:
                stored_document["_id"] = ObjectId()
            self._documents.append(stored_document)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> FakeUpdateResult:
        for document in self._documents:
            if _matches(document, query):
                for key, value in update.get("$set", {}).items():
                    document[key] = deepcopy(value)
                return FakeUpdateResult(1)
        return FakeUpdateResult(0)

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
    ) -> dict[str, Any] | None:
        for document in self._documents:
            if _matches(document, query):
                original_document = deepcopy(document)
                for key, value in update.get("$set", {}).items():
                    document[key] = deepcopy(value)
                return original_document
        return None

    async def delete_one(self, query: dict[str, Any]) -> FakeDeleteResult:
        for index, document in enumerate(self._documents):
            if _matches(document, query):
                del self._documents[index]
                return FakeDeleteResult(1)
        return FakeDeleteResult(0)

    def find(self, query: dict[str, Any]) -> FakeCursor:
        matching = [document for document in self._documents if _matches(document, query)]
        return FakeCursor(matching)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for document in self._documents if _matches(document, query))


class FakeDatabase:
    def __init__(self) -> None:
        self._collections = {
            "users": FakeCollection(),
            "sessions": FakeCollection(),
            "ingestion_previews": FakeCollection(),
            "problems": FakeCollection(),
            "tags": FakeCollection(),
            "solution_generation_tasks": FakeCollection(),
            "canonical_solutions": FakeCollection(),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections[name]


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[tuple[str, str, str | None, bytes]] = []
        self.get_calls: list[tuple[str, str]] = []
        self._counter = 0

    def build_object_key(self, user_id: str, extension: str) -> str:
        self._counter += 1
        return f"users/{user_id}/ingestion/preview-{self._counter}{extension}"

    def put_object(self, bucket: str, object_key: str, payload: bytes, content_type: str | None) -> None:
        self.objects[(bucket, object_key)] = payload
        self.put_calls.append((bucket, object_key, content_type, payload))

    def get_object(self, bucket: str, object_key: str) -> bytes:
        self.get_calls.append((bucket, object_key))
        return self.objects[(bucket, object_key)]

    def seed(self, bucket: str, object_key: str, payload: bytes) -> None:
        self.objects[(bucket, object_key)] = payload


@dataclass
class DelayedResult:
    delay_seconds: float
    result: ExtractionResult


class FakeVLMClient:
    def __init__(self) -> None:
        self.responses: list[ExtractionResult | DelayedResult | Exception] = []
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def extract(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
    ) -> ExtractionResult:
        self.calls.append({"image_url": image_url, "image_base64": image_base64})
        response = self.responses.pop(0)
        if isinstance(response, DelayedResult):
            await asyncio.sleep(response.delay_seconds)
            return response.result
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self) -> None:
        self.closed = True


def make_png_bytes() -> bytes:
    payload = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9l9iAAAAAASUVORK5CYII="
    )
    return io.BytesIO(payload).getvalue()


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def make_extraction_result(
    *,
    text: str = "What is 2 + 2?",
    problem_type: Literal[
        "single-choice", "multi-choice", "fill-in-the-blank", "short-answer"
    ] = "short-answer",
    graph_dsl: str | None = None,
    model: str = "gpt-4.1-mini",
) -> ExtractionResult:
    return ExtractionResult(
        request_type="ingestion",
        model=model,
        prompt_version="test-prompt-v1",
        schema_version="test-schema-v1",
        text=text,
        problem_type=problem_type,
        graph_dsl=graph_dsl,
        provider_metadata={"provider": "fake-vlm"},
        raw_provider_response={
            "text": text,
            "problemType": problem_type,
            "graphDsl": graph_dsl,
            "providerMetadata": {"provider": "fake-vlm"},
        },
    )


def make_preview(
    user_id: ObjectId,
    *,
    status: str = "ready",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    expires_at: datetime | None = None,
    request_started_at: datetime | None = None,
) -> dict[str, Any]:
    now = created_at or datetime.now(UTC)
    refreshed_at = updated_at or now
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "status": status,
        "sourceImage": {
            "bucket": "learnloop-media",
            "objectKey": f"users/{user_id}/images/source.png",
            "contentType": "image/png",
            "sizeBytes": 67,
            "sha256": "seeded-hash",
            "uploadedAt": now,
        },
        "extraction": {
            "requestModel": "gpt-4.1-mini",
            "requestStartedAt": request_started_at,
            "requestFinishedAt": refreshed_at if status != "extracting" else None,
            "success": status == "ready",
            "rawText": "raw extracted text" if status != "vlm-failed" else None,
            "rawProblemType": "short-answer" if status != "vlm-failed" else None,
            "rawGraphDsl": None,
            "rawProviderResponse": {"provider": "fake-vlm"} if status == "ready" else None,
            "failureCode": "vlm-timeout" if status == "vlm-failed" else None,
            "failureMessage": "timed out" if status == "vlm-failed" else None,
        },
        "editableDraft": {
            "text": "draft text",
            "problemType": "short-answer",
            "graphDsl": None,
            "correctAnswer": "4",
            "tags": ["draft"],
        },
        "createdAt": now,
        "updatedAt": refreshed_at,
        "expiresAt": expires_at or (now + timedelta(hours=24)),
    }


async def register_and_login(
    client: AsyncClient,
    app: FastAPI,
    *,
    username: str,
    password: str = "secret",
) -> dict[str, Any]:
    register_response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert register_response.status_code == 201

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert login_response.status_code == 200

    database: FakeDatabase = app.state.fake_database
    user = await database["users"].find_one({"username": username})
    assert user is not None
    return user


@pytest_asyncio.fixture
async def ingestion_app() -> AsyncIterator[FastAPI]:
    application = create_app()
    database = FakeDatabase()
    storage = FakeStorage()
    vlm_client = FakeVLMClient()
    settings = Settings(
        vlm_model="gpt-4.1-mini",
        vlm_timeout_seconds=1.0,
        preview_extracting_window_seconds=1.0,
    )

    application.state.fake_database = database
    application.state.fake_storage = storage
    application.state.fake_vlm_client = vlm_client
    application.state.sync_wait_seconds = 1.0

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[ingestion_presentation.get_s3_storage] = lambda: storage
    application.dependency_overrides[ingestion_presentation.get_vlm_client] = lambda: vlm_client
    application.dependency_overrides[ingestion_presentation.get_preview_sync_wait_seconds] = (
        lambda: application.state.sync_wait_seconds
    )

    yield application

    pending_tasks = list(ingestion_presentation.preview_tasks.values())
    for task in pending_tasks:
        if not task.done():
            task.cancel()
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)
    ingestion_presentation.preview_tasks.clear()


@pytest_asyncio.fixture
async def client(ingestion_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=ingestion_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest_asyncio.fixture
async def authenticated_client(client: AsyncClient, ingestion_app: FastAPI) -> AsyncIterator[AsyncClient]:
    await register_and_login(client, ingestion_app, username="student1")
    yield client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "sync_wait_seconds", "expected_status"),
    [
        (make_extraction_result(), 1.0, "ready"),
        (DelayedResult(0.0, make_extraction_result(text="Delayed text")), 0.0, "extracting"),
        (
            VLMError(
                "VLM request timed out",
                code="vlm-timeout",
                retryable=True,
                raw_provider_response={"detail": "timeout"},
            ),
            1.0,
            "vlm-failed",
        ),
    ],
)
async def test_create_preview_with_valid_image_returns_expected_status(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
    response: ExtractionResult | DelayedResult | Exception,
    sync_wait_seconds: float,
    expected_status: str,
) -> None:
    ingestion_app.state.fake_vlm_client.responses = [response]
    ingestion_app.state.sync_wait_seconds = sync_wait_seconds

    image_bytes = make_png_bytes()
    create_response = await authenticated_client.post(
        "/api/v1/ingestion-previews",
        files={"image": ("test.png", image_bytes, "image/png")},
    )

    assert create_response.status_code == 201
    preview = create_response.json()["preview"]
    assert preview["status"] == expected_status
    assert preview["sourceImage"]["contentType"] == "image/png"
    assert preview["sourceImage"]["sizeBytes"] == len(image_bytes)

    storage: FakeStorage = ingestion_app.state.fake_storage
    assert len(storage.put_calls) == 1
    assert storage.put_calls[0][3] == image_bytes

    if expected_status == "ready":
        assert preview["draft"]["text"] == "What is 2 + 2?"
        assert preview["draft"]["problemType"] == "short-answer"
        assert preview["extraction"]["success"] is True
    elif expected_status == "extracting":
        assert preview["draft"]["text"] is None
        assert preview["extraction"]["success"] is None
    else:
        assert preview["extraction"]["success"] is False
        assert preview["extraction"]["failureCode"] == "vlm-timeout"
        assert preview["extraction"]["failureMessage"] == "VLM request timed out"


@pytest.mark.asyncio
async def test_create_preview_with_non_image_returns_validation_error(
    authenticated_client: AsyncClient,
) -> None:
    response = await authenticated_client.post(
        "/api/v1/ingestion-previews",
        files={"image": ("notes.txt", b"not-an-image", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "INVALID_IMAGE", "message": "Uploaded file must be an image"}
    }


@pytest.mark.asyncio
async def test_get_preview_returns_owner_preview(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    owner = await database["users"].find_one({"username": "student1"})
    assert owner is not None
    preview = make_preview(owner["_id"])
    database["ingestion_previews"].seed(preview)

    response = await authenticated_client.get(f"/api/v1/ingestion-previews/{preview['_id']}")

    assert response.status_code == 200
    body = response.json()["preview"]
    assert body["id"] == str(preview["_id"])
    assert body["status"] == "ready"
    assert body["draft"]["text"] == "draft text"


@pytest.mark.asyncio
async def test_get_preview_returns_not_found_for_non_owner(
    ingestion_app: FastAPI,
) -> None:
    primary_transport = ASGITransport(app=ingestion_app)
    secondary_transport = ASGITransport(app=ingestion_app)

    async with AsyncClient(transport=primary_transport, base_url="http://testserver") as owner_client:
        owner = await register_and_login(owner_client, ingestion_app, username="student1")
        preview = make_preview(owner["_id"])
        ingestion_app.state.fake_database["ingestion_previews"].seed(preview)

    async with AsyncClient(transport=secondary_transport, base_url="http://testserver") as other_client:
        await register_and_login(other_client, ingestion_app, username="student2")
        response = await other_client.get(f"/api/v1/ingestion-previews/{preview['_id']}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Preview not found"}
    }


@pytest.mark.asyncio
async def test_patch_preview_persists_draft_and_extends_ttl(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    owner = await database["users"].find_one({"username": "student1"})
    assert owner is not None
    old_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    preview = make_preview(owner["_id"], expires_at=old_expires_at)
    database["ingestion_previews"].seed(preview)

    response = await authenticated_client.patch(
        f"/api/v1/ingestion-previews/{preview['_id']}",
        json={
            "text": "  Updated prompt text  ",
            "problemType": "multi-choice",
            "graphDsl": "graph TD; A-->B",
            "correctAnswer": "  B, A ,B  ",
            "tags": ["logic", " logic ", "sets"],
        },
    )

    assert response.status_code == 200
    body = response.json()["preview"]
    assert body["draft"] == {
        "text": "Updated prompt text",
        "problemType": "multi-choice",
        "graphDsl": "graph TD; A-->B",
        "correctAnswer": "B, A ,B",
        "tags": ["logic", "sets"],
    }
    assert parse_datetime(body["expiresAt"]) > old_expires_at

    stored_preview = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
    assert stored_preview is not None
    assert stored_preview["editableDraft"] == {
        "text": "Updated prompt text",
        "problemType": "multi-choice",
        "graphDsl": "graph TD; A-->B",
        "correctAnswer": "B, A ,B",
        "tags": ["logic", "sets"],
    }
    assert stored_preview["expiresAt"] > old_expires_at


@pytest.mark.asyncio
async def test_retry_preview_reuses_stored_image_and_reextracts(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    storage: FakeStorage = ingestion_app.state.fake_storage
    vlm_client: FakeVLMClient = ingestion_app.state.fake_vlm_client
    owner = await database["users"].find_one({"username": "student1"})
    assert owner is not None

    preview = make_preview(owner["_id"], status="vlm-failed")
    database["ingestion_previews"].seed(preview)
    image_bytes = make_png_bytes()
    storage.seed(preview["sourceImage"]["bucket"], preview["sourceImage"]["objectKey"], image_bytes)
    vlm_client.responses = [make_extraction_result(text="Retry succeeded")]

    response = await authenticated_client.post(
        f"/api/v1/ingestion-previews/{preview['_id']}/retry"
    )

    assert response.status_code == 200
    body = response.json()["preview"]
    assert body["status"] == "ready"
    assert body["draft"]["text"] == "draft text"
    assert storage.get_calls == [
        (preview["sourceImage"]["bucket"], preview["sourceImage"]["objectKey"])
    ]
    assert storage.put_calls == []
    assert len(vlm_client.calls) == 1
    assert isinstance(vlm_client.calls[0]["image_base64"], str)


@pytest.mark.asyncio
async def test_confirm_preview_creates_problem_from_confirmed_draft(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    owner = await database["users"].find_one({"username": "student1"})
    assert owner is not None
    preview = make_preview(owner["_id"], status="ready")
    preview["editableDraft"] = {
        "text": "Choose all correct letters",
        "problemType": "multi-choice",
        "graphDsl": "graph LR; A-->B",
        "correctAnswer": " C, A ,C ",
        "tags": ["geometry", " geometry ", "chapter-3"],
    }
    preview["extraction"]["rawText"] = "raw extracted text"
    preview["extraction"]["rawProblemType"] = "multi-choice"
    preview["extraction"]["rawGraphDsl"] = "graph LR; A-->B"
    database["ingestion_previews"].seed(preview)

    response = await authenticated_client.post(
        f"/api/v1/ingestion-previews/{preview['_id']}/confirm"
    )

    assert response.status_code == 201
    body = response.json()["problem"]
    assert body["text"] == "Choose all correct letters"
    assert body["problemType"] == "multi-choice"
    assert body["correctAnswer"] == {
        "display": "C, A ,C",
        "normalizedText": "a,c",
        "normalizedSet": ["a", "c"],
        "format": "set",
    }
    assert body["tags"] == ["geometry", "chapter-3"]
    assert body["sourceImage"]["objectKey"] == preview["sourceImage"]["objectKey"]

    stored_problem = await database["problems"].find_one({"_id": ObjectId(body["id"])})
    assert stored_problem is not None
    assert stored_problem["origin"] == {
        "previewId": str(preview["_id"]),
        "vlmModel": "gpt-4.1-mini",
        "rawExtractedText": "raw extracted text",
        "rawExtractedProblemType": "multi-choice",
        "rawExtractedGraphDsl": "graph LR; A-->B",
    }

    updated_preview = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
    assert updated_preview is not None
    assert updated_preview["status"] == "confirmed"
    solution_task = await database["solution_generation_tasks"].find_one({"problem_id": body["id"]})
    assert solution_task is not None
    assert solution_task["user_id"] == str(owner["_id"])
    assert solution_task["status"] == "pending"
    assert solution_task["retry_count"] == 0
    assert solution_task["failure_reason"] is None
    assert solution_task["started_at"] is None


@pytest.mark.asyncio
async def test_confirm_preview_allows_manual_confirmation_after_vlm_failure(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    owner = await database["users"].find_one({"username": "student1"})
    assert owner is not None
    preview = make_preview(owner["_id"], status="vlm-failed")
    preview["editableDraft"] = {
        "text": "Solve for x",
        "problemType": "fill-in-the-blank",
        "graphDsl": None,
        "correctAnswer": "12",
        "tags": ["manual-fix"],
    }
    database["ingestion_previews"].seed(preview)

    response = await authenticated_client.post(
        f"/api/v1/ingestion-previews/{preview['_id']}/confirm"
    )

    assert response.status_code == 201
    body = response.json()["problem"]
    assert body["text"] == "Solve for x"
    assert body["correctAnswer"]["display"] == "12"

    updated_preview = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
    assert updated_preview is not None
    assert updated_preview["status"] == "confirmed"


@pytest.mark.asyncio
async def test_confirm_preview_rolls_back_status_when_draft_missing_required_fields(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    owner = await database["users"].find_one({"username": "student1"})
    assert owner is not None
    preview = make_preview(owner["_id"], status="ready")
    preview["editableDraft"] = {
        "text": None,
        "problemType": "short-answer",
        "graphDsl": None,
        "correctAnswer": "42",
        "tags": [],
    }
    original_status = preview["status"]
    database["ingestion_previews"].seed(preview)

    response = await authenticated_client.post(
        f"/api/v1/ingestion-previews/{preview['_id']}/confirm"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PREVIEW"

    stored_preview = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
    assert stored_preview is not None
    assert stored_preview["status"] == original_status


@pytest.mark.asyncio
async def test_confirm_preview_rolls_back_status_when_problem_insert_fails(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    owner = await database["users"].find_one({"username": "student1"})
    assert owner is not None
    original_status = "ready"
    preview = make_preview(owner["_id"], status=original_status)
    preview["editableDraft"] = {
        "text": "Solve for x",
        "problemType": "short-answer",
        "graphDsl": None,
        "correctAnswer": "42",
        "tags": [],
    }
    database["ingestion_previews"].seed(preview)

    database["problems"]._insert_one_error = RuntimeError("simulated database error")

    response = await authenticated_client.post(
        f"/api/v1/ingestion-previews/{preview['_id']}/confirm"
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "PROBLEM_CREATION_FAILED"

    stored_preview = await database["ingestion_previews"].find_one({"_id": preview["_id"]})
    assert stored_preview is not None
    assert stored_preview["status"] == original_status

    stored_problem = await database["problems"].find_one({"userId": owner["_id"]})
    assert stored_problem is None


@pytest.mark.asyncio
async def test_confirm_preview_rejects_double_confirmation(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    owner = await database["users"].find_one({"username": "student1"})
    assert owner is not None
    preview = make_preview(owner["_id"], status="ready")
    database["ingestion_previews"].seed(preview)

    first_response = await authenticated_client.post(
        f"/api/v1/ingestion-previews/{preview['_id']}/confirm"
    )

    assert first_response.status_code == 201

    second_response = await authenticated_client.post(
        f"/api/v1/ingestion-previews/{preview['_id']}/confirm"
    )

    assert second_response.status_code == 409
    assert second_response.json()["error"]["code"] == "PREVIEW_ALREADY_CONFIRMED"
    assert await database["solution_generation_tasks"].count_documents({}) == 1


@pytest.mark.asyncio
async def test_backfill_solution_generation_tasks_enqueues_only_missing_problems(
    ingestion_app: FastAPI,
) -> None:
    from app.presentation.solution_generation import backfill_solution_generation_tasks

    database: FakeDatabase = ingestion_app.state.fake_database
    owner = {
        "_id": ObjectId(),
        "username": "student1",
        "status": "active",
        "createdAt": datetime.now(UTC),
        "updatedAt": datetime.now(UTC),
        "lastLoginAt": None,
    }
    database["users"].seed(owner)

    now = datetime.now(UTC)
    missing_problem = {
        "_id": ObjectId(),
        "userId": owner["_id"],
        "isDeleted": False,
        "createdAt": now,
        "updatedAt": now,
    }
    problem_with_task = {
        "_id": ObjectId(),
        "userId": owner["_id"],
        "isDeleted": False,
        "createdAt": now,
        "updatedAt": now,
    }
    problem_with_solution = {
        "_id": ObjectId(),
        "userId": owner["_id"],
        "isDeleted": False,
        "createdAt": now,
        "updatedAt": now,
    }
    deleted_problem = {
        "_id": ObjectId(),
        "userId": owner["_id"],
        "isDeleted": True,
        "createdAt": now,
        "updatedAt": now,
    }
    database["problems"].seed(missing_problem, problem_with_task, problem_with_solution, deleted_problem)
    database["solution_generation_tasks"].seed(
        {
            "_id": ObjectId(),
            "problem_id": str(problem_with_task["_id"]),
            "user_id": str(owner["_id"]),
            "status": "pending",
            "retry_count": 0,
            "failure_reason": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
        }
    )
    database["canonical_solutions"].seed(
        {
            "_id": ObjectId(),
            "problem_id": str(problem_with_solution["_id"]),
            "user_id": str(owner["_id"]),
            "steps_markdown": "steps",
            "final_answer": "answer",
            "math_level_classification": "middle-school",
            "created_at": now,
        }
    )

    inserted = await backfill_solution_generation_tasks(database, now=now)

    assert inserted == 1
    assert await database["solution_generation_tasks"].count_documents({}) == 2
    missing_task = await database["solution_generation_tasks"].find_one({"problem_id": str(missing_problem["_id"])})
    assert missing_task is not None
    assert await database["solution_generation_tasks"].find_one({"problem_id": str(deleted_problem["_id"])}) is None


@pytest.mark.asyncio
async def test_get_preview_recovers_stale_extracting_preview_to_vlm_failed(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    owner = await database["users"].find_one({"username": "student1"})
    assert owner is not None
    stale_started_at = datetime.now(UTC) - timedelta(seconds=10)
    preview = make_preview(
        owner["_id"],
        status="extracting",
        updated_at=stale_started_at,
        request_started_at=stale_started_at,
    )
    preview["extraction"]["requestModel"] = "gpt-4.1-mini"
    preview["extraction"]["success"] = None
    database["ingestion_previews"].seed(preview)

    response = await authenticated_client.get(f"/api/v1/ingestion-previews/{preview['_id']}")

    assert response.status_code == 200
    body = response.json()["preview"]
    assert body["status"] == "vlm-failed"
    assert body["extraction"]["success"] is False
    assert body["extraction"]["failureCode"] == "vlm-stale-preview-timeout"
    assert body["extraction"]["failureMessage"] == (
        "Preview extraction exceeded the configured extracting window."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        (
            "post",
            "/api/v1/ingestion-previews",
            {"files": {"image": ("test.png", make_png_bytes(), "image/png")}},
        ),
        ("get", f"/api/v1/ingestion-previews/{ObjectId()}", {}),
        (
            "patch",
            f"/api/v1/ingestion-previews/{ObjectId()}",
            {"json": {"text": "draft"}},
        ),
        ("post", f"/api/v1/ingestion-previews/{ObjectId()}/retry", {}),
        ("post", f"/api/v1/ingestion-previews/{ObjectId()}/confirm", {}),
    ],
)
async def test_ingestion_routes_require_authentication(
    client: AsyncClient,
    method: str,
    path: str,
    kwargs: dict[str, Any],
) -> None:
    response = await getattr(client, method)(path, **kwargs)

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHENTICATED", "message": "Authentication required"}
    }


@pytest.mark.asyncio
async def test_stream_preview_image_returns_image(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    storage: FakeStorage = ingestion_app.state.fake_storage
    user = await database["users"].find_one({"username": "student1"})
    assert user is not None
    user_id = user["_id"]

    image_bytes = make_png_bytes()
    preview = make_preview(user_id)
    preview["sourceImage"] = {
        "bucket": "learnloop-media",
        "objectKey": "test/preview-image.png",
        "contentType": "image/png",
        "sizeBytes": len(image_bytes),
    }
    storage.seed("learnloop-media", "test/preview-image.png", image_bytes)
    database["ingestion_previews"].seed(preview)

    response = await authenticated_client.get(f"/api/v1/ingestion-previews/{preview['_id']}/image")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == image_bytes


@pytest.mark.asyncio
async def test_stream_preview_image_returns_404_for_missing_source_image(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    user = await database["users"].find_one({"username": "student1"})
    assert user is not None
    user_id = user["_id"]

    preview = make_preview(user_id)
    preview["sourceImage"] = {}
    database["ingestion_previews"].seed(preview)

    response = await authenticated_client.get(f"/api/v1/ingestion-previews/{preview['_id']}/image")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Preview image not found"}
    }


@pytest.mark.asyncio
async def test_stream_preview_image_returns_404_for_other_user_preview(
    ingestion_app: FastAPI,
    authenticated_client: AsyncClient,
) -> None:
    database: FakeDatabase = ingestion_app.state.fake_database
    storage: FakeStorage = ingestion_app.state.fake_storage
    other_user_id = ObjectId()

    image_bytes = make_png_bytes()
    preview = make_preview(other_user_id)
    preview["sourceImage"] = {
        "bucket": "learnloop-media",
        "objectKey": "test/other-user-image.png",
        "contentType": "image/png",
    }
    storage.seed("learnloop-media", "test/other-user-image.png", image_bytes)
    database["ingestion_previews"].seed(preview)

    response = await authenticated_client.get(f"/api/v1/ingestion-previews/{preview['_id']}/image")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Preview not found"}
    }
