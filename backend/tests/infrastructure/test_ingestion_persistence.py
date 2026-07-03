from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId

from app.domain.ingestion import BatchState, ImageState, ItemState
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion import (
    add_items_for_image,
    add_source_image,
    build_source_image,
    cleanup_batch_media,
    create_batch,
    find_cleanup_candidates,
    get_active_batch_for_user,
    get_batch,
    is_batch_expired,
    mark_batch_cleaned,
    run_batch_cleanup,
)
from app.infrastructure.storage.s3 import StorageObjectNotFoundError


class FakeInsertOneResult:
    def __init__(self, inserted_id: ObjectId) -> None:
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = [deepcopy(document) for document in documents]

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        documents = self._documents
        if length is not None:
            documents = documents[:length]
        return [deepcopy(document) for document in documents]


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$or":
            return any(_matches(document, subquery) for subquery in expected)
        actual = document.get(key)
        if isinstance(expected, dict):
            for op, value in expected.items():
                if op == "$in":
                    if actual not in value:
                        return False
                elif op == "$gt":
                    if actual is None or actual <= value:
                        return False
                elif op == "$gte":
                    if actual is None or actual < value:
                        return False
                elif op == "$lt":
                    if actual is None or actual >= value:
                        return False
                elif op == "$lte":
                    if actual is None or actual > value:
                        return False
            continue
        if isinstance(actual, list):
            if expected not in actual:
                return False
            continue
        if actual != expected:
            return False
    return True


class FakeCollection:
    def __init__(self) -> None:
        self._documents: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any]) -> FakeInsertOneResult:
        stored = deepcopy(document)
        if "_id" not in stored:
            stored["_id"] = ObjectId()
        self._documents.append(stored)
        return FakeInsertOneResult(stored["_id"])

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for document in self._documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

    def find(self, query: dict[str, Any]) -> FakeCursor:
        return FakeCursor([document for document in self._documents if _matches(document, query)])

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
    ) -> FakeUpdateResult:
        for document in self._documents:
            if not _matches(document, query):
                continue
            for key, value in update.get("$set", {}).items():
                document[key] = deepcopy(value)
            return FakeUpdateResult(1)
        return FakeUpdateResult(0)


class FakeDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections.setdefault(name, FakeCollection())


class FakeStorage:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def seed(self, bucket: str, key: str, payload: bytes) -> None:
        self._objects[(bucket, key)] = payload

    def put_object(
        self,
        bucket: str,
        key: str,
        payload: bytes,
        content_type: str | None = None,
    ) -> None:
        self._objects[(bucket, key)] = payload

    def get_object(self, bucket: str, key: str) -> bytes:
        payload = self._objects.get((bucket, key))
        if payload is None:
            raise StorageObjectNotFoundError(key)
        return payload

    def delete_object(self, bucket: str, key: str) -> None:
        self._objects.pop((bucket, key), None)


class StrictStorage(FakeStorage):
    """Raises when deleting a missing object so cleanup exception handling is exercised."""

    def delete_object(self, bucket: str, key: str) -> None:
        if (bucket, key) not in self._objects:
            raise StorageObjectNotFoundError(key)
        super().delete_object(bucket, key)


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def settings() -> Settings:
    return Settings(bulk_ingestion_batch_ttl_seconds=3600)


@pytest.fixture
def user_id() -> ObjectId:
    return ObjectId()


@pytest.mark.asyncio
async def test_create_batch_persists_and_loads(
    database: FakeDatabase,
    user_id: ObjectId,
    settings: Settings,
) -> None:
    batch = await create_batch(database, user_id, settings)

    loaded = await get_batch(database, batch["_id"], user_id)
    assert loaded is not None
    assert loaded["userId"] == user_id
    assert loaded["status"] == BatchState.ACTIVE.value
    assert loaded["images"] == []
    assert loaded["items"] == []
    assert loaded["expiresAt"] > loaded["createdAt"]


@pytest.mark.asyncio
async def test_get_active_batch_for_user_returns_unexpired_active(
    database: FakeDatabase,
    user_id: ObjectId,
    settings: Settings,
) -> None:
    now = datetime.now(UTC)
    await create_batch(database, user_id, settings, now=now)

    active = await get_active_batch_for_user(database, user_id, now=now)
    assert active is not None
    assert active["status"] == BatchState.ACTIVE.value


