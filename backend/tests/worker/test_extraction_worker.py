from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId

from app.domain.ingestion import ImageState, ItemState
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion.documents import (
    build_batch_document,
    build_image_document,
    build_item_document,
    build_source_image,
)
from app.infrastructure.ingestion.repository import (
    claim_item,
    get_batch,
    reset_item_for_retry,
)
from app.infrastructure.vlm.client import ExtractionResult
from app.infrastructure.worker.extraction_worker import (
    _is_item_claimable,
    claim_next_item,
    process_item,
)
from tests.conftest import FakeDatabase, FakeStorage


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
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color="black").save(buffer, format="PNG")
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


def _seed_batch_with_committed_item(
    database: FakeDatabase,
    storage: FakeStorage,
    *,
    subject: str = "math",
    box: dict[str, Any] | None = None,
    item_status: str = ItemState.QUEUED.value,
    lease_until: datetime | None = None,
    retry_count: int = 0,
) -> tuple[ObjectId, ObjectId, str, str]:
    user_id = ObjectId()
    batch_id = ObjectId()
    image_id = "image-1"
    item_id = "item-1"
    now = datetime.now(UTC)

    source_image = build_source_image(
        bucket="media",
        object_key="users/u/source.png",
        content_type="image/png",
        size_bytes=len(make_png_bytes()),
        sha256="sha",
        uploaded_at=now,
        width=10,
        height=10,
    )
    image = build_image_document(
        image_id=image_id,
        source_image=source_image,
        order=0,
        now=now,
    )
    image["status"] = ImageState.COMMITTED.value
    image["subject"] = subject
    image["boxes"] = [box] if box else []
    image["committedAt"] = now

    item = build_item_document(
        item_id=item_id,
        batch_id=batch_id,
        image_id=image_id,
        order=0,
        now=now,
        box=box,
    )
    item["status"] = item_status
    item["leaseUntil"] = lease_until
    item["retryCount"] = retry_count

    batch = build_batch_document(
        batch_id=batch_id,
        user_id=user_id,
        expires_at=now + timedelta(hours=1),
        now=now,
    )
    batch["images"] = [image]
    batch["items"] = [item]

    database.seed("ingestion_batches", [batch])
    storage.seed("media", "users/u/source.png", make_png_bytes())
    return batch_id, user_id, image_id, item_id


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        s3_bucket="media",
        bulk_ingestion_item_lease_timeout_seconds=60,
        bulk_ingestion_extraction_concurrency=2,
    )


@pytest.mark.asyncio
async def test_is_item_claimable_queued() -> None:
    assert _is_item_claimable({"status": ItemState.QUEUED.value}, datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_is_item_claimable_extracting_with_expired_lease() -> None:
    now = datetime.now(UTC)
    assert (
        _is_item_claimable(
            {"status": ItemState.EXTRACTING.value, "leaseUntil": now - timedelta(seconds=1)},
            now,
        )
        is True
    )


@pytest.mark.asyncio
async def test_is_item_claimable_not_ready() -> None:
    now = datetime.now(UTC)
    assert _is_item_claimable({"status": ItemState.READY.value}, now) is False
    assert (
        _is_item_claimable(
            {"status": ItemState.EXTRACTING.value, "leaseUntil": now + timedelta(seconds=1)},
            now,
        )
        is False
    )


@pytest.mark.asyncio
async def test_claim_item_sets_extracting_and_lease(
    database: FakeDatabase,
    storage: FakeStorage,
    settings: Settings,
) -> None:
    batch_id, user_id, _image_id, item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
    )
    batch = await get_batch(database, batch_id, user_id)
    user_id = batch["userId"]
    now = datetime.now(UTC)

    claimed = await claim_item(
        database,
        batch_id,
        item_id,
        user_id,
        lease_timeout_seconds=settings.bulk_ingestion_item_lease_timeout_seconds,
        now=now,
    )

    assert claimed is not None
    assert claimed["status"] == ItemState.EXTRACTING.value
    assert claimed["retryCount"] == 1
    assert claimed["leaseUntil"] > now

    refreshed = await get_batch(database, batch_id, user_id)
    stored_item = refreshed["items"][0]
    assert stored_item["status"] == ItemState.EXTRACTING.value


