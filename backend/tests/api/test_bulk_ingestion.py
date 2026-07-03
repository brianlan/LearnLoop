from __future__ import annotations

import base64
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.config.settings import Settings
from app.infrastructure.storage.s3 import StorageObjectNotFoundError
from app.infrastructure.vlm.client import DetectionResult, ExtractionResult, ProblemBox
from app.main import create_app
from app.presentation.deps import (
    create_helper_vlm_client,
    get_app_settings,
    get_database,
    get_s3_storage,
)
from tests.api.conftest import FakeDatabase


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[tuple[str, str, str | None, bytes]] = []
        self.get_calls: list[tuple[str, str]] = []
        self._counter = 0

    def build_object_key(
        self, user_id: str, extension: str, *, category: str = "images"
    ) -> str:
        self._counter += 1
        return f"users/{user_id}/{category}/preview-{self._counter}{extension}"

    def put_object(self, bucket: str, object_key: str, payload: bytes, content_type: str | None) -> None:
        self.objects[(bucket, object_key)] = payload
        self.put_calls.append((bucket, object_key, content_type, payload))

    def get_object(self, bucket: str, object_key: str) -> bytes:
        self.get_calls.append((bucket, object_key))
        payload = self.objects.get((bucket, object_key))
        if payload is None:
            raise StorageObjectNotFoundError(object_key)
        return payload

    def seed(self, bucket: str, object_key: str, payload: bytes) -> None:
        self.objects[(bucket, object_key)] = payload


class FakeHelperVLMClient:
    def __init__(self, model: str = "fake-helper-vlm") -> None:
        self._model = model
        self.responses: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    @property
    def model(self) -> str:
        return self._model

    async def detect_problem_boxes(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
    ) -> DetectionResult:
        self.calls.append({"image_url": image_url, "image_base64": image_base64})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeIngestionVLMClient:
    def __init__(self, model: str = "fake-ingestion-vlm") -> None:
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
        self.calls.append({"image_url": image_url, "image_base64": image_base64})
        response = self.responses.pop(0)
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


def make_oversize_png_bytes(size: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * size


def make_valid_png_bytes(*, width: int = 10, height: int = 10) -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color="black").save(buffer, format="PNG")
    return buffer.getvalue()


def make_extraction_result(
    *,
    text: str = "What is 2+2?",
    problem_type: str = "short-answer",
    graph_dsl: str | None = None,
    model: str = "fake-ingestion-vlm",
) -> ExtractionResult:
    raw = {
        "text": text,
        "problemType": problem_type,
        "graphDsl": graph_dsl,
        "providerMetadata": {"provider": "fake"},
    }
    return ExtractionResult(
        request_type="ingestion",
        model=model,
        text=text,
        problem_type=problem_type,
        graph_dsl=graph_dsl,
        provider_metadata=raw["providerMetadata"],
        raw_provider_response=raw,
    )


def make_detection_result(
    *,
    subject: str = "math",
    boxes: list[ProblemBox] | None = None,
    model: str = "fake-helper-vlm",
) -> DetectionResult:
    boxes = boxes or []
    raw = {
        "subject": subject,
        "boxes": [box.model_dump() for box in boxes],
        "providerMetadata": {"provider": "fake"},
    }
    return DetectionResult(
        request_type="problem-box-detection",
        model=model,
        subject=subject,
        boxes=boxes,
        provider_metadata=raw["providerMetadata"],
        raw_provider_response=raw,
    )


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


async def create_committed_item(
    client: AsyncClient,
    helper_vlm: FakeHelperVLMClient,
    *,
    subject: str = "math",
) -> tuple[str, str, str]:
    batch_id, image_id = await create_batch_with_image(client, image_bytes=make_valid_png_bytes())
    helper_vlm.responses.append(
        make_detection_result(
            subject=subject,
            boxes=[ProblemBox(x=0, y=0, width=1, height=1)],
        )
    )
    detect_response = await client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/detect"
    )
    assert detect_response.status_code == 200

    commit_response = await client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/commit"
    )
    assert commit_response.status_code == 200
    item_id = commit_response.json()["batch"]["items"][0]["itemId"]
    return batch_id, image_id, item_id


