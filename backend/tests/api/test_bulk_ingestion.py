from __future__ import annotations

import asyncio
import base64
import contextlib
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
from app.infrastructure.storage.mongo import get_mongo_adapter
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


class FakeSession:
    async def with_transaction(self, callback: Any) -> Any:
        return await callback(self)


class FakeMongoAdapter:
    """Runs transaction callbacks directly against the FakeDatabase."""

    @contextlib.asynccontextmanager
    async def start_session(self):  # noqa: ANN201
        yield FakeSession()


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


def make_pdf_bytes(num_pages: int = 1) -> bytes:
    """Create a minimal multi-page PDF whose rendered pages fit the test image byte limit."""
    import pymupdf

    document = pymupdf.open()
    for _ in range(num_pages):
        document.new_page(width=1, height=1)
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
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
    application.dependency_overrides[get_mongo_adapter] = lambda: FakeMongoAdapter()
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
async def test_upload_pdf_creates_one_image_per_page(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("doc.pdf", make_pdf_bytes(num_pages=2), "application/pdf")},
    )

    assert response.status_code == 201
    images = response.json()["batch"]["images"]
    assert len(images) == 2
    for index, image in enumerate(images):
        assert image["order"] == index
        assert image["status"] == "uploaded"
        assert image["sourceImage"]["contentType"] == "image/png"
        assert image["sourceImage"]["width"] is not None
        assert image["sourceImage"]["height"] is not None

    storage: FakeStorage = bulk_app.state.fake_storage
    assert len(storage.put_calls) == 2


@pytest.mark.asyncio
async def test_upload_multiple_pdfs_preserves_file_and_page_order(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    # The example case from the issue: two PDFs with 2 and 3 pages = 5 images.
    # The default test fixture limits batches to 3 images, so override it.
    base_settings = bulk_app.dependency_overrides[get_app_settings]()
    bulk_app.dependency_overrides[get_app_settings] = lambda: base_settings.model_copy(
        update={"bulk_ingestion_max_images": 10}
    )

    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files=[
            ("images", ("first.pdf", make_pdf_bytes(num_pages=2), "application/pdf")),
            ("images", ("second.pdf", make_pdf_bytes(num_pages=3), "application/pdf")),
        ],
    )

    assert response.status_code == 201
    images = response.json()["batch"]["images"]
    assert len(images) == 5
    assert [image["order"] for image in images] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_upload_mixed_image_and_pdf_preserves_order(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    image_bytes = make_png_bytes()
    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files=[
            ("images", ("photo.png", image_bytes, "image/png")),
            ("images", ("doc.pdf", make_pdf_bytes(num_pages=2), "application/pdf")),
        ],
    )

    assert response.status_code == 201
    images = response.json()["batch"]["images"]
    assert len(images) == 3
    assert images[0]["sourceImage"]["contentType"] == "image/png"
    assert images[1]["sourceImage"]["contentType"] == "image/png"
    assert images[2]["sourceImage"]["contentType"] == "image/png"
    assert [image["order"] for image in images] == [0, 1, 2]


@pytest.mark.asyncio
async def test_upload_pdf_page_count_included_in_image_limit(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    image_bytes = make_png_bytes()
    first_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("photo.png", image_bytes, "image/png")},
    )
    assert first_response.status_code == 201

    second_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("doc.pdf", make_pdf_bytes(num_pages=3), "application/pdf")},
    )

    assert second_response.status_code == 409
    assert second_response.json()["error"]["code"] == "BATCH_IMAGE_LIMIT_EXCEEDED"

    storage: FakeStorage = bulk_app.state.fake_storage
    assert len(storage.put_calls) == 1


@pytest.mark.asyncio
async def test_upload_rejects_empty_pdf(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("empty.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PDF"

    storage: FakeStorage = bulk_app.state.fake_storage
    assert len(storage.put_calls) == 0


@pytest.mark.asyncio
async def test_upload_rejects_invalid_pdf(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("corrupt.pdf", b"not a pdf", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PDF"

    storage: FakeStorage = bulk_app.state.fake_storage
    assert len(storage.put_calls) == 0


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
    assert image["boxes"][0] == {
        "boxId": "box-1",
        "x": 0.0,
        "y": 0.0,
        "width": 1.0,
        "height": 1.0,
    }
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
    assert image["boxes"][0]["boxId"] == "box-1"


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
                {"boxId": "edited-1", "x": 0, "y": 0, "width": 1, "height": 1},
                {"boxId": "edited-2", "x": 0, "y": 0, "width": 1, "height": 1},
            ],
        },
    )
    assert save_response.status_code == 200
    image = save_response.json()["batch"]["images"][0]
    assert image["subject"] == "english"
    assert len(image["boxes"]) == 2
    assert [box["boxId"] for box in image["boxes"]] == ["edited-1", "edited-2"]


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

    from app.infrastructure.ingestion.repository import claim_item
    claimed = await claim_item(
        database, ObjectId(batch_id), item["itemId"], batch["userId"],
        lease_timeout_seconds=60, now=datetime.now(UTC),
    )
    assert claimed is not None
    item = claimed

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
async def test_retry_item_resets_submit_failed_item(
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
                "items.$.status": "submit-failed",
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

    from app.infrastructure.ingestion.repository import claim_item
    claimed = await claim_item(
        database, ObjectId(batch_id), batch["items"][0]["itemId"], batch["userId"],
        lease_timeout_seconds=60, now=datetime.now(UTC),
    )
    assert claimed is not None

    math_ingestion_vlm.responses.append(
        make_extraction_result(text=f"Extracted {subject} text", model="math-model")
    )

    from app.infrastructure.worker.extraction_worker import process_item
    from app.infrastructure.config.settings import Settings as AppSettings

    settings = AppSettings(s3_bucket="learnloop-media")
    await process_item(
        claimed,
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
async def test_stream_source_image_returns_404_for_missing_metadata(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    database: FakeDatabase = bulk_app.state.fake_database
    batch_id, image_id, _item_id = await create_committed_item(
        authenticated_bulk_client, helper_vlm
    )

    await database["ingestion_batches"].update_one(
        {"_id": ObjectId(batch_id), "images.imageId": image_id},
        {"$set": {"images.$.sourceImage": {}}},
    )

    response = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}/images/{image_id}/source"
    )
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Source image not found"}
    }


@pytest.mark.asyncio
async def test_stream_crop_returns_404_when_storage_object_missing(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    database: FakeDatabase = bulk_app.state.fake_database
    batch_id, _image_id, item_id = await create_committed_item(
        authenticated_bulk_client, helper_vlm
    )

    await database["ingestion_batches"].update_one(
        {"_id": ObjectId(batch_id), "items.itemId": item_id},
        {"$set": {"items.$.crop": {
            "bucket": "test-bucket",
            "objectKey": "missing-crop.png",
            "contentType": "image/png",
        }}},
    )

    response = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/crop"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["error"]["message"] == "Crop image not found"


async def create_ready_item_with_draft(
    client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
    *,
    correct_answer: str = "4",
    subject: str = "math",
) -> tuple[str, str, str]:
    batch_id, image_id, item_id = await create_ready_item(
        client, bulk_app, helper_vlm, math_ingestion_vlm, subject=subject
    )
    response = await client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"correctAnswer": correct_answer},
    )
    assert response.status_code == 200
    return batch_id, image_id, item_id


