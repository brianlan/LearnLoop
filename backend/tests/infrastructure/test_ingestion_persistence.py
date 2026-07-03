from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId
from botocore.exceptions import ClientError

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
from tests.conftest import FakeDatabase, FakeStorage


class StrictStorage(FakeStorage):
    """Raises when deleting a missing object so cleanup exception handling is exercised."""

    def delete_object(self, bucket: str, key: str) -> None:
        if (bucket, key) not in self._objects:
            raise StorageObjectNotFoundError(key)
        super().delete_object(bucket, key)


class AccessDeniedStorage(FakeStorage):
    """Simulates a non-missing object-store failure so cleanup does not swallow real errors."""

    def delete_object(self, bucket: str, key: str) -> None:
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "DeleteObject",
        )


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
async def test_cleanup_re_raises_non_missing_object_store_errors(
    database: FakeDatabase,
    storage: FakeStorage,
    user_id: ObjectId,
    settings: Settings,
) -> None:
    storage = AccessDeniedStorage()
    batch = await create_batch(database, user_id, settings)
    source_image = build_source_image(
        bucket="media",
        object_key="users/u/denied.png",
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

    with pytest.raises(ClientError):
        await run_batch_cleanup(database, storage)

    loaded = await get_batch(database, batch["_id"], user_id)
    assert loaded is not None
    assert loaded["status"] == BatchState.EXPIRED.value


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