async def create_batch_with_image(
    client: AsyncClient,
    image_bytes: bytes | None = None,
) -> tuple[str, str]:
    create_response = await client.post("/api/v1/ingestion-batches")
    assert create_response.status_code == 201
    batch_id = create_response.json()["batch"]["id"]

    image_bytes = image_bytes or make_png_bytes()
    upload_response = await client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("test.png", image_bytes, "image/png")},
    )
    assert upload_response.status_code == 201
    image_id = upload_response.json()["batch"]["images"][0]["imageId"]
    return batch_id, image_id


@pytest_asyncio.fixture
async def bulk_app() -> AsyncIterator[FastAPI]:
    application = create_app()
    database = FakeDatabase()
    storage = FakeStorage()
    settings = Settings(
        helper_vlm_model="gpt-4.1-mini",
        helper_vlm_timeout_seconds=1.0,
        math_ingestion_vlm_model="math-model",
        math_ingestion_vlm_timeout_seconds=1.0,
        english_ingestion_vlm_model="english-model",
        english_ingestion_vlm_timeout_seconds=1.0,
        bulk_ingestion_max_images=3,
        bulk_ingestion_max_image_bytes=200,
        bulk_ingestion_batch_ttl_seconds=3600,
        bulk_ingestion_extraction_worker_enabled=False,
        bulk_ingestion_extraction_poll_interval_seconds=3600,
    )

    helper_vlm = FakeHelperVLMClient(model=settings.helper_vlm_model)
    math_vlm = FakeIngestionVLMClient(model=settings.math_ingestion_vlm_model)
    english_vlm = FakeIngestionVLMClient(model=settings.english_ingestion_vlm_model)

    application.state.fake_database = database
    application.state.fake_storage = storage
    application.state.fake_helper_vlm = helper_vlm
    application.state.fake_math_ingestion_vlm = math_vlm
    application.state.fake_english_ingestion_vlm = english_vlm

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_s3_storage] = lambda: storage
    application.dependency_overrides[create_helper_vlm_client] = lambda: helper_vlm

    yield application