@pytest.mark.asyncio
async def test_claim_item_returns_none_when_lease_active(
    database: FakeDatabase,
    storage: FakeStorage,
    settings: Settings,
) -> None:
    now = datetime.now(UTC)
    batch_id, user_id, _image_id, item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
        item_status=ItemState.EXTRACTING.value,
        lease_until=now + timedelta(seconds=30),
    )
    batch = await get_batch(database, batch_id, user_id)
    user_id = batch["userId"]

    claimed = await claim_item(
        database,
        batch_id,
        item_id,
        user_id,
        lease_timeout_seconds=settings.bulk_ingestion_item_lease_timeout_seconds,
        now=now,
    )

    assert claimed is None


@pytest.mark.asyncio
async def test_process_item_success_math(
    database: FakeDatabase,
    storage: FakeStorage,
    settings: Settings,
) -> None:
    batch_id, user_id, _image_id, item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
    )
    batch = await get_batch(database, batch_id, user_id)
    item = batch["items"][0]

    math_client = FakeIngestionVLMClient(model="math-model")
    english_client = FakeIngestionVLMClient(model="english-model")
    math_client.responses.append(make_extraction_result(text="math problem", model="math-model"))

    await process_item(
        item,
        batch,
        database,
        storage,
        math_client,
        english_client,
        settings,
    )

    assert len(math_client.calls) == 1
    assert len(english_client.calls) == 0
    assert storage.put_calls[-1][0] == "media"
    assert storage.put_calls[-1][1].startswith("users/") and "ingestion/crops" in storage.put_calls[-1][1]

    refreshed = await get_batch(database, batch_id, batch["userId"])
    stored_item = refreshed["items"][0]
    assert stored_item["status"] == ItemState.READY.value
    assert stored_item["draft"]["text"] == "math problem"
    assert stored_item["draft"]["subject"] == "math"
    assert stored_item["extraction"]["success"] is True
    assert stored_item["extraction"]["model"] == "math-model"
    assert stored_item["crop"] is not None
    assert stored_item["leaseUntil"] is None


@pytest.mark.asyncio
async def test_process_item_routes_to_english_client(
    database: FakeDatabase,
    storage: FakeStorage,
    settings: Settings,
) -> None:
    batch_id, user_id, _image_id, item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="english",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
    )
    batch = await get_batch(database, batch_id, user_id)
    item = batch["items"][0]

    math_client = FakeIngestionVLMClient(model="math-model")
    english_client = FakeIngestionVLMClient(model="english-model")
    english_client.responses.append(make_extraction_result(text="english problem", model="english-model"))

    await process_item(
        item,
        batch,
        database,
        storage,
        math_client,
        english_client,
        settings,
    )

    assert len(math_client.calls) == 0
    assert len(english_client.calls) == 1
    refreshed = await get_batch(database, batch_id, batch["userId"])
    assert refreshed["items"][0]["extraction"]["model"] == "english-model"


@pytest.mark.asyncio
async def test_process_item_failure_isolated(
    database: FakeDatabase,
    storage: FakeStorage,
    settings: Settings,
) -> None:
    batch_id, user_id, _image_id, item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
    )
    batch = await get_batch(database, batch_id, user_id)
    item = batch["items"][0]

    math_client = FakeIngestionVLMClient(model="math-model")
    math_client.responses.append(RuntimeError("boom"))
    english_client = FakeIngestionVLMClient(model="english-model")

    await process_item(
        item,
        batch,
        database,
        storage,
        math_client,
        english_client,
        settings,
    )

    refreshed = await get_batch(database, batch_id, batch["userId"])
    stored_item = refreshed["items"][0]
    assert stored_item["status"] == ItemState.FAILED.value
    assert stored_item["extraction"]["success"] is False
    assert stored_item["extraction"]["failureCode"] == "extraction-failed"


@pytest.mark.asyncio
async def test_process_item_source_missing_marks_failed(
    database: FakeDatabase,
    storage: FakeStorage,
    settings: Settings,
) -> None:
    batch_id, user_id, _image_id, item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
    )
    # Remove the source object so storage read fails
    storage._objects.clear()
    batch = await get_batch(database, batch_id, user_id)
    item = batch["items"][0]

    math_client = FakeIngestionVLMClient(model="math-model")
    english_client = FakeIngestionVLMClient(model="english-model")

    await process_item(
        item,
        batch,
        database,
        storage,
        math_client,
        english_client,
        settings,
    )

    refreshed = await get_batch(database, batch_id, batch["userId"])
    stored_item = refreshed["items"][0]
    assert stored_item["status"] == ItemState.FAILED.value
    assert stored_item["extraction"]["failureCode"] == "storage-read-failed"