async def create_two_ready_items(
    client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> tuple[str, list[str]]:
    create_response = await client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]

    image_bytes = make_valid_png_bytes()
    upload_response = await client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("test.png", image_bytes, "image/png")},
    )
    assert upload_response.status_code == 201
    image_id = upload_response.json()["batch"]["images"][0]["imageId"]

    helper_vlm.responses.append(
        make_detection_result(
            subject="math",
            boxes=[
                ProblemBox(x=0, y=0, width=1, height=1),
                ProblemBox(x=0, y=0, width=1, height=1),
            ],
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
    item_ids = [item["itemId"] for item in commit_response.json()["batch"]["items"]]

    database: FakeDatabase = bulk_app.state.fake_database
    storage: FakeStorage = bulk_app.state.fake_storage
    batch = await database["ingestion_batches"].find_one({"_id": ObjectId(batch_id)})
    assert batch is not None

    from app.infrastructure.worker.extraction_worker import process_item
    from app.infrastructure.config.settings import Settings as AppSettings
    from app.infrastructure.ingestion.repository import claim_item

    settings = AppSettings(s3_bucket="learnloop-media")
    for _ in item_ids:
        math_ingestion_vlm.responses.append(
            make_extraction_result(text="Extracted text", model="math-model")
        )

    for item in batch["items"]:
        claimed = await claim_item(
            database, ObjectId(batch_id), item["itemId"], batch["userId"],
            lease_timeout_seconds=60, now=datetime.now(UTC),
        )
        assert claimed is not None
        await process_item(
            claimed,
            batch,
            database,
            storage,
            math_ingestion_vlm,
            bulk_app.state.fake_english_ingestion_vlm,
            settings,
        )

    return batch_id, item_ids


@pytest.mark.asyncio
async def test_submit_batch_creates_problems_and_solution_tasks(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item_with_draft(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )

    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["batchId"] == batch_id
    assert summary["status"] == "completed"
    assert len(summary["items"]) == 1
    assert summary["items"][0]["itemId"] == item_id
    assert summary["items"][0]["status"] == "submitted"
    assert summary["items"][0]["submittedProblemId"] is not None
    assert summary["items"][0]["failureCode"] is None

    database: FakeDatabase = bulk_app.state.fake_database
    problems = database["problems"]._documents
    assert len(problems) == 1
    assert problems[0]["origin"] == {
        "batchId": batch_id,
        "imageId": _image_id,
        "itemId": item_id,
    }

    tasks = database["solution_generation_tasks"]._documents
    assert len(tasks) == 1
    assert tasks[0]["problem_id"] == str(problems[0]["_id"])


@pytest.mark.asyncio
async def test_submit_batch_gate_blocks_missing_fields(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item(
        authenticated_bulk_client, bulk_app, helper_vlm, math_ingestion_vlm
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )

    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["status"] == "active"
    assert summary["items"][0]["status"] == "submit-failed"
    assert summary["items"][0]["failureCode"] == "MISSING_REQUIRED_FIELD"

    database: FakeDatabase = bulk_app.state.fake_database
    assert len(database["problems"]._documents) == 0


@pytest.mark.asyncio
async def test_submit_batch_gate_skips_non_ready_items(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, _image_id, _item_id = await create_committed_item(
        authenticated_bulk_client, helper_vlm
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )

    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["status"] == "active"
    assert summary["items"] == []

    database: FakeDatabase = bulk_app.state.fake_database
    assert len(database["problems"]._documents) == 0


@pytest.mark.asyncio
async def test_submit_batch_best_effort_partial_failure(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, item_ids = await create_two_ready_items(
        authenticated_bulk_client, bulk_app, helper_vlm, math_ingestion_vlm
    )

    await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_ids[0]}",
        json={"correctAnswer": "A"},
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )

    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["status"] == "active"
    assert len(summary["items"]) == 2

    by_item = {item["itemId"]: item for item in summary["items"]}
    assert by_item[item_ids[0]]["status"] == "submitted"
    assert by_item[item_ids[0]]["submittedProblemId"] is not None
    assert by_item[item_ids[1]]["status"] == "submit-failed"

    database: FakeDatabase = bulk_app.state.fake_database
    assert len(database["problems"]._documents) == 1


@pytest.mark.asyncio
async def test_submit_batch_is_idempotent(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item_with_draft(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
    )

    first = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )
    assert first.status_code == 200
    first_problem_id = first.json()["submitSummary"]["items"][0]["submittedProblemId"]

    second = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )
    assert second.status_code == 200
    second_problem_id = second.json()["submitSummary"]["items"][0]["submittedProblemId"]

    assert first_problem_id == second_problem_id

    database: FakeDatabase = bulk_app.state.fake_database
    assert len(database["problems"]._documents) == 1
    assert len(database["solution_generation_tasks"]._documents) == 1


@pytest.mark.asyncio
async def test_submit_batch_marks_batch_completed_when_all_submitted(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, item_ids = await create_two_ready_items(
        authenticated_bulk_client, bulk_app, helper_vlm, math_ingestion_vlm
    )

    for item_id in item_ids:
        response = await authenticated_bulk_client.patch(
            f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
            json={"correctAnswer": "4"},
        )
        assert response.status_code == 200

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )

    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["status"] == "completed"
    assert all(item["status"] == "submitted" for item in summary["items"])


@pytest.mark.asyncio
async def test_submit_batch_completes_after_remaining_items_deleted(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, item_ids = await create_two_ready_items(
        authenticated_bulk_client, bulk_app, helper_vlm, math_ingestion_vlm
    )

    await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_ids[0]}",
        json={"correctAnswer": "4"},
    )
    await authenticated_bulk_client.delete(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_ids[1]}"
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )

    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["status"] == "completed"


@pytest.mark.asyncio
async def test_full_batch_lifecycle_through_completion(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    math_ingestion_vlm: FakeIngestionVLMClient,
) -> None:
    batch_id, _image_id, item_id = await create_ready_item_with_draft(
        authenticated_bulk_client,
        bulk_app,
        helper_vlm,
        math_ingestion_vlm,
        correct_answer="A",
    )

    submit_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )
    assert submit_response.status_code == 200
    assert submit_response.json()["submitSummary"]["status"] == "completed"

    detail_response = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}"
    )
    assert detail_response.status_code == 200
    batch = detail_response.json()["batch"]
    assert batch["status"] == "completed"
    assert batch["items"][0]["status"] == "submitted"
    assert batch["items"][0]["submit"]["submittedProblemId"] is not None