@pytest_asyncio.fixture
async def bulk_client(bulk_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=bulk_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture
async def authenticated_bulk_client(
    bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> AsyncIterator[AsyncClient]:
    await register_and_login(bulk_client, bulk_app, username="student1")
    yield bulk_client


@pytest_asyncio.fixture
async def helper_vlm(bulk_app: FastAPI) -> FakeHelperVLMClient:
    return bulk_app.state.fake_helper_vlm


@pytest_asyncio.fixture
async def math_ingestion_vlm(bulk_app: FastAPI) -> FakeIngestionVLMClient:
    return bulk_app.state.fake_math_ingestion_vlm


@pytest_asyncio.fixture
async def english_ingestion_vlm(bulk_app: FastAPI) -> FakeIngestionVLMClient:
    return bulk_app.state.fake_english_ingestion_vlm


@pytest.mark.asyncio
async def test_create_batch_for_authenticated_user(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")

    assert response.status_code == 201
    body = response.json()["batch"]
    assert body["status"] == "active"
    assert "id" in body
    assert "expiresAt" in body

    database: FakeDatabase = bulk_app.state.fake_database
    stored = await database["ingestion_batches"].find_one({"_id": ObjectId(body["id"])})
    assert stored is not None
    assert stored["status"] == "active"


@pytest.mark.asyncio
async def test_get_active_batch_returns_existing_unexpired_batch(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    response = await authenticated_bulk_client.get("/api/v1/ingestion-batches/active")

    assert response.status_code == 200
    assert response.json()["batch"]["id"] == batch_id


@pytest.mark.asyncio
async def test_get_active_batch_returns_404_when_no_batch_exists(
    authenticated_bulk_client: AsyncClient,
) -> None:
    response = await authenticated_bulk_client.get("/api/v1/ingestion-batches/active")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_upload_valid_images_returns_batch_with_image_metadata(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    image_bytes = make_png_bytes()
    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files=[
            ("images", ("first.png", image_bytes, "image/png")),
            ("images", ("second.png", image_bytes, "image/png")),
        ],
    )

    assert response.status_code == 201
    batch = response.json()["batch"]
    assert len(batch["images"]) == 2
    assert batch["images"][0]["order"] == 0
    assert batch["images"][1]["order"] == 1
    assert batch["images"][0]["sourceImage"]["contentType"] == "image/png"
    assert batch["images"][0]["sourceImage"]["sizeBytes"] == len(image_bytes)
    assert batch["images"][0]["status"] == "uploaded"

    storage: FakeStorage = bulk_app.state.fake_storage
    assert len(storage.put_calls) == 2
    assert storage.put_calls[0][3] == image_bytes


@pytest.mark.asyncio
async def test_upload_rejects_empty_image(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("empty.png", b"", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMAGE"

    storage: FakeStorage = bulk_app.state.fake_storage
    assert len(storage.put_calls) == 0


@pytest.mark.asyncio
async def test_upload_rejects_non_image_file(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("notes.txt", b"not-an-image", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMAGE"

    storage: FakeStorage = bulk_app.state.fake_storage
    assert len(storage.put_calls) == 0


@pytest.mark.asyncio
async def test_upload_rejects_oversize_image(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("huge.png", make_oversize_png_bytes(500), "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMAGE_TOO_LARGE"

    storage: FakeStorage = bulk_app.state.fake_storage
    assert len(storage.put_calls) == 0


@pytest.mark.asyncio
async def test_upload_rejects_exceeding_max_images(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]
    image_bytes = make_png_bytes()

    first_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files=[
            ("images", ("a.png", image_bytes, "image/png")),
            ("images", ("b.png", image_bytes, "image/png")),
        ],
    )
    assert first_response.status_code == 201

    second_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files=[
            ("images", ("c.png", image_bytes, "image/png")),
            ("images", ("d.png", image_bytes, "image/png")),
        ],
    )

    assert second_response.status_code == 409
    assert second_response.json()["error"]["code"] == "BATCH_IMAGE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_upload_rejects_expired_batch(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    database: FakeDatabase = bulk_app.state.fake_database
    await database["ingestion_batches"].update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": {"expiresAt": datetime.now(UTC) - timedelta(seconds=1)}},
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("test.png", make_png_bytes(), "image/png")},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BATCH_EXPIRED"


@pytest.mark.asyncio
async def test_upload_enforces_ownership(
    bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    owner = await register_and_login(bulk_client, bulk_app, username="owner")
    create_response = await bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    await register_and_login(bulk_client, bulk_app, username="other")
    response = await bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("test.png", make_png_bytes(), "image/png")},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"

    database: FakeDatabase = bulk_app.state.fake_database
    stored = await database["ingestion_batches"].find_one({"_id": ObjectId(batch_id)})
    assert stored is not None
    assert stored["userId"] == owner["_id"]


@pytest.mark.asyncio
async def test_get_active_batch_enforces_ownership(
    bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    await register_and_login(bulk_client, bulk_app, username="owner")
    await bulk_client.post("/api/v1/ingestion-batches")

    await register_and_login(bulk_client, bulk_app, username="other")
    response = await bulk_client.get("/api/v1/ingestion-batches/active")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_batch_routes_require_authentication(
    bulk_client: AsyncClient,
) -> None:
    response = await bulk_client.post("/api/v1/ingestion-batches")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"

    response = await bulk_client.get("/api/v1/ingestion-batches/active")
    assert response.status_code == 401

    response = await bulk_client.post(
        f"/api/v1/ingestion-batches/{ObjectId()}/images",
        files={"images": ("test.png", make_png_bytes(), "image/png")},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_detect_success_creates_boxes_and_subject(
    authenticated_bulk_client: AsyncClient,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)
    helper_vlm.responses.append(
        make_detection_result(
            subject="math",
            boxes=[
                ProblemBox(x=0, y=0, width=1, height=1),
            ],
        )
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/detect"
    )

    assert response.status_code == 200
    batch = response.json()["batch"]
    assert len(batch["images"]) == 1
    image = batch["images"][0]
    assert image["imageId"] == image_id
    assert image["status"] == "ready"
    assert image["subject"] == "math"
    assert len(image["boxes"]) == 1
    assert image["boxes"][0] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    assert image["detection"]["model"] == "fake-helper-vlm"
    assert image["sourceImage"]["width"] == 1
    assert image["sourceImage"]["height"] == 1


@pytest.mark.asyncio
async def test_detect_failure_and_retry_preserves_other_images(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    image_bytes = make_png_bytes()
    upload_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files=[
            ("images", ("first.png", image_bytes, "image/png")),
            ("images", ("second.png", image_bytes, "image/png")),
        ],
    )
    assert upload_response.status_code == 201
    first_image_id = upload_response.json()["batch"]["images"][0]["imageId"]
    second_image_id = upload_response.json()["batch"]["images"][1]["imageId"]

    helper_vlm.responses.append(Exception("detection failed"))
    helper_vlm.responses.append(
        make_detection_result(
            subject="english",
            boxes=[ProblemBox(x=0, y=0, width=1, height=1)],
        )
    )

    first_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{first_image_id}/detect"
    )
    assert first_response.status_code == 200
    assert first_response.json()["batch"]["images"][0]["status"] == "detect-failed"

    second_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{second_image_id}/detect"
    )
    assert second_response.status_code == 200
    assert second_response.json()["batch"]["images"][1]["status"] == "ready"
    assert second_response.json()["batch"]["images"][1]["subject"] == "english"

    helper_vlm.responses.append(
        make_detection_result(
            subject="math",
            boxes=[
                ProblemBox(x=0, y=0, width=1, height=1),
                ProblemBox(x=0, y=0, width=1, height=1),
            ],
        )
    )
    retry_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{first_image_id}/detect"
    )
    assert retry_response.status_code == 200
    batch = retry_response.json()["batch"]
    assert batch["images"][0]["status"] == "ready"
    assert batch["images"][0]["subject"] == "math"
    assert len(batch["images"][0]["boxes"]) == 2


@pytest.mark.asyncio
async def test_manual_box_entry_after_detection_failure(
    authenticated_bulk_client: AsyncClient,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)
    helper_vlm.responses.append(Exception("detection failed"))

    detect_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/detect"
    )
    assert detect_response.status_code == 200
    assert detect_response.json()["batch"]["images"][0]["status"] == "detect-failed"

    save_response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}",
        json={
            "subject": "math",
            "boxes": [{"x": 0, "y": 0, "width": 1, "height": 1}],
        },
    )
    assert save_response.status_code == 200
    image = save_response.json()["batch"]["images"][0]
    assert image["status"] == "ready"
    assert image["subject"] == "math"
    assert len(image["boxes"]) == 1


@pytest.mark.asyncio
async def test_save_boxes_persists_edits(
    authenticated_bulk_client: AsyncClient,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)
    helper_vlm.responses.append(
        make_detection_result(
            subject="math",
            boxes=[
                ProblemBox(x=0, y=0, width=1, height=1),
            ],
        )
    )
    await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/detect"
    )

    save_response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}",
        json={
            "subject": "english",
            "boxes": [
                {"x": 0, "y": 0, "width": 1, "height": 1},
                {"x": 0, "y": 0, "width": 1, "height": 1},
            ],
        },
    )
    assert save_response.status_code == 200
    image = save_response.json()["batch"]["images"][0]
    assert image["subject"] == "english"
    assert len(image["boxes"]) == 2