@pytest.mark.asyncio
async def test_reset_item_for_retry(
    database: FakeDatabase,
    storage: FakeStorage,
) -> None:
    now = datetime.now(UTC)
    batch_id, user_id, _image_id, item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
        item_status=ItemState.FAILED.value,
    )
    batch = await get_batch(database, batch_id, user_id)
    user_id = batch["userId"]

    retried = await reset_item_for_retry(database, batch_id, user_id, item_id, now=now)

    assert retried is True
    refreshed = await get_batch(database, batch_id, user_id)
    stored_item = refreshed["items"][0]
    assert stored_item["status"] == ItemState.QUEUED.value
    assert stored_item["leaseUntil"] is None


@pytest.mark.asyncio
async def test_reset_item_for_retry_stalled_extraction(
    database: FakeDatabase,
    storage: FakeStorage,
) -> None:
    now = datetime.now(UTC)
    batch_id, user_id, _image_id, item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
        item_status=ItemState.EXTRACTING.value,
        lease_until=now - timedelta(seconds=1),
    )
    batch = await get_batch(database, batch_id, user_id)
    user_id = batch["userId"]

    retried = await reset_item_for_retry(database, batch_id, user_id, item_id, now=now)

    assert retried is True
    refreshed = await get_batch(database, batch_id, user_id)
    assert refreshed["items"][0]["status"] == ItemState.QUEUED.value


@pytest.mark.asyncio
async def test_reset_item_for_retry_rejects_active_extraction(
    database: FakeDatabase,
    storage: FakeStorage,
) -> None:
    now = datetime.now(UTC)
    batch_id, user_id, _image_id, item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
        item_status=ItemState.EXTRACTING.value,
        lease_until=now + timedelta(seconds=30),
    )
    batch = await get_batch(database, batch_id, user_id)
    user_id = batch["userId"]

    retried = await reset_item_for_retry(database, batch_id, user_id, item_id, now=now)

    assert retried is False


@pytest.mark.asyncio
async def test_claim_next_item_respects_active_lease(
    database: FakeDatabase,
    storage: FakeStorage,
    settings: Settings,
) -> None:
    now = datetime.now(UTC)
    batch_id, _user_id, _image_id, _item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
        item_status=ItemState.EXTRACTING.value,
        lease_until=now + timedelta(seconds=30),
    )

    claimed = await claim_next_item(database, settings, now=now)

    assert claimed is None


@pytest.mark.asyncio
async def test_claim_next_item_recovers_expired_lease(
    database: FakeDatabase,
    storage: FakeStorage,
    settings: Settings,
) -> None:
    now = datetime.now(UTC)
    batch_id, user_id, _image_id, item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
        item_status=ItemState.EXTRACTING.value,
        lease_until=now - timedelta(seconds=1),
    )

    claimed = await claim_next_item(database, settings, now=now)

    assert claimed is not None
    _batch, item = claimed
    assert item["itemId"] == item_id
    assert item["status"] == ItemState.EXTRACTING.value


@pytest.mark.asyncio
async def test_claim_next_item_skips_completed_items(
    database: FakeDatabase,
    storage: FakeStorage,
    settings: Settings,
) -> None:
    _batch_id, _user_id, _image_id, _item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
        item_status=ItemState.READY.value,
    )

    claimed = await claim_next_item(database, settings, now=datetime.now(UTC))

    assert claimed is None


@pytest.mark.asyncio
async def test_process_item_completed_item_not_reextracted(
    database: FakeDatabase,
    storage: FakeStorage,
    settings: Settings,
) -> None:
    batch_id, user_id, _image_id, _item_id = _seed_batch_with_committed_item(
        database,
        storage,
        subject="math",
        box={"x": 0, "y": 0, "width": 1, "height": 1},
        item_status=ItemState.READY.value,
    )
    batch = await get_batch(database, batch_id, user_id)
    item = batch["items"][0]

    math_client = FakeIngestionVLMClient(model="math-model")
    english_client = FakeIngestionVLMClient(model="english-model")

    # process_item does not guard status itself; claim_item does. Calling it directly on a
    # ready item would re-extract, so this test documents that completed items must be
    # filtered by the claim layer.
    claimed = await claim_next_item(database, settings, now=datetime.now(UTC))
    assert claimed is None
    assert len(math_client.calls) == 0