@pytest.mark.asyncio
async def test_submit_requires_authentication(
    bulk_client: AsyncClient,
) -> None:
    response = await bulk_client.post(
        f"/api/v1/ingestion-batches/{ObjectId()}/submit"
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


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


# ---------------------------------------------------------------------------
# Variant ingestion lifecycle (issue #613)
# ---------------------------------------------------------------------------

from app.infrastructure.ingestion.repository import (  # noqa: E402
    INGESTION_BATCHES_COLLECTION,
    claim_item,
    claim_variation_work,
    create_batch,
    renew_submit_reservation,
    request_variation_generation,
    request_variation_revalidation,
    reserve_items_for_original_submit,
    save_variation_candidate_checkpoint,
    save_variation_result,
    submit_items_and_complete_batch,
)


VARIANT_ORIGINAL = {
    "text": "What is 2+2?",
    "problemType": "short-answer",
    "graphDsl": None,
    "correctAnswer": "4",
    "subject": "math",
}
VARIANT_CANDIDATE = {
    "text": "What is 3+5?",
    "problemType": "short-answer",
    "graphDsl": None,
    "correctAnswer": "8",
    "subject": "math",
    "generator": {"provider": "fake", "model": "gen-model"},
}


def _enable_variant_profiles(bulk_app: FastAPI, *, include_second: bool = True) -> None:
    base = bulk_app.dependency_overrides[get_app_settings]()
    configured = base.model_copy(
        update={
            "variant_generator_vlm_endpoint": "https://variant-generator.test/api",
            "variant_generator_vlm_model": "gen-model",
            "variant_generator_vlm_api_key": "sk-gen",
            "variant_validator_vlm_endpoint": "https://variant-validator.test/api",
            "variant_validator_vlm_model": "val-model",
            "variant_validator_vlm_api_key": "sk-val",
            # Optional second validator: the unconfigured profile keeps the
            # library defaults, which the builder reads as fully unset.
            **(
                {
                    "variant_validator2_vlm_endpoint": "https://variant-validator2.test/api",
                    "variant_validator2_vlm_model": "val2-model",
                    "variant_validator2_vlm_api_key": "sk-val2",
                }
                if include_second
                else {}
            ),
            "helper_vlm_endpoint": "https://helper.test/api",
            "helper_vlm_model": "helper-model",
            "helper_vlm_api_key": "sk-helper",
        }
    )
    bulk_app.dependency_overrides[get_app_settings] = lambda: configured


async def _create_variant_batch(
    client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    *,
    mode: str = "data-only",
    username: str = "student1",
) -> tuple[str, str, str]:
    """A variant-mode batch with one committed, extraction-ready item."""
    create_response = await client.post(
        "/api/v1/ingestion-batches", json={"ingestionMode": mode}
    )
    assert create_response.status_code == 201
    batch = create_response.json()["batch"]
    assert batch["ingestionMode"] == mode
    batch_id = batch["id"]

    image_bytes = make_valid_png_bytes()
    upload_response = await client.post(
        f"/api/v1/ingestion-batches/{batch_id}/images",
        files={"images": ("test.png", image_bytes, "image/png")},
    )
    assert upload_response.status_code == 201
    image_id = upload_response.json()["batch"]["images"][0]["imageId"]

    helper_vlm.responses.append(
        make_detection_result(subject="math", boxes=[ProblemBox(x=0, y=0, width=1, height=1)])
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

    # Extraction produces the reviewed draft the variant flow starts from.
    database = bulk_app.state.fake_database
    user = await database["users"].find_one({"username": username})
    math_vlm = bulk_app.state.fake_math_ingestion_vlm
    math_vlm.responses.append(make_extraction_result())
    extract_response = await client.post(f"/api/v1/ingestion-batches/{batch_id}/extract")
    assert extract_response.status_code == 202

    from app.infrastructure.worker.extraction_worker import process_item

    claimed = await claim_item(
        database, ObjectId(batch_id), item_id, user["_id"],
        lease_timeout_seconds=60, now=datetime.now(UTC),
    )
    assert claimed is not None
    stored_batch = await database[INGESTION_BATCHES_COLLECTION].find_one(
        {"_id": ObjectId(batch_id)}
    )
    await process_item(
        claimed,
        stored_batch,
        database,
        bulk_app.state.fake_storage,
        math_vlm,
        bulk_app.state.fake_english_ingestion_vlm,
        Settings(s3_bucket="learnloop-media"),
    )
    return batch_id, image_id, item_id


async def _drive_to_ready_candidate(
    bulk_app: FastAPI,
    user_id: Any,
    batch_id: str,
    item_id: str,
) -> None:
    """Drive a queued item to a ready validated candidate via repo writers."""
    database = bulk_app.state.fake_database
    now = datetime.now(UTC)
    batch_object_id = ObjectId(batch_id)
    await request_variation_generation(
        database, batch_object_id, user_id, item_id,
        original=dict(VARIANT_ORIGINAL), expected_revision=0, now=now,
    )
    claimed = await claim_variation_work(
        database, batch_object_id, user_id, item_id,
        lease_timeout_seconds=300, now=now,
    )
    assert claimed is not None
    token = claimed["variation"]["claimToken"]
    assert await save_variation_candidate_checkpoint(
        database, batch_object_id, user_id, item_id,
        token=token, claimed_revision=1,
        candidate=dict(VARIANT_CANDIDATE), now=now,
    )
    assert await save_variation_result(
        database, batch_object_id, user_id, item_id,
        token=token, claimed_revision=1, verdict="pass",
        validation={"verdict": "pass", "failures": [], "reports": []},
        now=now,
    )


def _variation_of(batch_body: dict[str, Any], item_id: str) -> dict[str, Any]:
    item = next(i for i in batch_body["batch"]["items"] if i["itemId"] == item_id)
    return item


async def _drive_to_needs_validation(
    bulk_app: FastAPI,
    user_id: Any,
    batch_id: str,
    item_id: str,
) -> None:
    """Drive a ready candidate into needs-validation with a stale PASS.

    One semantic candidate edit lands at contentRevision 2, keeping the
    stored pass report visible but stale (the #648 edit contract).
    """
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)
    from app.infrastructure.ingestion.repository import edit_variation_candidate

    await edit_variation_candidate(
        bulk_app.state.fake_database,
        ObjectId(batch_id),
        user_id,
        item_id,
        candidate_update={"text": "What is 4+4?"},
        tags=None,
        expected_revision=1,
        now=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_create_batch_defaults_to_original_mode(
    authenticated_bulk_client: AsyncClient,
) -> None:
    response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    assert response.status_code == 201
    assert response.json()["batch"]["ingestionMode"] == "original"


@pytest.mark.asyncio
async def test_variant_generate_rejects_original_mode_batch(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
) -> None:
    _enable_variant_profiles(bulk_app)
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    batch_id = create_response.json()["batch"]["id"]
    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/some-item/variation/generate",
        json={"expectedRevision": 0, "original": VARIANT_ORIGINAL},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INGESTION_MODE_MISMATCH"


@pytest.mark.asyncio
async def test_variant_generate_requires_configured_profiles(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    # Default fixture settings keep the reserved .invalid endpoints.
    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/generate",
        json={"expectedRevision": 0, "original": VARIANT_ORIGINAL},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "vlm-profile-invalid"
    # Nothing was queued.
    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    item = _variation_of(detail.json(), item_id)
    assert item["variation"]["status"] == "not-requested"
    assert item["variation"]["generationCount"] == 0


@pytest.mark.asyncio
async def test_variant_generate_accepted_with_second_validator_unconfigured(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app, include_second=False)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/generate",
        json={"expectedRevision": 0, "original": VARIANT_ORIGINAL},
    )
    assert response.status_code == 202
    variation = _variation_of(response.json(), item_id)["variation"]
    assert variation["status"] == "queued"


@pytest.mark.asyncio
async def test_variant_revalidate_accepted_with_second_validator_unconfigured(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app, include_second=False)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (
        await bulk_app.state.fake_database["users"].find_one({"username": "student1"})
    )["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)
    edit = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "text": "What is 5+5?"},
    )
    assert edit.status_code == 200

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/revalidate",
        json={"expectedRevision": 2},
    )
    assert response.status_code == 202
    assert _variation_of(response.json(), item_id)["variation"]["status"] == "queued"