@pytest.mark.asyncio
async def test_subject_override_persists(
    authenticated_bulk_client: AsyncClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}",
        json={"subject": "english", "boxes": []},
    )

    assert response.status_code == 200
    assert response.json()["batch"]["images"][0]["subject"] == "english"


@pytest.mark.asyncio
async def test_invalid_box_zero_area_rejected(
    authenticated_bulk_client: AsyncClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}",
        json={"boxes": [{"x": 0, "y": 0, "width": 0, "height": 1}]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_BOXES"


@pytest.mark.asyncio
async def test_invalid_box_outside_bounds_rejected(
    authenticated_bulk_client: AsyncClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}",
        json={"boxes": [{"x": 0, "y": 0, "width": 2, "height": 1}]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_BOXES"


@pytest.mark.asyncio
async def test_commit_zero_boxes_creates_no_items(
    authenticated_bulk_client: AsyncClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)

    save_response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}",
        json={"subject": "math", "boxes": []},
    )
    assert save_response.status_code == 200

    commit_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/commit"
    )
    assert commit_response.status_code == 200
    batch = commit_response.json()["batch"]
    assert batch["images"][0]["status"] == "committed"
    assert batch["items"] == []


@pytest.mark.asyncio
async def test_commit_creates_items_in_reading_order(
    authenticated_bulk_client: AsyncClient,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)
    helper_vlm.responses.append(
        make_detection_result(
            subject="math",
            boxes=[
                ProblemBox(x=0, y=0, width=1, height=1),
                ProblemBox(x=0, y=0, width=1, height=1),
            ],
        )
    )
    await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/detect"
    )

    commit_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/commit"
    )
    assert commit_response.status_code == 200
    batch = commit_response.json()["batch"]
    assert len(batch["items"]) == 2
    assert batch["items"][0]["order"] == 0
    assert batch["items"][1]["order"] == 1
    assert batch["items"][0]["imageId"] == image_id