@pytest.mark.asyncio
async def test_get_active_batch_for_user_ignores_expired(
    database: FakeDatabase,
    user_id: ObjectId,
) -> None:
    now = datetime.now(UTC)
    short_settings = Settings(bulk_ingestion_batch_ttl_seconds=1)
    await create_batch(database, user_id, short_settings, now=now)

    active = await get_active_batch_for_user(database, user_id, now=now + timedelta(seconds=2))
    assert active is None


@pytest.mark.asyncio
async def test_add_source_image_round_trips(
    database: FakeDatabase,
    user_id: ObjectId,
    settings: Settings,
) -> None:
    batch = await create_batch(database, user_id, settings)
    source_image = build_source_image(
        bucket="media",
        object_key="users/u/img.png",
        content_type="image/png",
        size_bytes=42,
        sha256="sha",
        uploaded_at=datetime.now(UTC),
    )

    image = await add_source_image(
        database,
        batch["_id"],
        user_id,
        source_image,
        order=0,
    )

    loaded = await get_batch(database, batch["_id"], user_id)
    assert loaded is not None
    assert len(loaded["images"]) == 1
    stored_image = loaded["images"][0]
    assert stored_image["imageId"] == image["imageId"]
    assert stored_image["status"] == ImageState.UPLOADED.value
    assert stored_image["sourceImage"] == source_image
    assert stored_image["detection"]["failureCode"] is None


@pytest.mark.asyncio
async def test_add_items_for_image_commits_image_and_creates_items(
    database: FakeDatabase,
    user_id: ObjectId,
    settings: Settings,
) -> None:
    batch = await create_batch(database, user_id, settings)
    source_image = build_source_image(
        bucket="media",
        object_key="users/u/img.png",
        content_type="image/png",
        size_bytes=42,
        sha256="sha",
        uploaded_at=datetime.now(UTC),
    )
    image = await add_source_image(database, batch["_id"], user_id, source_image, order=0)

    items = await add_items_for_image(
        database,
        batch["_id"],
        user_id,
        image["imageId"],
        item_count=2,
        starting_order=0,
    )

    loaded = await get_batch(database, batch["_id"], user_id)
    assert loaded is not None
    assert len(loaded["images"]) == 1
    assert loaded["images"][0]["status"] == ImageState.COMMITTED.value
    assert loaded["images"][0]["committedAt"] is not None
    assert len(loaded["items"]) == 2
    for index, item in enumerate(loaded["items"]):
        assert item["itemId"] == items[index]["itemId"]
        assert item["imageId"] == image["imageId"]
        assert item["status"] == ItemState.QUEUED.value
        assert item["draft"] == {
            "text": None,
            "problemType": None,
            "graphDsl": None,
            "correctAnswer": None,
            "tags": [],
            "subject": None,
        }
        assert item["extraction"]["failureCode"] is None
        assert item["retryCount"] == 0
        assert item["submit"]["submittedProblemId"] is None
        assert item["origin"]["batchId"] == str(batch["_id"])
        assert item["origin"]["itemId"] == item["itemId"]


@pytest.mark.asyncio
async def test_find_cleanup_candidates_includes_expired_and_completed(
    database: FakeDatabase,
    user_id: ObjectId,
) -> None:
    now = datetime.now(UTC)
    short_settings = Settings(bulk_ingestion_batch_ttl_seconds=1)

    expired_active = await create_batch(database, user_id, short_settings, now=now)
    completed = await create_batch(database, user_id, Settings(bulk_ingestion_batch_ttl_seconds=3600), now=now)
    await database["ingestion_batches"].update_one(
        {"_id": completed["_id"]},
        {"$set": {"status": BatchState.COMPLETED.value}},
    )
    expired_terminal = await create_batch(database, user_id, short_settings, now=now)
    await database["ingestion_batches"].update_one(
        {"_id": expired_terminal["_id"]},
        {"$set": {"status": BatchState.EXPIRED.value}},
    )
    fresh = await create_batch(database, user_id, Settings(bulk_ingestion_batch_ttl_seconds=3600), now=now)

    candidates = await find_cleanup_candidates(database, now=now + timedelta(seconds=2))
    candidate_ids = {candidate["_id"] for candidate in candidates}

    assert expired_active["_id"] in candidate_ids
    assert completed["_id"] in candidate_ids
    assert expired_terminal["_id"] in candidate_ids
    assert fresh["_id"] not in candidate_ids