@pytest.mark.asyncio
async def test_variant_generate_partial_second_validator_rejected(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    """A half-configured validator2 is a config error, not single-validator mode."""
    _enable_variant_profiles(bulk_app, include_second=False)
    base = bulk_app.dependency_overrides[get_app_settings]()
    bulk_app.dependency_overrides[get_app_settings] = lambda: base.model_copy(
        update={"variant_validator2_vlm_model": "val2-model"}
    )
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/generate",
        json={"expectedRevision": 0, "original": VARIANT_ORIGINAL},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "vlm-profile-invalid"


@pytest.mark.asyncio
async def test_variant_generate_confirms_source_and_queues(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/generate",
        json={
            "expectedRevision": 0,
            "original": dict(VARIANT_ORIGINAL, text="What is 10+10?"),
        },
    )
    assert response.status_code == 202
    item = _variation_of(response.json(), item_id)
    variation = item["variation"]
    assert variation["status"] == "queued"
    assert variation["generationCount"] == 1
    assert variation["original"]["text"] == "What is 10+10?"
    assert variation["candidate"] is None
    assert variation["validatedRevision"] is None
    assert "claimToken" not in variation
    assert "leaseUntil" not in variation
    # The confirmed draft replaces the extracted source text.
    assert item["draft"]["text"] == "What is 10+10?"
    assert item["contentRevision"] == 1


@pytest.mark.asyncio
async def test_variant_generate_missing_required_fields_blocked(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    for missing in ("text", "problemType", "correctAnswer"):
        payload = {k: v for k, v in VARIANT_ORIGINAL.items() if k != missing}
        response = await authenticated_bulk_client.post(
            f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/generate",
            json={"expectedRevision": 0, "original": payload},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_variant_generate_stale_revision_conflict(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/generate",
        json={"expectedRevision": 7, "original": VARIANT_ORIGINAL},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_MISMATCH"


@pytest.mark.asyncio
async def test_variant_generate_duplicate_in_flight_not_counted_twice(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    first = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/generate",
        json={"expectedRevision": 0, "original": VARIANT_ORIGINAL},
    )
    assert first.status_code == 202
    # A second generate against the new revision hits the in-flight guard.
    second = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/generate",
        json={"expectedRevision": 1, "original": VARIANT_ORIGINAL},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "VARIATION_BUSY"
    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    item = _variation_of(detail.json(), item_id)
    assert item["variation"]["generationCount"] == 1


@pytest.mark.asyncio
async def test_variant_generate_other_user_batch_not_found(
    bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    await register_and_login(bulk_client, bulk_app, username="owner")
    batch_id, _, item_id = await _create_variant_batch(bulk_client, bulk_app, helper_vlm, username="owner")
    await register_and_login(bulk_client, bulk_app, username="attacker")
    _enable_variant_profiles(bulk_app)
    response = await bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/generate",
        json={"expectedRevision": 0, "original": VARIANT_ORIGINAL},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_variant_candidate_semantic_edit_enters_needs_validation(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "text": "What is 4+4?"},
    )
    assert response.status_code == 200
    item = _variation_of(response.json(), item_id)
    variation = item["variation"]
    assert variation["status"] == "needs-validation"
    assert variation["candidate"]["text"] == "What is 4+4?"
    assert variation["candidate"]["correctAnswer"] == "8"
    # #648: the stored report stays visible (labeled stale) after a semantic
    # edit; only the approval pointer and attestation are cleared.
    assert variation["validation"]["verdict"] == "pass"
    assert variation["validatedRevision"] is None
    assert variation["attestation"] is None
    assert variation["generationCount"] == 1
    assert item["contentRevision"] == 2


@pytest.mark.asyncio
async def test_variant_candidate_tags_only_does_not_invalidate(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "tags": ["algebra"]},
    )
    assert response.status_code == 200
    item = _variation_of(response.json(), item_id)
    assert item["variation"]["status"] == "ready"
    assert item["variation"]["validatedRevision"] == 1
    assert item["contentRevision"] == 1
    assert item["draft"]["tags"] == ["algebra"]


@pytest.mark.asyncio
async def test_variant_candidate_empty_graphdsl_edit_does_not_invalidate(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    # The wizard sends "" for an unset graphDsl; the stored candidate has None.
    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "tags": ["algebra"], "graphDsl": ""},
    )
    assert response.status_code == 200
    item = _variation_of(response.json(), item_id)
    assert item["variation"]["status"] == "ready"
    assert item["variation"]["validatedRevision"] == 1
    assert item["contentRevision"] == 1


@pytest.mark.asyncio
async def test_variant_candidate_clearing_real_graphdsl_still_invalidates(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    set_graph = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "graphDsl": "create('board', {});"},
    )
    assert set_graph.status_code == 200
    assert _variation_of(set_graph.json(), item_id)["variation"]["status"] == "needs-validation"

    clear_graph = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 2, "graphDsl": None},
    )
    assert clear_graph.status_code == 200
    item = _variation_of(clear_graph.json(), item_id)
    assert item["variation"]["status"] == "needs-validation"
    assert item["contentRevision"] == 3


@pytest.mark.asyncio
async def test_variant_candidate_type_mismatch_preserved_for_evidence(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "problemType": "fill-in-the-blank"},
    )
    assert response.status_code == 200
    variation = _variation_of(response.json(), item_id)["variation"]
    assert variation["status"] == "needs-validation"
    assert variation["candidate"]["problemType"] == "fill-in-the-blank"


@pytest.mark.asyncio
async def test_variant_revalidate_queues_validator_only_run(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)
    # A candidate edit makes it needs-validation.
    edit = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "text": "What is 5+5?"},
    )
    assert edit.status_code == 200

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/revalidate",
        json={"expectedRevision": 2},
    )
    assert response.status_code == 202
    variation = _variation_of(response.json(), item_id)["variation"]
    assert variation["status"] == "queued"
    assert variation["candidate"]["text"] == "What is 5+5?"
    assert variation["generationCount"] == 1
    assert _variation_of(response.json(), item_id)["contentRevision"] == 2

    # Revalidate is only legal from needs-validation.
    second = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/revalidate",
        json={"expectedRevision": 2},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_variant_candidate_whitespace_edit_does_not_invalidate(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    """A whitespace-only candidate edit is formatting, not semantic (#648)."""
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "text": "What is 3+5?\u3000"},
    )
    assert response.status_code == 200
    item = _variation_of(response.json(), item_id)
    assert item["variation"]["status"] == "ready"
    assert item["variation"]["validatedRevision"] == 1
    assert item["variation"]["validation"]["verdict"] == "pass"
    assert item["variation"]["candidate"]["text"] == "What is 3+5?\u3000"
    assert item["contentRevision"] == 1
    assert item["variation"]["attestation"] is None


@pytest.mark.asyncio
async def test_variant_source_whitespace_edit_does_not_invalidate(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    """A whitespace-only source edit must not nuke the variation (#648)."""
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"expectedRevision": 1, "text": "What is 2+2?  "},
    )
    assert response.status_code == 200
    item = _variation_of(response.json(), item_id)
    assert item["variation"]["status"] == "ready"
    assert item["variation"]["validatedRevision"] == 1
    assert item["variation"]["candidate"] is not None
    assert item["contentRevision"] == 1


@pytest.mark.asyncio
async def test_variant_attest_restores_ready_and_admits_submit(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    """Keep-validation attestation restores READY and admits submit honestly."""
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    database = bulk_app.state.fake_database
    user_id = (await database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    edit = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "text": "What is 4+4?"},
    )
    assert edit.status_code == 200
    variation = _variation_of(edit.json(), item_id)["variation"]
    assert variation["status"] == "needs-validation"
    # The stored report is kept visible but stale; approval and attestation
    # are cleared.
    assert variation["validation"]["verdict"] == "pass"
    assert variation["validatedRevision"] is None
    assert variation["attestation"] is None
    assert _variation_of(edit.json(), item_id)["contentRevision"] == 2

    attest = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/attest",
        json={"expectedRevision": 2},
    )
    assert attest.status_code == 200
    item = _variation_of(attest.json(), item_id)
    assert item["variation"]["status"] == "ready"
    assert item["variation"]["attestation"]["revision"] == 2
    assert item["variation"]["attestation"]["at"]
    # The two gate branches stay mutually exclusive.
    assert item["variation"]["validatedRevision"] is None
    assert item["contentRevision"] == 2

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )
    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["items"][0]["status"] == "submitted"
    problem_id = summary["items"][0]["submittedProblemId"]

    problem = await database["problems"].find_one({"_id": ObjectId(problem_id)})
    # An attested PASS is never frozen as if validator-covered.
    assert problem["variation"]["validation"]["attestedByUser"] is True


@pytest.mark.asyncio
async def test_variant_attest_rejections(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    """Attest preconditions: stale FAIL, wrong revision, READY, legacy None."""
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    database = bulk_app.state.fake_database
    user_id = (await database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    # Attest is only legal from needs-validation.
    from_ready = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/attest",
        json={"expectedRevision": 1},
    )
    assert from_ready.status_code == 409
    assert from_ready.json()["error"]["code"] == "INVALID_VARIATION_STATE"

    edit = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "text": "What is 4+4?"},
    )
    assert edit.status_code == 200

    # A stale revision never attests.
    stale = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/attest",
        json={"expectedRevision": 99},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVISION_MISMATCH"

    item_filter = {"_id": ObjectId(batch_id), "items.itemId": item_id}
    # A stale FAIL verdict cannot be attested into READY.
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        item_filter,
        {"$set": {"items.$.variation.validation.verdict": "fail"}},
    )
    fail_verdict = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/attest",
        json={"expectedRevision": 2},
    )
    assert fail_verdict.status_code == 409
    assert fail_verdict.json()["error"]["code"] == "INVALID_VARIATION_STATE"

    # Legacy items with no stored report cannot attest.
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        item_filter,
        {"$set": {"items.$.variation.validation": None}},
    )
    no_report = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/attest",
        json={"expectedRevision": 2},
    )
    assert no_report.status_code == 409
    assert no_report.json()["error"]["code"] == "INVALID_VARIATION_STATE"

    detail = await authenticated_bulk_client.get(
        f"/api/v1/ingestion-batches/{batch_id}"
    )
    item = _variation_of(detail.json(), item_id)
    assert item["variation"]["status"] == "needs-validation"