@pytest.mark.asyncio
async def test_commit_is_idempotent(
    authenticated_bulk_client: AsyncClient,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)
    helper_vlm.responses.append(
        make_detection_result(
            subject="math",
            boxes=[ProblemBox(x=0, y=0, width=1, height=1)],
        )
    )
    await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/detect"
    )

    first_commit = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/commit"
    )
    assert first_commit.status_code == 200
    assert len(first_commit.json()["batch"]["items"]) == 1

    second_commit = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/commit"
    )
    assert second_commit.status_code == 200
    assert len(second_commit.json()["batch"]["items"]) == 1


@pytest.mark.asyncio
async def test_commit_rejects_unready_image(
    authenticated_bulk_client: AsyncClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/commit"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IMAGE_NOT_READY"


@pytest.mark.asyncio
async def test_patch_rejects_committed_image(
    authenticated_bulk_client: AsyncClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)
    await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}",
        json={"subject": "math", "boxes": [{"x": 0, "y": 0, "width": 1, "height": 1}]},
    )
    await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/commit"
    )

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}",
        json={"boxes": [{"x": 0, "y": 0, "width": 1, "height": 1}]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IMAGE_ALREADY_COMMITTED"


@pytest.mark.asyncio
async def test_delete_image_removes_image_and_items(
    authenticated_bulk_client: AsyncClient,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, image_id = await create_batch_with_image(authenticated_bulk_client)
    helper_vlm.responses.append(
        make_detection_result(
            subject="math",
            boxes=[ProblemBox(x=0, y=0, width=1, height=1)],
        )
    )
    await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/detect"
    )
    await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/commit"
    )

    delete_response = await authenticated_bulk_client.delete(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}"
    )
    assert delete_response.status_code == 200
    batch = delete_response.json()["batch"]
    assert batch["images"] == []
    assert batch["items"] == []


