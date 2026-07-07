from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal, cast

import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.vlm.client import ClassificationResult, ExtractionResult
from app.main import create_app
from app.presentation import ingestion as ingestion_presentation
from app.infrastructure.storage.mongo import get_mongo_adapter
from app.presentation.deps import (
    create_english_ingestion_vlm_client,
    create_helper_vlm_client,
    create_math_ingestion_vlm_client,
    get_app_settings,
    get_database,
    get_grading_vlm_client,
    get_s3_storage,
)
from tests.conftest import FakeDatabase, FakeStorage


class FakeSession:
    async def with_transaction(self, callback: Any) -> Any:
        return await callback(self)


class FakeMongoAdapter:
    @asynccontextmanager
    async def start_session(self) -> AsyncIterator[FakeSession]:
        yield FakeSession()


class FakeGradingResult:
    def __init__(self, *, is_correct: bool, feedback: str = "", model: str = "fake-vlm") -> None:
        self.is_correct = is_correct
        self.feedback = feedback
        self.model = model
        self.raw_provider_response = {"isCorrect": is_correct, "feedback": feedback}


class FakeVLMClient:
    def __init__(self, model: str = "fake-vlm") -> None:
        self._model = model
        self.responses: list[Any] = []
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    @property
    def model(self) -> str:
        return self._model

    async def extract(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
    ) -> ExtractionResult:
        self.calls.append(
            {
                "method": "extract",
                "image_url": image_url,
                "image_base64": image_base64,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return cast(ExtractionResult, response)

    async def classify_subject(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
    ) -> ClassificationResult:
        self.calls.append(
            {
                "method": "classify_subject",
                "image_url": image_url,
                "image_base64": image_base64,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return cast(ClassificationResult, response)

    async def grade_short_answer(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
        problem_text: str,
        user_answer: str,
        correct_answer: str,
        subject: str = "math",
    ) -> FakeGradingResult:
        self.calls.append(
            {
                "image_url": image_url,
                "image_base64": image_base64,
                "problem_text": problem_text,
                "user_answer": user_answer,
                "correct_answer": correct_answer,
                "subject": subject,
                "method": "grade_short_answer",
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self) -> None:
        self.closed = True


def make_png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9l9iAAAAAASUVORK5CYII="
    )


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


def make_ready_preview(
    user_id: ObjectId,
    *,
    text: str,
    problem_type: str,
    correct_answer: str,
    tags: list[str],
    graph_dsl: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "status": "ready",
        "sourceImage": {
            "bucket": "learnloop-media",
            "objectKey": f"users/{user_id}/images/{ObjectId()}.png",
            "contentType": "image/png",
            "sizeBytes": 7,
            "sha256": "preview-sha",
            "uploadedAt": now,
        },
        "extraction": {
            "requestModel": "fake-vlm",
            "rawText": text,
            "rawProblemType": problem_type,
            "rawGraphDsl": graph_dsl,
        },
        "editableDraft": {
            "text": text,
            "problemType": problem_type,
            "graphDsl": graph_dsl,
            "correctAnswer": correct_answer,
            "tags": tags,
            "subject": "math",
        },
        "helperDetection": {
            "subject": "math",
            "confidence": 0.95,
            "reason": "Contains math notation",
            "model": "fake-vlm",
            "rawProviderResponse": None,
            "failureCode": None,
            "failureMessage": None,
        },
        "createdAt": now,
        "updatedAt": now,
        "expiresAt": now,
    }


@pytest_asyncio.fixture
async def exams_app() -> AsyncIterator[FastAPI]:
    """Minimal app fixture for exam history tests that don't need auth, ingestion, or VLM."""
    from app.presentation.deps import get_current_user

    application = create_app()
    database = FakeDatabase()
    adapter = FakeMongoAdapter()
    storage = FakeStorage()
    vlm = FakeVLMClient()
    settings = Settings(
        helper_vlm_model="gpt-4.1-mini",
        helper_vlm_timeout_seconds=1.0,
        math_ingestion_vlm_model="math-model",
        math_ingestion_vlm_timeout_seconds=1.0,
        english_ingestion_vlm_model="english-model",
        english_ingestion_vlm_timeout_seconds=1.0,
        problem_selection_min_age_days=0,
    )

    primary_user = {
        "_id": ObjectId(),
        "username": "primary-user",
        "status": "active",
    }

    application.state.fake_database = database
    application.state.fake_storage = storage
    application.state.fake_adapter = adapter
    application.state.fake_grading_vlm = vlm
    application.state.primary_user = primary_user
    application.state.sync_wait_seconds = 1.0

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_current_user] = lambda: primary_user
    application.dependency_overrides[get_s3_storage] = lambda: storage
    application.dependency_overrides[get_mongo_adapter] = lambda: adapter
    application.dependency_overrides[get_grading_vlm_client] = lambda: vlm

    yield application


@pytest_asyncio.fixture
async def exams_client(exams_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=exams_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest_asyncio.fixture
async def app() -> AsyncIterator[FastAPI]:
    application = create_app()
    database = FakeDatabase()
    storage = FakeStorage()
    adapter = FakeMongoAdapter()
    helper_vlm = FakeVLMClient(model="gpt-4.1-mini")
    math_ingestion_vlm = FakeVLMClient(model="math-model")
    english_ingestion_vlm = FakeVLMClient(model="english-model")
    grading_vlm = FakeVLMClient()
    settings = Settings(
        helper_vlm_model="gpt-4.1-mini",
        helper_vlm_timeout_seconds=1.0,
        math_ingestion_vlm_model="math-model",
        math_ingestion_vlm_timeout_seconds=1.0,
        english_ingestion_vlm_model="english-model",
        english_ingestion_vlm_timeout_seconds=1.0,
        problem_selection_min_age_days=0,
    )

    application.state.fake_database = database
    application.state.fake_storage = storage
    application.state.fake_adapter = adapter
    application.state.fake_helper_vlm = helper_vlm
    application.state.fake_math_ingestion_vlm = math_ingestion_vlm
    application.state.fake_english_ingestion_vlm = english_ingestion_vlm
    application.state.fake_grading_vlm = grading_vlm
    application.state.sync_wait_seconds = 1.0

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_s3_storage] = lambda: storage
    application.dependency_overrides[get_mongo_adapter] = lambda: adapter
    application.dependency_overrides[get_grading_vlm_client] = lambda: grading_vlm
    application.dependency_overrides[create_helper_vlm_client] = lambda: helper_vlm
    application.dependency_overrides[create_math_ingestion_vlm_client] = lambda: math_ingestion_vlm
    application.dependency_overrides[create_english_ingestion_vlm_client] = lambda: english_ingestion_vlm
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
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest_asyncio.fixture
async def register_and_login(
    app: FastAPI,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def _register_and_login(
        client: Any,
        *,
        username: str = "student1",
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

        user = await app.state.fake_database["users"].find_one({"username": username})
        assert user is not None
        return user

    return _register_and_login


@pytest_asyncio.fixture
async def create_problem_via_api(
    app: FastAPI,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def _create_problem_via_api(
        client: Any,
        *,
        user_id: ObjectId,
        text: str,
        problem_type: str,
        correct_answer: str,
        tags: list[str],
        graph_dsl: str | None = None,
        image_bytes: bytes = b"pngdata",
    ) -> dict[str, Any]:
        preview = make_ready_preview(
            user_id,
            text=text,
            problem_type=problem_type,
            correct_answer=correct_answer,
            tags=tags,
            graph_dsl=graph_dsl,
        )
        app.state.fake_database["ingestion_previews"].seed(preview)
        app.state.fake_storage.seed(
            preview["sourceImage"]["bucket"],
            preview["sourceImage"]["objectKey"],
            image_bytes,
        )

        response = await client.post(
            f"/api/v1/ingestion-previews/{preview['_id']}/confirm",
        )
        assert response.status_code == 201
        problem = response.json()["problem"]
        return problem

    return _create_problem_via_api


@pytest_asyncio.fixture
async def get_problem_document(app: FastAPI) -> Callable[[str], Awaitable[dict[str, Any] | None]]:
    async def _get_problem_document(problem_id: str) -> dict[str, Any] | None:
        return await app.state.fake_database["problems"].find_one({"_id": ObjectId(problem_id)})

    return _get_problem_document


@pytest_asyncio.fixture
async def find_exam_item() -> Callable[..., dict[str, Any]]:
    def _find_exam_item(
        exam: dict[str, Any],
        *,
        problem_id: str | None = None,
        problem_type: str | None = None,
    ) -> dict[str, Any]:
        for item in exam["items"]:
            matches_problem_id = problem_id is None or item["problemId"] == problem_id
            matches_problem_type = (
                problem_type is None or item["problem"]["problemType"] == problem_type
            )
            if matches_problem_id and matches_problem_type:
                return item
        raise AssertionError("Matching exam item not found")

    return _find_exam_item


@pytest_asyncio.fixture
async def database(app: FastAPI) -> FakeDatabase:
    return app.state.fake_database


@pytest_asyncio.fixture
async def storage(app: FastAPI) -> FakeStorage:
    return app.state.fake_storage


@pytest_asyncio.fixture
async def helper_vlm_client(app: FastAPI) -> FakeVLMClient:
    return app.state.fake_helper_vlm


@pytest_asyncio.fixture
async def math_ingestion_vlm_client(app: FastAPI) -> FakeVLMClient:
    return app.state.fake_math_ingestion_vlm


@pytest_asyncio.fixture
async def english_ingestion_vlm_client(app: FastAPI) -> FakeVLMClient:
    return app.state.fake_english_ingestion_vlm


@pytest_asyncio.fixture
async def vlm_client(app: FastAPI) -> FakeVLMClient:
    return app.state.fake_grading_vlm


@pytest_asyncio.fixture
async def png_bytes() -> bytes:
    return make_png_bytes()