@pytest.mark.asyncio
async def test_variant_attestation_cleared_by_later_semantic_edit(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    """A stale attestation can never ride along into the next edit (#648)."""
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)
    edit = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 1, "text": "What is 4+4?"},
    )
    assert edit.status_code == 200
    attest = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/attest",
        json={"expectedRevision": 2},
    )
    assert attest.status_code == 200

    second_edit = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": 2, "text": "What is 5+5?"},
    )
    assert second_edit.status_code == 200
    item = _variation_of(second_edit.json(), item_id)
    assert item["variation"]["status"] == "needs-validation"
    assert item["variation"]["attestation"] is None
    assert item["variation"]["validatedRevision"] is None
    assert item["contentRevision"] == 3


@pytest.mark.asyncio
async def test_variant_source_semantic_edit_clears_attestation(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    """A source semantic edit nukes the variation and its attestation."""
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_needs_validation(bulk_app, user_id, batch_id, item_id)
    attest = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/attest",
        json={"expectedRevision": 2},
    )
    assert attest.status_code == 200

    source_edit = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"expectedRevision": 2, "text": "A changed problem statement?"},
    )
    assert source_edit.status_code == 200
    item = _variation_of(source_edit.json(), item_id)
    assert item["variation"]["status"] == "not-requested"
    assert item["variation"]["attestation"] is None


@pytest.mark.asyncio
async def test_variant_generate_clears_attestation(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    """Generate Again discards the attested candidate and its attestation."""
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_needs_validation(bulk_app, user_id, batch_id, item_id)
    attest = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/attest",
        json={"expectedRevision": 2},
    )
    assert attest.status_code == 200

    generate = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/generate",
        json={"expectedRevision": 2, "original": VARIANT_ORIGINAL},
    )
    assert generate.status_code == 202
    item = _variation_of(generate.json(), item_id)
    assert item["variation"]["status"] == "queued"
    assert item["variation"]["attestation"] is None


@pytest.mark.asyncio
async def test_variant_revalidate_clears_attestation(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    """Revalidate clears any attestation (defensive: legacy documents may
    still carry one into needs-validation)."""
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    database = bulk_app.state.fake_database
    user_id = (await database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_needs_validation(bulk_app, user_id, batch_id, item_id)
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": ObjectId(batch_id), "items.itemId": item_id},
        {"$set": {"items.$.variation.attestation": {"revision": 2, "at": "legacy"}}},
    )

    revalidate = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/revalidate",
        json={"expectedRevision": 2},
    )
    assert revalidate.status_code == 202
    item = _variation_of(revalidate.json(), item_id)
    assert item["variation"]["status"] == "queued"
    assert item["variation"]["attestation"] is None


@pytest.mark.asyncio
async def test_variant_source_semantic_edit_invalidates(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"expectedRevision": 1, "text": "A changed problem statement?"},
    )
    assert response.status_code == 200
    item = _variation_of(response.json(), item_id)
    variation = item["variation"]
    assert variation["status"] == "not-requested"
    assert variation["original"] is None
    assert variation["candidate"] is None
    assert variation["validation"] is None
    assert variation["generationCount"] == 1
    assert item["contentRevision"] == 2
    assert item["draft"]["text"] == "A changed problem statement?"


@pytest.mark.asyncio
async def test_variant_source_tags_only_edit_keeps_ready(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"expectedRevision": 1, "tags": ["arithmetic"]},
    )
    assert response.status_code == 200
    item = _variation_of(response.json(), item_id)
    assert item["variation"]["status"] == "ready"
    assert item["variation"]["validatedRevision"] == 1
    assert item["contentRevision"] == 1


@pytest.mark.asyncio
async def test_variant_source_empty_graphdsl_edit_keeps_ready(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    # "" for an unset graphDsl must not count as a semantic source edit.
    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"expectedRevision": 1, "tags": ["arithmetic"], "graphDsl": ""},
    )
    assert response.status_code == 200
    item = _variation_of(response.json(), item_id)
    assert item["variation"]["status"] == "ready"
    assert item["variation"]["validatedRevision"] == 1
    assert item["variation"]["candidate"] is not None
    assert item["contentRevision"] == 1


@pytest.mark.asyncio
async def test_variant_source_edit_requires_expected_revision(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"text": "no revision"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REVISION_REQUIRED"


@pytest.mark.asyncio
async def test_variant_source_edit_stale_revision_conflict(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"expectedRevision": 9, "text": "stale edit"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_MISMATCH"


@pytest.mark.asyncio
async def test_original_mode_patch_without_expected_revision_still_works(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    # _create_variant_batch creates a variant batch; use a fresh original one.
    create_response = await authenticated_bulk_client.post("/api/v1/ingestion-batches")
    original_batch_id = create_response.json()["batch"]["id"]
    assert create_response.json()["batch"]["ingestionMode"] == "original"

    upload = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{original_batch_id}/images",
        files={"images": ("test.png", make_valid_png_bytes(), "image/png")},
    )
    image_id = upload.json()["batch"]["images"][0]["imageId"]
    helper_vlm.responses.append(
        make_detection_result(subject="math", boxes=[ProblemBox(x=0, y=0, width=1, height=1)])
    )
    await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{original_batch_id}/images/{image_id}/detect"
    )
    commit = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{original_batch_id}/images/{image_id}/commit"
    )
    original_item_id = commit.json()["batch"]["items"][0]["itemId"]

    # Legacy request shape: no expectedRevision at all.
    response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{original_batch_id}/items/{original_item_id}",
        json={"text": "Edited without revision", "correctAnswer": "9"},
    )
    assert response.status_code == 200
    item = _variation_of(response.json(), original_item_id)
    assert item["draft"]["text"] == "Edited without revision"
    assert item["contentRevision"] == 0


@pytest.mark.asyncio
async def test_variant_submit_skips_variant_items_and_keeps_batch_active(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, variant_item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, variant_item_id)

    # A second item in the same batch that never requested a variant.
    database = bulk_app.state.fake_database
    batch_object_id = ObjectId(batch_id)
    second_update = await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_object_id, "userId": user_id},
        {
            "$push": {
                "items": {
                    "itemId": "plain-item",
                    "imageId": "image-1",
                    "batchId": batch_object_id,
                    "status": "ready",
                    "order": 1,
                    "draft": {"text": "Plain original", "problemType": "short-answer",
                              "graphDsl": None, "correctAnswer": "1", "tags": [], "subject": "math"},
                    "extraction": {}, "retryCount": 0, "contentRevision": 0,
                    "variation": {"status": "not-requested", "generationCount": 0,
                                  "original": None, "candidate": None, "validation": None,
                                  "validatedRevision": None, "claimToken": None,
                                  "leaseUntil": None, "queuedAt": None},
                    "submit": {}, "origin": {"itemId": "plain-item"}, "crop": None,
                    "leaseUntil": None,
                    "createdAt": datetime.now(UTC), "updatedAt": datetime.now(UTC),
                }
            }
        },
    )
    assert second_update.modified_count == 1

    submit_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )
    assert submit_response.status_code == 200
    summary = submit_response.json()["submitSummary"]
    submitted_ids = [r["itemId"] for r in summary["items"]]
    # Variant submit admits the current PASS candidate transactionally; the
    # never-requested plain item is not actionable in variant mode and is
    # never pushed through the original-draft path.
    assert submitted_ids == [variant_item_id]
    assert summary["items"][0]["status"] == "submitted"
    assert summary["status"] == "active"

    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    items = {i["itemId"]: i for i in detail.json()["batch"]["items"]}
    plain_item = items["plain-item"]
    assert plain_item["status"] == "ready"
    assert plain_item["submit"].get("submittedProblemId") is None
    variant_item = items[variant_item_id]
    assert variant_item["status"] == "submitted"
    assert variant_item["submit"]["submittedProblemId"] == summary["items"][0]["submittedProblemId"]
    assert variant_item["variation"]["status"] == "ready"
    assert variant_item["variation"]["candidate"]["text"] == VARIANT_CANDIDATE["text"]
    database = bulk_app.state.fake_database
    assert await database["problems"].count_documents({}) == 1
    problem = await database["problems"].find_one({})
    assert problem["text"] == VARIANT_CANDIDATE["text"]
    assert problem["sourceImage"] is None


