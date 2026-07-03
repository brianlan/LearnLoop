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
from app.main import create_app
from app.presentation.deps import get_app_settings, get_database, get_s3_storage
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


def make_png_bytes() -> bytes:
    payload = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9l9iAAAAAASUVORK5CYII="
    )
    return io.BytesIO(payload).getvalue()


def make_oversize_png_bytes(size: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * size


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
    )

    application.state.fake_database = database
    application.state.fake_storage = storage

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_app_settings] = lambda: settings
    application.dependency_overrides[get_s3_storage] = lambda: storage

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
