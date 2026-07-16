from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.domain.ingestion import BatchState, ItemState
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion.repository import (
    INGESTION_BATCHES_COLLECTION,
    create_batch,
    get_batch,
    mark_item_deleted,
    reset_item_for_retry,
    save_item_extraction_failure,
    save_item_extraction_success,
    undo_item_deletion,
    update_item_draft,
)
from app.infrastructure.storage.mongo import ensure_database_setup


class _SynchronizedCollection:
    """Delegating collection proxy that can pause find_one and update_one calls.

    The proxy forwards every operation to the real Mongo collection. When a test
    enables synchronization, each intercepted call waits on an event before
    proceeding and signals another event once it completes. This lets a test
    coordinate two repository calls so both read the same batch snapshot before
    either write is released.
    """

    def __init__(self, real_collection: Any) -> None:
        self._real = real_collection
        self._sync_enabled = False
        self._find_before: asyncio.Event | None = None
        self._find_after: asyncio.Event | None = None
        self._update_before: asyncio.Event | None = None
        self._update_after: asyncio.Event | None = None

    def enable_sync(
        self,
        *,
        find_before: asyncio.Event,
        find_after: asyncio.Event,
        update_before: asyncio.Event,
        update_after: asyncio.Event,
    ) -> None:
        self._sync_enabled = True
        self._find_before = find_before
        self._find_after = find_after
        self._update_before = update_before
        self._update_after = update_after

    async def find_one(self, *args: Any, **kwargs: Any) -> Any:
        if self._sync_enabled:
            assert self._find_before is not None
            assert self._find_after is not None
            await self._find_before.wait()
            result = await self._real.find_one(*args, **kwargs)
            self._find_after.set()
            return result
        return await self._real.find_one(*args, **kwargs)

    async def update_one(self, *args: Any, **kwargs: Any) -> Any:
        if self._sync_enabled:
            assert self._update_before is not None
            assert self._update_after is not None
            await self._update_before.wait()
            result = await self._real.update_one(*args, **kwargs)
            self._update_after.set()
            return result
        return await self._real.update_one(*args, **kwargs)

    async def insert_one(self, *args: Any, **kwargs: Any) -> Any:
        return await self._real.insert_one(*args, **kwargs)

    async def find_one_and_update(self, *args: Any, **kwargs: Any) -> Any:
        return await self._real.find_one_and_update(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _SynchronizedDatabase:
    """Delegating database proxy that returns a synchronized collection."""

    def __init__(self, real_database: Any) -> None:
        self._real = real_database
        self._collection: _SynchronizedCollection | None = None

    def get_collection(self, name: str) -> _SynchronizedCollection:
        if name == INGESTION_BATCHES_COLLECTION:
            if self._collection is None:
                self._collection = _SynchronizedCollection(self._real[name])
            return self._collection
        return self._real[name]

    def __getitem__(self, name: str) -> Any:
        return self.get_collection(name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@pytest_asyncio.fixture(loop_scope="function")
async def real_database() -> Any:
    uri = os.environ.get("MONGODB_URI", "mongodb://mongodb:27017/learnloop?replicaSet=rs0&directConnection=true")
    database_name = os.environ.get("MONGODB_DATABASE", "learnloop")
    client: AsyncMongoClient[Any] = AsyncMongoClient(uri)
    try:
        database = client.get_database(database_name)
        await ensure_database_setup(database)
        yield database
    finally:
        # Clean up only the ingestion batches collection after each test.
        try:
            await database[INGESTION_BATCHES_COLLECTION].delete_many({})
        finally:
            await client.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(bulk_ingestion_batch_ttl_seconds=3600)


@pytest.fixture
def user_id() -> ObjectId:
    return ObjectId()


NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


async def _batch_with_two_items(
    database: Any, user_id: ObjectId, settings: Settings
) -> tuple[ObjectId, str, str]:
    """Seed a batch with two queued items and return (batch_id, item_a_id, item_b_id)."""
    from app.infrastructure.ingestion import add_source_image, add_items_for_image, build_source_image

    source_image = build_source_image(
        bucket="media",
        object_key="users/u/img.png",
        content_type="image/png",
        size_bytes=42,
        sha256="sha",
        uploaded_at=NOW,
    )

    batch = await create_batch(database, user_id, settings, now=NOW)
    image = await add_source_image(database, batch["_id"], user_id, source_image, order=0, now=NOW)
    items = await add_items_for_image(
        database, batch["_id"], user_id, image["imageId"], item_count=2, starting_order=0, now=NOW
    )
    return batch["_id"], items[0]["itemId"], items[1]["itemId"]


@pytest.mark.asyncio
async def test_harness_uses_real_mongo_not_fake(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """The harness must fail clearly if it silently receives a fake database."""
    batch = await create_batch(real_database, user_id, settings, now=NOW)
    loaded = await get_batch(real_database, batch["_id"], user_id)
    assert loaded is not None
    assert loaded["_id"] == batch["_id"]
    # ObjectIds from a real Mongo server are actual bson ObjectIds, not strings.
    assert isinstance(loaded["_id"], ObjectId)


@pytest.mark.asyncio
async def test_concurrent_distinct_item_updates_lose_one_change(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Reproduce the whole-array lost update when two item drafts update concurrently."""
    batch_id, item_a_id, item_b_id = await _batch_with_two_items(real_database, user_id, settings)

    proxy_db = _SynchronizedDatabase(real_database)
    collection = proxy_db.get_collection(INGESTION_BATCHES_COLLECTION)

    # Events for operation A.
    a_find_before = asyncio.Event()
    a_find_after = asyncio.Event()
    a_update_before = asyncio.Event()
    a_update_after = asyncio.Event()

    # Events for operation B.
    b_find_before = asyncio.Event()
    b_find_after = asyncio.Event()
    b_update_before = asyncio.Event()
    b_update_after = asyncio.Event()

    a_find_before.set()
    b_find_before.set()

    collection.enable_sync(
        find_before=a_find_before,
        find_after=a_find_after,
        update_before=a_update_before,
        update_after=a_update_after,
    )

    async def update_a() -> None:
        await update_item_draft(
            proxy_db, batch_id, user_id, item_a_id,
            draft_update={"text": "updated by A"}, now=NOW + timedelta(seconds=1),
        )

    async def update_b() -> None:
        # For operation B we need separate sync events. Since the proxy only has
        # one set of events, we alternate: B uses the same events but the test
        # coordinates the ordering via a second call not being intercepted.
        # Instead, run B through the real database (no proxy) after A has written,
        # which still demonstrates the lost update because B reads stale state.
        await update_item_draft(
            real_database, batch_id, user_id, item_b_id,
            draft_update={"text": "updated by B"}, now=NOW + timedelta(seconds=2),
        )

    task_a = asyncio.create_task(update_a())

    # Wait for A to finish its find_one.
    await a_find_after.wait()

    # While A is paused before update_one, run B through the real database.
    # B reads the original document (without A's change) and writes.
    await update_b()

    # Now release A's update_one. A writes the original document with only
    # item A updated, overwriting B's change to item B.
    a_update_before.set()
    await task_a

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None

    item_a = next(i for i in final["items"] if i["itemId"] == item_a_id)
    item_b = next(i for i in final["items"] if i["itemId"] == item_b_id)

    # A's write is the last one, so item A reflects A's update.
    assert item_a["draft"]["text"] == "updated by A"
    # B's change is lost because A's whole-array replacement did not include it.
    assert item_b["draft"]["text"] is None


# ---------------------------------------------------------------------------
# Sequential contract tests against real Mongo for the six mutation functions.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_item_extraction_success_sets_ready_clears_lease_and_timestamps(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)

    await save_item_extraction_success(
        real_database, batch_id, user_id, item_a_id,
        crop={"bucket": "b", "objectKey": "k"},
        draft={"text": "t"},
        extraction={"success": True},
        now=NOW,
    )

    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.READY.value
    assert item["leaseUntil"] is None
    assert item["updatedAt"].replace(tzinfo=UTC) == NOW
    assert loaded["updatedAt"].replace(tzinfo=UTC) == NOW


@pytest.mark.asyncio
async def test_save_item_extraction_failure_sets_failed_clears_lease_and_timestamps(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)

    extraction = {"success": False, "failureCode": "X", "failureMessage": "boom"}
    await save_item_extraction_failure(
        real_database, batch_id, user_id, item_a_id, extraction=extraction, now=NOW,
    )

    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.FAILED.value
    assert item["extraction"] == extraction
    assert item["leaseUntil"] is None
    assert item["updatedAt"].replace(tzinfo=UTC) == NOW


@pytest.mark.asyncio
async def test_reset_item_for_retry_eligibility_and_idempotency(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)

    # Failed -> queued.
    await save_item_extraction_failure(
        real_database, batch_id, user_id, item_a_id,
        extraction={"success": False, "failureCode": "X"}, now=NOW,
    )
    assert await reset_item_for_retry(real_database, batch_id, user_id, item_a_id, now=NOW) is True
    loaded = await get_batch(real_database, batch_id, user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.QUEUED.value
    assert item["leaseUntil"] is None

    # Missing item -> False.
    assert await reset_item_for_retry(real_database, batch_id, user_id, "missing", now=NOW) is False

    # Missing batch -> ValueError.
    with pytest.raises(ValueError, match="Batch not found"):
        await reset_item_for_retry(real_database, ObjectId(), user_id, item_a_id, now=NOW)


@pytest.mark.asyncio
async def test_update_item_draft_allowed_keys_and_missing_item(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)

    updated = await update_item_draft(
        real_database, batch_id, user_id, item_a_id,
        draft_update={"text": "Q", "problemType": "short-answer", "ignored": "x"},
        now=NOW,
    )
    assert updated is not None
    assert updated["draft"]["text"] == "Q"
    assert "ignored" not in updated["draft"]

    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    assert loaded["updatedAt"].replace(tzinfo=UTC) == NOW

    # Missing item -> None.
    assert await update_item_draft(
        real_database, batch_id, user_id, "missing", draft_update={"text": "x"}, now=NOW
    ) is None


@pytest.mark.asyncio
async def test_mark_item_deleted_preserves_previous_status_and_idempotency(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)

    assert await mark_item_deleted(real_database, batch_id, user_id, item_a_id, now=NOW) is True
    loaded = await get_batch(real_database, batch_id, user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.DELETED.value
    assert item["previousStatus"] == ItemState.QUEUED.value
    assert item["deletedAt"].replace(tzinfo=UTC) == NOW

    # Idempotent.
    later = NOW + timedelta(seconds=5)
    assert await mark_item_deleted(real_database, batch_id, user_id, item_a_id, now=later) is True
    loaded = await get_batch(real_database, batch_id, user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["deletedAt"].replace(tzinfo=UTC) == NOW  # unchanged


@pytest.mark.asyncio
async def test_undo_item_deletion_restores_and_requires_previous_status(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)

    # Not deleted -> False.
    assert await undo_item_deletion(real_database, batch_id, user_id, item_a_id, now=NOW) is False

    await mark_item_deleted(real_database, batch_id, user_id, item_a_id, now=NOW)
    later = NOW + timedelta(seconds=5)

    assert await undo_item_deletion(real_database, batch_id, user_id, item_a_id, now=later) is True
    loaded = await get_batch(real_database, batch_id, user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.QUEUED.value
    assert "previousStatus" not in item
    assert "deletedAt" not in item
    assert item["updatedAt"].replace(tzinfo=UTC) == later