@pytest.mark.asyncio
async def test_variant_submit_failure_retry_does_not_reextract(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    # Simulate a submission failure on the variant item (e.g. from a
    # dedicated save path): the retry must return it to ready without
    # re-extraction, never back to the extraction queue.
    database = bulk_app.state.fake_database
    updated = await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": ObjectId(batch_id), "userId": user_id, "items.itemId": item_id},
        {"$set": {"items.$.status": "submit-failed"}},
    )
    assert updated.modified_count == 1

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/retry"
    )
    assert response.status_code == 200
    item = _variation_of(response.json(), item_id)
    assert item["status"] == "ready"
    assert item["variation"]["original"] == VARIANT_ORIGINAL
    assert item["variation"]["candidate"] is not None


# ---------------------------------------------------------------------------
# Submit reservation vs Generate race (issue #613 reviewer fixes)
# ---------------------------------------------------------------------------

from app.problem_variation import IngestionMode  # noqa: E402


async def _seed_submit_ready_batch(
    database: FakeDatabase,
    *,
    item_ids: list[str],
) -> tuple[ObjectId, list[str]]:
    """Seed an active data-only batch whose items are ready for submit."""
    settings = Settings(s3_bucket="learnloop-media")
    batch = await create_batch(
        database, "user-1", settings,
        ingestion_mode=IngestionMode.DATA_ONLY, now=datetime.now(UTC),
    )
    items = []
    for index, item_id in enumerate(item_ids):
        items.append(
            {
                "itemId": item_id,
                "imageId": "image-1",
                "batchId": batch["_id"],
                "status": "ready",
                "order": index,
                "draft": {
                    "text": f"What is {index}+{index}?",
                    "problemType": "short-answer",
                    "graphDsl": None,
                    "correctAnswer": str(2 * index),
                    "tags": [],
                    "subject": "math",
                },
                "extraction": {},
                "retryCount": 0,
                "contentRevision": 0,
                "variation": {
                    "status": "not-requested",
                    "generationCount": 0,
                    "original": None,
                    "candidate": None,
                    "validation": None,
                    "validatedRevision": None,
                    "claimToken": None,
                    "leaseUntil": None,
                    "queuedAt": None,
                },
                "submit": {
                    "submittedProblemId": None,
                    "success": None,
                    "failureCode": None,
                    "failureMessage": None,
                },
                "origin": {"itemId": item_id},
                "crop": None,
                "leaseUntil": None,
                "createdAt": datetime.now(UTC),
                "updatedAt": datetime.now(UTC),
            }
        )
    batch["items"] = items
    await database[INGESTION_BATCHES_COLLECTION].replace_one(
        {"_id": batch["_id"]}, dict(batch)
    )
    return batch["_id"], list(item_ids)


def _stored_item(batch_doc: dict[str, Any], item_id: str) -> dict[str, Any]:
    return next(i for i in batch_doc["items"] if i["itemId"] == item_id)


@pytest.mark.asyncio
async def test_reserve_skips_variant_flagged_items() -> None:
    database = FakeDatabase()
    batch_id, item_ids = await _seed_submit_ready_batch(database, item_ids=["a", "b"])
    # Item "a" had its variant confirmed between the submit read and the
    # reserve: it must never be reserved for the original path.
    await request_variation_generation(
        database, batch_id, "user-1", "a",
        original=dict(VARIANT_ORIGINAL), expected_revision=0, now=datetime.now(UTC),
    )

    _, reserved = await reserve_items_for_original_submit(
        database, batch_id, "user-1", item_ids, now=datetime.now(UTC),
    )
    assert reserved == ["b"]


@pytest.mark.asyncio
async def test_generate_rejects_submit_reserved_item_until_expiry() -> None:
    database = FakeDatabase()
    batch_id, item_ids = await _seed_submit_ready_batch(database, item_ids=["a"])
    now = datetime.now(UTC)
    token, reserved = await reserve_items_for_original_submit(
        database, batch_id, "user-1", item_ids, now=now,
    )
    assert reserved == ["a"]

    from app.problem_variation import InvalidVariationStateError

    with pytest.raises(InvalidVariationStateError, match="reserved for submission"):
        await request_variation_generation(
            database, batch_id, "user-1", "a",
            original=dict(VARIANT_ORIGINAL), expected_revision=0, now=now,
        )

    # The reservation expires (crashed submit request): Generate wins and
    # clears the stale reservation.
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id, "items.itemId": "a"},
        {"$set": {"items.$.variation.submitReservation.expiresAt": now - timedelta(seconds=1)}},
    )
    await request_variation_generation(
        database, batch_id, "user-1", "a",
        original=dict(VARIANT_ORIGINAL), expected_revision=0, now=now,
    )
    batch_doc = await database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    item = _stored_item(batch_doc, "a")
    assert item["variation"]["status"] == "queued"
    assert item["variation"]["submitReservation"] is None
    assert token  # the stale token can no longer reserve anything


@pytest.mark.asyncio
async def test_submit_reservation_renewal_requires_live_window() -> None:
    database = FakeDatabase()
    batch_id, item_ids = await _seed_submit_ready_batch(database, item_ids=["a"])
    now = datetime.now(UTC)
    token, _ = await reserve_items_for_original_submit(
        database, batch_id, "user-1", item_ids, now=now,
    )

    from app.problem_variation import InvalidVariationStateError

    # A renewal strictly inside the window extends it: Generate stays locked
    # out past the original 10-minute deadline (the live submit's heartbeat).
    within = now + timedelta(minutes=5)
    assert await renew_submit_reservation(
        database, batch_id, "user-1", "a", token=token, now=within,
    ) is True
    past_original = now + timedelta(minutes=11)
    with pytest.raises(InvalidVariationStateError, match="reserved for submission"):
        await request_variation_generation(
            database, batch_id, "user-1", "a",
            original=dict(VARIANT_ORIGINAL), expected_revision=0, now=past_original,
        )

    # A foreign token can never renew someone else's reservation.
    assert await renew_submit_reservation(
        database, batch_id, "user-1", "a", token="not-the-owner", now=within,
    ) is False

    # Once the renewed window passes, the old token cannot revive the
    # reservation and Generate reclaims the item.
    expired = now + timedelta(minutes=16)
    assert await renew_submit_reservation(
        database, batch_id, "user-1", "a", token=token, now=expired,
    ) is False
    await request_variation_generation(
        database, batch_id, "user-1", "a",
        original=dict(VARIANT_ORIGINAL), expected_revision=0, now=expired,
    )
    batch_doc = await database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    assert _stored_item(batch_doc, "a")["variation"]["submitReservation"] is None


@pytest.mark.asyncio
async def test_submit_completion_requires_matching_reservation_token() -> None:
    database = FakeDatabase()
    batch_id, item_ids = await _seed_submit_ready_batch(database, item_ids=["a"])
    now = datetime.now(UTC)
    token, _ = await reserve_items_for_original_submit(
        database, batch_id, "user-1", item_ids, now=now,
    )

    def submitted_result(item_id: str) -> dict[str, Any]:
        return {
            "itemId": item_id,
            "status": "submitted",
            "submit": {
                "submittedProblemId": str(ObjectId()),
                "success": True,
                "failureCode": None,
                "failureMessage": None,
            },
        }

    # A submit holding a different token can never record its result.
    await submit_items_and_complete_batch(
        database, batch_id, "user-1",
        item_results=[submitted_result("a")],
        reservation_token="not-the-owner",
        now=now,
    )
    batch_doc = await database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    item = _stored_item(batch_doc, "a")
    assert item["status"] == "ready"
    assert item["submit"]["submittedProblemId"] is None

    # The owning token lands the result and releases the reservation.
    await submit_items_and_complete_batch(
        database, batch_id, "user-1",
        item_results=[submitted_result("a")],
        reservation_token=token,
        now=now,
    )
    batch_doc = await database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    item = _stored_item(batch_doc, "a")
    assert item["status"] == "submitted"
    assert item["variation"]["submitReservation"] is None