@pytest.mark.asyncio
async def test_cleanup_deletes_source_and_crop_media(
    database: FakeDatabase,
    storage: FakeStorage,
    user_id: ObjectId,
    settings: Settings,
) -> None:
    batch = await create_batch(database, user_id, settings)
    source_image = build_source_image(
        bucket="media",
        object_key="users/u/source.png",
        content_type="image/png",
        size_bytes=10,
        sha256="sha",
        uploaded_at=datetime.now(UTC),
    )
    image = await add_source_image(database, batch["_id"], user_id, source_image, order=0)
    items = await add_items_for_image(
        database, batch["_id"], user_id, image["imageId"], item_count=1, starting_order=0
    )

    crop_key = "users/u/crop.png"
    items[0]["crop"] = {"bucket": "media", "objectKey": crop_key}
    await database["ingestion_batches"].update_one(
        {"_id": batch["_id"]},
        {"$set": {"items": items}},
    )
    storage.seed("media", source_image["objectKey"], b"source")
    storage.seed("media", crop_key, b"crop")
    await database["ingestion_batches"].update_one(
        {"_id": batch["_id"]},
        {"$set": {"status": BatchState.EXPIRED.value}},
    )

    cleaned = await run_batch_cleanup(database, storage)

    assert cleaned == 1
    with pytest.raises(StorageObjectNotFoundError):
        storage.get_object("media", source_image["objectKey"])
    with pytest.raises(StorageObjectNotFoundError):
        storage.get_object("media", crop_key)
    loaded = await get_batch(database, batch["_id"], user_id)
    assert loaded is not None
    assert loaded["status"] == BatchState.DELETED.value


@pytest.mark.asyncio
async def test_cleanup_ignores_missing_media(
    database: FakeDatabase,
    user_id: ObjectId,
    settings: Settings,
) -> None:
    storage = StrictStorage()
    batch = await create_batch(database, user_id, settings)
    source_image = build_source_image(
        bucket="media",
        object_key="users/u/missing.png",
        content_type="image/png",
        size_bytes=10,
        sha256="sha",
        uploaded_at=datetime.now(UTC),
    )
    await add_source_image(database, batch["_id"], user_id, source_image, order=0)
    await database["ingestion_batches"].update_one(
        {"_id": batch["_id"]},
        {"$set": {"status": BatchState.EXPIRED.value}},
    )

    cleaned = await run_batch_cleanup(database, storage)

    assert cleaned == 1
    loaded = await get_batch(database, batch["_id"], user_id)
    assert loaded is not None
    assert loaded["status"] == BatchState.DELETED.value


@pytest.mark.asyncio
async def test_cleanup_does_not_touch_unrelated_media(
    database: FakeDatabase,
    storage: FakeStorage,
    user_id: ObjectId,
    settings: Settings,
) -> None:
    batch = await create_batch(database, user_id, settings)
    source_image = build_source_image(
        bucket="media",
        object_key="users/u/source.png",
        content_type="image/png",
        size_bytes=10,
        sha256="sha",
        uploaded_at=datetime.now(UTC),
    )
    await add_source_image(database, batch["_id"], user_id, source_image, order=0)
    await database["ingestion_batches"].update_one(
        {"_id": batch["_id"]},
        {"$set": {"status": BatchState.EXPIRED.value}},
    )
    storage.seed("media", "users/u/other.png", b"other")
    storage.seed("media", source_image["objectKey"], b"source")

    await run_batch_cleanup(database, storage)

    assert storage.get_object("media", "users/u/other.png") == b"other"


@pytest.mark.asyncio
async def test_is_batch_expired(
    database: FakeDatabase,
    user_id: ObjectId,
) -> None:
    now = datetime.now(UTC)
    batch = await create_batch(database, user_id, Settings(bulk_ingestion_batch_ttl_seconds=1), now=now)

    assert is_batch_expired(batch, now=now + timedelta(seconds=2)) is True
    assert is_batch_expired(batch, now=now) is False


@pytest.mark.asyncio
async def test_mark_batch_cleaned(
    database: FakeDatabase,
    user_id: ObjectId,
    settings: Settings,
) -> None:
    batch = await create_batch(database, user_id, settings)
    await mark_batch_cleaned(database, batch["_id"])

    loaded = await get_batch(database, batch["_id"], user_id)
    assert loaded is not None
    assert loaded["status"] == BatchState.DELETED.value