@pytest.mark.asyncio
async def test_detect_requires_authentication(
    bulk_client: AsyncClient,
) -> None:
    response = await bulk_client.post(
        f"/api/v1/ingestion-batches/{ObjectId()}/images/image-1/detect"
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_save_boxes_requires_authentication(
    bulk_client: AsyncClient,
) -> None:
    response = await bulk_client.patch(
        f"/api/v1/ingestion-batches/{ObjectId()}/images/image-1",
        json={"boxes": []},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_commit_requires_authentication(
    bulk_client: AsyncClient,
) -> None:
    response = await bulk_client.post(
        f"/api/v1/ingestion-batches/{ObjectId()}/images/image-1/commit"
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_requires_authentication(
    bulk_client: AsyncClient,
) -> None:
    response = await bulk_client.delete(
        f"/api/v1/ingestion-batches/{ObjectId()}/images/image-1"
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_start_extraction_returns_202(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, _image_id, _item_id = await create_committed_item(
        authenticated_bulk_client, helper_vlm
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/extract"
    )

    assert response.status_code == 202
    assert response.json()["batchId"] == batch_id


@pytest.mark.asyncio
async def test_extraction_populates_draft_after_worker_runs(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_committed_item(
        authenticated_bulk_client, helper_vlm, subject="math"
    )

    extract_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/extract"
    )
    assert extract_response.status_code == 202

    database: FakeDatabase = bulk_app.state.fake_database
    storage: FakeStorage = bulk_app.state.fake_storage
    batch = await database["ingestion_batches"].find_one({"_id": ObjectId(batch_id)})
    assert batch is not None
    item = batch["items"][0]

    math_ingestion_vlm.responses.append(
        make_extraction_result(text="Extracted math text", model="math-model")
    )

    from app.infrastructure.worker.extraction_worker import process_item
    from app.infrastructure.config.settings import Settings as AppSettings

    settings = AppSettings(s3_bucket="learnloop-media")
    await process_item(
        item,
        batch,
        database,
        storage,
        math_ingestion_vlm,
        bulk_app.state.fake_english_ingestion_vlm,
        settings,
    )

    active_response = await authenticated_bulk_client.get("/api/v1/ingestion-batches/active")
    assert active_response.status_code == 200
    items = active_response.json()["batch"]["items"]
    assert len(items) == 1
    assert items[0]["itemId"] == item_id
    assert items[0]["status"] == "ready"
    assert items[0]["draft"]["text"] == "Extracted math text"
    assert items[0]["extraction"]["success"] is True


@pytest.mark.asyncio
async def test_retry_item_resets_failed_item(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_committed_item(
        authenticated_bulk_client, helper_vlm
    )
    database: FakeDatabase = bulk_app.state.fake_database
    await database["ingestion_batches"].update_one(
        {"_id": ObjectId(batch_id), "items.itemId": item_id},
        {
            "$set": {
                "items.$.status": "failed",
                "items.$.leaseUntil": None,
            }
        },
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/retry"
    )

    assert response.status_code == 200
    assert response.json()["batch"]["items"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_retry_item_rejects_active_item(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_committed_item(
        authenticated_bulk_client, helper_vlm
    )
    database: FakeDatabase = bulk_app.state.fake_database
    await database["ingestion_batches"].update_one(
        {"_id": ObjectId(batch_id), "items.itemId": item_id},
        {
            "$set": {
                "items.$.status": "extracting",
                "items.$.leaseUntil": datetime.now(UTC) + timedelta(seconds=60),
            }
        },
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/retry"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ITEM_NOT_RETRYABLE"


@pytest.mark.asyncio
async def test_extract_requires_authentication(
    bulk_client: AsyncClient,
) -> None:
    response = await bulk_client.post(
        f"/api/v1/ingestion-batches/{ObjectId()}/extract"
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_retry_requires_authentication(
    bulk_client: AsyncClient,
) -> None:
    response = await bulk_client.post(
        f"/api/v1/ingestion-batches/{ObjectId()}/items/item-1/retry"
    )
    assert response.status_code == 401


async def create_ready_item(
    client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
    *,
    subject: str = "math",
) -> tuple[str, str, str]:
    batch_id, image_id, item_id = await create_committed_item(
        client, helper_vlm, subject=subject
    )

    database: FakeDatabase = bulk_app.state.fake_database
    storage: FakeStorage = bulk_app.state.fake_storage
    batch = await database["ingestion_batches"].find_one({"_id": ObjectId(batch_id)})
    assert batch is not None

    math_ingestion_vlm.responses.append(
        make_extraction_result(text=f"Extracted {subject} text", model="math-model")
    )

    from app.infrastructure.worker.extraction_worker import process_item
    from app.infrastructure.config.settings import Settings as AppSettings

    settings = AppSettings(s3_bucket="learnloop-media")
    await process_item(
        batch["items"][0],
        batch,
        database,
        storage,
        math_ingestion_vlm,
        bulk_app.state.fake_english_ingestion_vlm,
        settings,
    )
    return batch_id, image_id, item_id


@pytest.mark.asyncio
async def test_get_batch_detail_returns_review_state_with_media_urls(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, image_id, item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    response = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}"
    )
    assert response.status_code == 200
    batch = response.json()["batch"]
    assert batch["id"] == batch_id
    assert len(batch["images"]) == 1
    assert batch["images"][0]["sourceImage"]["mediaUrl"] == (
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/source"
    )
    assert len(batch["items"]) == 1
    item = batch["items"][0]
    assert item["itemId"] == item_id
    assert item["status"] == "ready"
    assert item["draft"]["text"] == "Extracted math text"
    assert item["crop"]["mediaUrl"] == (
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/crop"
    )


@pytest.mark.asyncio
async def test_get_batch_detail_includes_deleted_items(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, image_id, _item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    await authenticated_bulk_client.delete(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}"
    )

    response = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}"
    )
    assert response.status_code == 200
    batch = response.json()["batch"]
    assert len(batch["images"]) == 1
    assert batch["images"][0]["status"] == "deleted"
    assert len(batch["items"]) == 1
    assert batch["items"][0]["status"] == "deleted"


@pytest.mark.asyncio
async def test_update_item_draft_persists_all_editable_fields(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={
            "text": "New text",
            "problemType": "single-choice",
            "graphDsl": "graph",
            "correctAnswer": "A",
            "tags": ["tag-a", " tag-b ", "tag-a"],
            "subject": "english",
        },
    )
    assert response.status_code == 200
    item = response.json()["batch"]["items"][0]
    assert item["draft"]["text"] == "New text"
    assert item["draft"]["problemType"] == "single-choice"
    assert item["draft"]["graphDsl"] == "graph"
    assert item["draft"]["correctAnswer"] == "A"
    assert item["draft"]["tags"] == ["tag-a", "tag-b"]
    assert item["draft"]["subject"] == "english"


@pytest.mark.asyncio
async def test_update_item_draft_omitted_fields_preserved(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"text": "First"},
    )
    await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"correctAnswer": "Second"},
    )

    response = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}"
    )
    item = response.json()["batch"]["items"][0]
    assert item["draft"]["text"] == "First"
    assert item["draft"]["correctAnswer"] == "Second"


@pytest.mark.asyncio
async def test_update_item_draft_rejects_invalid_problem_type(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"problemType": "invalid"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_item_draft_rejects_expired_batch(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    database: FakeDatabase = bulk_app.state.fake_database
    await database["ingestion_batches"].update_one(
        {"_id": ObjectId(batch_id)},
        {"$set": {"expiresAt": datetime.now(UTC) - timedelta(seconds=1)}},
    )

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"text": "New"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BATCH_EXPIRED"


@pytest.mark.asyncio
async def test_delete_item_marks_deleted_and_keeps_in_review_state(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    response = await authenticated_bulk_client.delete(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}"
    )
    assert response.status_code == 200
    item = response.json()["batch"]["items"][0]
    assert item["itemId"] == item_id
    assert item["status"] == "deleted"

    detail = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}"
    )
    assert detail.json()["batch"]["items"][0]["status"] == "deleted"


@pytest.mark.asyncio
async def test_undo_delete_item_restores_previous_status(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    await authenticated_bulk_client.delete(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}"
    )
    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/undo-delete"
    )
    assert response.status_code == 200
    item = response.json()["batch"]["items"][0]
    assert item["status"] == "ready"


@pytest.mark.asyncio
async def test_undo_delete_item_rejects_non_deleted(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/undo-delete"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ITEM_NOT_DELETED"


@pytest.mark.asyncio
async def test_stream_source_image_returns_owned_image(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, image_id, _item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    response = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/source"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 0


@pytest.mark.asyncio
async def test_stream_item_crop_returns_owned_crop(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    response = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/crop"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 0


@pytest.mark.asyncio
async def test_stream_source_image_rejects_cross_user(
    bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    await register_and_login(bulk_client, bulk_app, username="owner")
    batch_id, image_id, _item_id = await create_ready_item(
        bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    await register_and_login(bulk_client, bulk_app, username="other")
    response = await bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/source"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stream_crop_rejects_missing_crop(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_committed_item(
        authenticated_bulk_client, helper_vlm
    )

    response = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/crop"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CROP_NOT_FOUND"


@pytest.mark.asyncio
async def test_review_routes_require_authentication(
    bulk_client: AsyncClient,
) -> None:
    batch_id = str(ObjectId())
    item_id = "item-1"
    image_id = "image-1"

    assert (
        await bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    ).status_code == 401
    assert (
        await bulk_client.patch(
            f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}", json={}
        )
    ).status_code == 401
    assert (
        await bulk_client.delete(
            f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}"
        )
    ).status_code == 401
    assert (
        await bulk_client.post(
            f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/undo-delete"
        )
    ).status_code == 401
    assert (
        await bulk_client.get(
            f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/source"
        )
    ).status_code == 401
    assert (
        await bulk_client.get(
            f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/crop"
        )
    ).status_code == 401