@pytest.mark.asyncio
async def test_stale_submit_completion_cannot_record_after_generate_wins() -> None:
    database = FakeDatabase()
    batch_id, item_ids = await _seed_submit_ready_batch(database, item_ids=["a"])
    now = datetime.now(UTC)
    token, _ = await reserve_items_for_original_submit(
        database, batch_id, "user-1", item_ids, now=now,
    )

    # The reservation expired while the submit was stalled; Generate confirms
    # the variant and clears the stale reservation.
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id, "items.itemId": "a"},
        {"$set": {"items.$.variation.submitReservation.expiresAt": now - timedelta(seconds=1)}},
    )
    await request_variation_generation(
        database, batch_id, "user-1", "a",
        original=dict(VARIANT_ORIGINAL), expected_revision=0, now=now,
    )

    # The stalled submit's completion can no longer record the item as an
    # original submission.
    await submit_items_and_complete_batch(
        database, batch_id, "user-1",
        item_results=[
            {
                "itemId": "a",
                "status": "submitted",
                "submit": {
                    "submittedProblemId": str(ObjectId()),
                    "success": True,
                    "failureCode": None,
                    "failureMessage": None,
                },
            }
        ],
        reservation_token=token,
        now=now,
    )
    batch_doc = await database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    item = _stored_item(batch_doc, "a")
    assert item["status"] == "ready"
    assert item["submit"]["submittedProblemId"] is None
    assert item["variation"]["status"] == "queued"
    assert item["variation"]["original"] == VARIANT_ORIGINAL


@pytest.mark.asyncio
async def test_delete_serializes_with_live_submit_reservation(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard-passing create cannot lose its item to a concurrent deletion.

    Once the in-creation ownership guard succeeds the reservation is live,
    so a competing delete is refused before the insert: the problem, its
    solution task and its tags commit coherently and no orphan problem can
    exist after a reservation loss. Original-mode batch: this race lives on
    the original-draft submit path (#614 moved variant batches to the
    transactional variant admission).
    """
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm, mode="original"
    )
    patch_response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"correctAnswer": "4", "expectedRevision": 0},
    )
    assert patch_response.status_code == 200
    database = bulk_app.state.fake_database

    from app.presentation import bulk_ingestion as bulk_ingestion_module

    creation_started = asyncio.Event()
    release_creation = asyncio.Event()
    renewal_calls = {"n": 0}

    async def fake_renew(*args: Any, **kwargs: Any) -> bool:
        renewal_calls["n"] += 1
        if renewal_calls["n"] == 1:
            return True  # pre-creation ownership proof
        # The second call is the guard inside real problem creation, right
        # before the irreversible insert: pause it while a competing delete
        # runs. The guard itself SUCCEEDS — deletion must lose to the live
        # reservation, not to a failed guard.
        creation_started.set()
        await release_creation.wait()
        return True

    monkeypatch.setattr(bulk_ingestion_module, "renew_submit_reservation", fake_renew)

    submit_task = asyncio.create_task(
        authenticated_bulk_client.post(f"/api/v1/ingestion-batches/{batch_id}/submit")
    )
    await asyncio.wait_for(creation_started.wait(), timeout=5)

    # Deletion races the submit between the successful guard and the insert.
    delete_response = await authenticated_bulk_client.delete(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}"
    )
    assert delete_response.status_code == 200
    assert _variation_of(delete_response.json(), item_id)["status"] == "ready"

    release_creation.set()
    response = await asyncio.wait_for(submit_task, timeout=5)
    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["items"][0]["status"] == "submitted"
    assert summary["items"][0]["failureCode"] is None

    # The side effects committed coherently with the recorded submission:
    # no orphan problem and no dropped reservation.
    assert await database["problems"].count_documents({}) == 1
    assert await database["solution_generation_tasks"].count_documents({}) == 1
    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    item = _variation_of(detail.json(), item_id)
    assert item["status"] == "submitted"


@pytest.mark.asyncio
async def test_generate_wins_during_variant_submit_and_admission_fails_closed(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generate winning between selection and admission invalidates the save.

    The pre-#614 race (Generate vs the original submit) is gone: variant
    batches admit the validated candidate, and a new generation request
    landing inside the submit window bumps the item revision, so the
    transactional re-read fails the admission closed instead of saving the
    source or a stale candidate.
    """
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    database = bulk_app.state.fake_database
    user_id = (await database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)
    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    revision = _variation_of(detail.json(), item_id)["contentRevision"]

    from app.presentation import bulk_ingestion as bulk_ingestion_module

    admission_started = asyncio.Event()
    release_admission = asyncio.Event()

    real_admit = bulk_ingestion_module.admit_variant_item

    async def pausing_admit(*args: Any, **kwargs: Any) -> Any:
        admission_started.set()
        await asyncio.wait_for(release_admission.wait(), timeout=5)
        return await real_admit(*args, **kwargs)

    monkeypatch.setattr(bulk_ingestion_module, "admit_variant_item", pausing_admit)

    submit_task = asyncio.create_task(
        authenticated_bulk_client.post(f"/api/v1/ingestion-batches/{batch_id}/submit")
    )
    await asyncio.wait_for(admission_started.wait(), timeout=5)

    # Generate wins the window: a new generation bumps the revision and
    # clears the validated candidate.
    await request_variation_generation(
        database, ObjectId(batch_id), user_id, item_id,
        original=dict(VARIANT_ORIGINAL), expected_revision=revision,
        now=datetime.now(UTC),
    )
    release_admission.set()

    response = await asyncio.wait_for(submit_task, timeout=5)
    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["items"][0]["status"] == "submit-failed"
    assert summary["items"][0]["failureCode"] == "VARIANT_INVALIDATED"
    assert summary["status"] == "active"

    assert await database["problems"].count_documents({}) == 0
    assert await database["solution_generation_tasks"].count_documents({}) == 0
    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    item = _variation_of(detail.json(), item_id)
    assert item["status"] == "ready"
    assert item["variation"]["status"] == "queued"
    assert item["submit"]["submittedProblemId"] is None


@pytest.mark.asyncio
async def test_submit_fails_closed_when_reservation_lost_before_creation(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Original-mode batch: the reservation race lives on the original-draft
    # submit path (#614 moved variant batches to transactional admission).
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm, mode="original"
    )
    patch_response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"correctAnswer": "4", "expectedRevision": 0},
    )
    assert patch_response.status_code == 200
    database = bulk_app.state.fake_database

    from app.presentation import bulk_ingestion as bulk_ingestion_module

    created: list[Any] = []

    async def must_not_create(*args: Any, **kwargs: Any) -> Any:
        created.append(args)
        return {"_id": ObjectId()}

    async def lost_renewal(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(bulk_ingestion_module, "create_problem_from_draft", must_not_create)
    monkeypatch.setattr(bulk_ingestion_module, "renew_submit_reservation", lost_renewal)

    # The pre-creation ownership proof fails: no original problem is created
    # and the item fails closed instead of being recorded as submitted.
    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )
    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["items"][0]["status"] == "submit-failed"
    assert summary["items"][0]["failureCode"] == "RESERVATION_LOST"
    assert created == []

    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    item = _variation_of(detail.json(), item_id)
    assert item["status"] == "submit-failed"
    assert item["submit"]["submittedProblemId"] is None


@pytest.mark.asyncio
async def test_null_candidate_edit_revalidation_fails_with_evidence(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    user_id = (await bulk_app.state.fake_database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)
    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    revision = _variation_of(detail.json(), item_id)["contentRevision"]

    # An explicit null for a required candidate field is accepted and marks
    # the candidate needs-validation...
    edit_response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/candidate",
        json={"expectedRevision": revision, "text": None},
    )
    assert edit_response.status_code == 200
    assert _variation_of(edit_response.json(), item_id)["variation"]["status"] == "needs-validation"
    revision = _variation_of(edit_response.json(), item_id)["contentRevision"]

    revalidate_response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}/variation/revalidate",
        json={"expectedRevision": revision},
    )
    assert revalidate_response.status_code == 202

    # ...and the worker fails it closed with structured evidence instead of
    # crashing and leaving the item in-flight forever.
    database = bulk_app.state.fake_database
    batch_object_id = ObjectId(batch_id)
    claimed = await claim_variation_work(
        database, batch_object_id, user_id, item_id,
        lease_timeout_seconds=300, now=datetime.now(UTC),
    )
    assert claimed is not None
    stored_batch = await database[INGESTION_BATCHES_COLLECTION].find_one(
        {"_id": batch_object_id}
    )
    from app.infrastructure.worker.variation_worker import process_variation

    await process_variation(
        claimed, stored_batch, database, [], [], None,
        Settings(s3_bucket="learnloop-media"), now=datetime.now(UTC),
    )

    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    variation = _variation_of(detail.json(), item_id)["variation"]
    assert variation["status"] == "failed"
    failure = variation["validation"]["failures"][0]
    assert failure["kind"] == "invalid-candidate"
    assert "text" in failure["evidence"]



# ---------------------------------------------------------------------------
# Transactional variant admission (issue #614)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variant_submit_admits_ready_candidate(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    database = bulk_app.state.fake_database
    user_id = (await database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )
    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["status"] == "completed"
    assert summary["items"][0]["itemId"] == item_id
    assert summary["items"][0]["status"] == "submitted"
    problem_id = summary["items"][0]["submittedProblemId"]
    assert problem_id

    problem = await database["problems"].find_one({"_id": ObjectId(problem_id)})
    assert problem is not None
    # Main content comes from the accepted variant; the source is audit-only.
    assert problem["text"] == VARIANT_CANDIDATE["text"]
    assert problem["correctAnswer"]["display"] == VARIANT_CANDIDATE["correctAnswer"]
    assert problem["sourceImage"] is None
    variation = problem["variation"]
    assert variation["mode"] == "data-only"
    assert variation["acceptedVariant"]["text"] == VARIANT_CANDIDATE["text"]
    assert variation["original"]["text"] == VARIANT_ORIGINAL["text"]
    assert variation["original"]["auditImage"]["objectKey"] == (
        f"users/{user_id}/problems/audit/{batch_id}/{item_id}.png"
    )
    assert variation["validation"]["verdict"] == "pass"

    # One transactionally consistent solution task; the batch completed.
    assert await database["solution_generation_tasks"].count_documents(
        {"problem_id": problem_id}
    ) == 1
    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    item = _variation_of(detail.json(), item_id)
    assert item["status"] == "submitted"
    assert item["submit"]["submittedProblemId"] == problem_id

    storage: FakeStorage = bulk_app.state.fake_storage
    audit_keys = [key for (_, key) in storage.objects if "/problems/audit/" in key]
    assert audit_keys == [variation["original"]["auditImage"]["objectKey"]]


@pytest.mark.asyncio
async def test_variant_submit_invalidated_between_selection_and_admission(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    database = bulk_app.state.fake_database
    user_id = (await database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)
    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    revision = _variation_of(detail.json(), item_id)["contentRevision"]

    from app.presentation import bulk_ingestion as bulk_ingestion_module

    admission_started = asyncio.Event()
    release_admission = asyncio.Event()

    real_admit = bulk_ingestion_module.admit_variant_item

    async def pausing_admit(*args: Any, **kwargs: Any) -> Any:
        # Deterministic pause between selection and the transactional
        # re-read: a semantic edit lands here and must invalidate the
        # selected candidate.
        admission_started.set()
        await asyncio.wait_for(release_admission.wait(), timeout=5)
        return await real_admit(*args, **kwargs)

    monkeypatch.setattr(bulk_ingestion_module, "admit_variant_item", pausing_admit)

    submit_task = asyncio.create_task(
        authenticated_bulk_client.post(f"/api/v1/ingestion-batches/{batch_id}/submit")
    )
    await asyncio.wait_for(admission_started.wait(), timeout=5)

    patch_response = await authenticated_bulk_client.patch(
        f"/api/v1/ingestion-batches/{batch_id}/items/{item_id}",
        json={"correctAnswer": "9", "expectedRevision": revision},
    )
    assert patch_response.status_code == 200
    release_admission.set()

    response = await asyncio.wait_for(submit_task, timeout=5)
    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["status"] == "active"
    assert summary["items"][0]["status"] == "submit-failed"
    assert summary["items"][0]["failureCode"] == "VARIANT_INVALIDATED"

    # The source and the stale candidate were never saved.
    assert await database["problems"].count_documents({}) == 0
    assert await database["solution_generation_tasks"].count_documents({}) == 0
    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    item = _variation_of(detail.json(), item_id)
    assert item["status"] == "ready"
    assert item["submit"]["submittedProblemId"] is None


@pytest.mark.asyncio
async def test_variant_submit_retry_reuses_audit_copy_and_saves_once(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    database = bulk_app.state.fake_database
    user_id = (await database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)

    from app.presentation import bulk_ingestion as bulk_ingestion_module
    from app.presentation.errors import ApiError

    calls = {"n": 0}

    real_admit = bulk_ingestion_module.admit_variant_item

    async def flaky_admit(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ApiError(503, "VARIANT_ADMISSION_FAILED", "Transient admission failure")
        return await real_admit(*args, **kwargs)

    monkeypatch.setattr(bulk_ingestion_module, "admit_variant_item", flaky_admit)

    first = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )
    assert first.status_code == 200
    assert first.json()["submitSummary"]["items"][0]["status"] == "submit-failed"

    second = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )
    assert second.status_code == 200
    summary = second.json()["submitSummary"]
    assert summary["items"][0]["status"] == "submitted"
    problem_id = summary["items"][0]["submittedProblemId"]

    # Exactly one Problem and one audit object; the retry overwrote the same
    # deterministic key instead of creating another copy.
    assert await database["problems"].count_documents({}) == 1
    storage: FakeStorage = bulk_app.state.fake_storage
    audit_puts = [key for (_, key, _, _) in storage.put_calls if "/problems/audit/" in key]
    assert len(audit_puts) == 2
    assert len(set(audit_puts)) == 1
    assert await database["solution_generation_tasks"].count_documents(
        {"problem_id": problem_id}
    ) == 1


@pytest.mark.asyncio
async def test_variant_submit_skips_items_without_current_pass(
    authenticated_bulk_client: AsyncClient,
    bulk_app: FastAPI,
    helper_vlm: FakeHelperVLMClient,
) -> None:
    _enable_variant_profiles(bulk_app)
    batch_id, _, item_id = await _create_variant_batch(
        authenticated_bulk_client, bulk_app, helper_vlm
    )
    database = bulk_app.state.fake_database
    user_id = (await database["users"].find_one({"username": "student1"}))["_id"]
    await _drive_to_ready_candidate(bulk_app, user_id, batch_id, item_id)
    # Invalidate the candidate (simulated failed revalidation state).
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": ObjectId(batch_id), "items.itemId": item_id},
        {"$set": {"items.$.variation.status": "failed"}},
    )

    response = await authenticated_bulk_client.post(
        f"/api/v1/ingestion-batches/{batch_id}/submit"
    )
    assert response.status_code == 200
    summary = response.json()["submitSummary"]
    assert summary["items"] == []
    assert summary["status"] == "active"
    assert await database["problems"].count_documents({}) == 0
    detail = await authenticated_bulk_client.get(f"/api/v1/ingestion-batches/{batch_id}")
    item = _variation_of(detail.json(), item_id)
    assert item["status"] == "ready"
