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

from app.domain.ingestion import ItemState
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
    uri = os.environ.get("MONGODB_URI")
    if uri is None:
        pytest.skip("real Mongo integration tests require MONGODB_URI (run through agent-env.sh)")

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


async def _set_item_fields(
    database: Any,
    batch_id: ObjectId,
    user_id: ObjectId,
    item_id: str,
    **fields: Any,
) -> None:
    result = await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id, "userId": user_id, "items.itemId": item_id},
        {"$set": {f"items.$.{key}": value for key, value in fields.items()}},
    )
    assert result.modified_count == 1


@pytest.mark.asyncio
async def test_harness_uses_real_mongo_not_fake(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """The harness must fail clearly if it silently receives a fake database."""
    assert isinstance(real_database.client, AsyncMongoClient)
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

    proxy_a = _SynchronizedDatabase(real_database)
    proxy_b = _SynchronizedDatabase(real_database)
    collection_a = proxy_a.get_collection(INGESTION_BATCHES_COLLECTION)
    collection_b = proxy_b.get_collection(INGESTION_BATCHES_COLLECTION)

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

    collection_a.enable_sync(
        find_before=a_find_before,
        find_after=a_find_after,
        update_before=a_update_before,
        update_after=a_update_after,
    )
    collection_b.enable_sync(
        find_before=b_find_before,
        find_after=b_find_after,
        update_before=b_update_before,
        update_after=b_update_after,
    )

    async def update_a() -> None:
        await update_item_draft(
            proxy_a, batch_id, user_id, item_a_id,
            draft_update={"text": "updated by A"}, now=NOW + timedelta(seconds=1),
        )

    async def update_b() -> None:
        await update_item_draft(
            proxy_b, batch_id, user_id, item_b_id,
            draft_update={"text": "updated by B"}, now=NOW + timedelta(seconds=2),
        )

    task_a = asyncio.create_task(update_a())
    task_b = asyncio.create_task(update_b())

    # Both real reads finish before either real write is released.
    await asyncio.gather(a_find_after.wait(), b_find_after.wait())
    assert not a_update_after.is_set()
    assert not b_update_after.is_set()

    # Write B first, then A. A still holds the original snapshot and therefore
    # overwrites B's distinct-item change when its full items array is persisted.
    b_update_before.set()
    await b_update_after.wait()
    await task_b
    assert not a_update_after.is_set()

    a_update_before.set()
    await a_update_after.wait()
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
    await _set_item_fields(
        real_database,
        batch_id,
        user_id,
        item_a_id,
        status=ItemState.EXTRACTING.value,
        leaseUntil=NOW + timedelta(minutes=5),
    )

    crop = {"bucket": "b", "objectKey": "k"}
    draft = {"text": "t"}
    extraction = {"success": True}

    result = await save_item_extraction_success(
        real_database, batch_id, user_id, item_a_id,
        crop=crop,
        draft=draft,
        extraction=extraction,
        now=NOW,
    )

    assert result is None
    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.READY.value
    assert item["crop"] == crop
    assert item["draft"] == draft
    assert item["extraction"] == extraction
    assert item["leaseUntil"] is None
    assert item["updatedAt"].replace(tzinfo=UTC) == NOW
    assert loaded["updatedAt"].replace(tzinfo=UTC) == NOW

    later = NOW + timedelta(seconds=1)
    items_before = loaded["items"]
    assert await save_item_extraction_success(
        real_database, batch_id, user_id, "missing",
        crop={}, draft={}, extraction={}, now=later,
    ) is None
    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    assert loaded["items"] == items_before
    assert loaded["updatedAt"].replace(tzinfo=UTC) == later


@pytest.mark.asyncio
async def test_save_item_extraction_failure_sets_failed_clears_lease_and_timestamps(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)
    await _set_item_fields(
        real_database,
        batch_id,
        user_id,
        item_a_id,
        status=ItemState.EXTRACTING.value,
        leaseUntil=NOW + timedelta(minutes=5),
    )

    extraction = {"success": False, "failureCode": "X", "failureMessage": "boom"}
    result = await save_item_extraction_failure(
        real_database, batch_id, user_id, item_a_id, extraction=extraction, now=NOW,
    )

    assert result is None
    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.FAILED.value
    assert item["extraction"] == extraction
    assert item["leaseUntil"] is None
    assert item["updatedAt"].replace(tzinfo=UTC) == NOW
    assert loaded["updatedAt"].replace(tzinfo=UTC) == NOW

    later = NOW + timedelta(seconds=1)
    items_before = loaded["items"]
    assert await save_item_extraction_failure(
        real_database, batch_id, user_id, "missing", extraction={}, now=later,
    ) is None
    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    assert loaded["items"] == items_before
    assert loaded["updatedAt"].replace(tzinfo=UTC) == later


@pytest.mark.asyncio
async def test_reset_item_for_retry_eligibility_and_idempotency(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)

    eligible_states = [
        (ItemState.FAILED.value, None),
        (ItemState.SUBMIT_FAILED.value, None),
    ]
    for index, (status, lease_until) in enumerate(eligible_states, start=1):
        await _set_item_fields(
            real_database,
            batch_id,
            user_id,
            item_a_id,
            status=status,
            leaseUntil=lease_until,
        )
        changed_at = NOW + timedelta(seconds=index)
        assert await reset_item_for_retry(
            real_database, batch_id, user_id, item_a_id, now=changed_at,
        ) is True
        loaded = await get_batch(real_database, batch_id, user_id)
        assert loaded is not None
        item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
        assert item["status"] == ItemState.QUEUED.value
        assert item["leaseUntil"] is None
        assert item["updatedAt"].replace(tzinfo=UTC) == changed_at
        assert loaded["updatedAt"].replace(tzinfo=UTC) == changed_at

    await _set_item_fields(
        real_database,
        batch_id,
        user_id,
        item_a_id,
        status=ItemState.EXTRACTING.value,
        leaseUntil=NOW - timedelta(seconds=1),
    )
    before = await get_batch(real_database, batch_id, user_id)
    assert before is not None
    with pytest.raises(TypeError, match="offset-naive and offset-aware"):
        await reset_item_for_retry(real_database, batch_id, user_id, item_a_id, now=NOW)
    assert await get_batch(real_database, batch_id, user_id) == before

    await _set_item_fields(
        real_database,
        batch_id,
        user_id,
        item_a_id,
        status=ItemState.QUEUED.value,
        leaseUntil=None,
    )
    before = await get_batch(real_database, batch_id, user_id)
    assert before is not None
    assert await reset_item_for_retry(
        real_database, batch_id, user_id, item_a_id, now=NOW + timedelta(minutes=1),
    ) is False
    assert await get_batch(real_database, batch_id, user_id) == before

    assert await reset_item_for_retry(real_database, batch_id, user_id, "missing", now=NOW) is False
    assert await get_batch(real_database, batch_id, user_id) == before


@pytest.mark.asyncio
async def test_update_item_draft_allowed_keys_and_missing_item(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)

    draft_update = {
        "text": "Q",
        "problemType": "short-answer",
        "graphDsl": "graph TD",
        "correctAnswer": "42",
        "tags": ["algebra"],
        "subject": "math",
        "ignored": "x",
    }
    updated = await update_item_draft(
        real_database, batch_id, user_id, item_a_id,
        draft_update=draft_update,
        now=NOW,
    )
    assert updated is not None
    for key in {"text", "problemType", "graphDsl", "correctAnswer", "tags", "subject"}:
        assert updated["draft"][key] == draft_update[key]
    assert "ignored" not in updated["draft"]
    assert updated["updatedAt"] == NOW

    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    stored = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert stored["draft"] == updated["draft"]
    assert stored["updatedAt"].replace(tzinfo=UTC) == NOW
    assert loaded["updatedAt"].replace(tzinfo=UTC) == NOW

    before = loaded
    assert await update_item_draft(
        real_database, batch_id, user_id, "missing", draft_update={"text": "x"}, now=NOW
    ) is None
    assert await get_batch(real_database, batch_id, user_id) == before


@pytest.mark.asyncio
async def test_mark_item_deleted_preserves_previous_status_and_idempotency(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, item_b_id = await _batch_with_two_items(real_database, user_id, settings)

    assert await mark_item_deleted(real_database, batch_id, user_id, item_a_id, now=NOW) is True
    loaded = await get_batch(real_database, batch_id, user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.DELETED.value
    assert item["previousStatus"] == ItemState.QUEUED.value
    assert item["deletedAt"].replace(tzinfo=UTC) == NOW
    assert item["updatedAt"].replace(tzinfo=UTC) == NOW
    assert loaded["updatedAt"].replace(tzinfo=UTC) == NOW

    before = loaded
    later = NOW + timedelta(seconds=5)
    assert await mark_item_deleted(real_database, batch_id, user_id, item_a_id, now=later) is True
    assert await get_batch(real_database, batch_id, user_id) == before

    await _set_item_fields(
        real_database, batch_id, user_id, item_b_id, status=ItemState.SUBMITTED.value,
    )
    before = await get_batch(real_database, batch_id, user_id)
    assert before is not None
    assert await mark_item_deleted(real_database, batch_id, user_id, item_b_id, now=later) is True
    assert await get_batch(real_database, batch_id, user_id) == before

    assert await mark_item_deleted(real_database, batch_id, user_id, "missing", now=later) is False
    assert await get_batch(real_database, batch_id, user_id) == before


@pytest.mark.asyncio
async def test_undo_item_deletion_restores_and_requires_previous_status(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, item_b_id = await _batch_with_two_items(real_database, user_id, settings)

    before = await get_batch(real_database, batch_id, user_id)
    assert before is not None
    assert await undo_item_deletion(real_database, batch_id, user_id, item_a_id, now=NOW) is False
    assert await get_batch(real_database, batch_id, user_id) == before

    await mark_item_deleted(real_database, batch_id, user_id, item_a_id, now=NOW)
    later = NOW + timedelta(seconds=5)

    assert await undo_item_deletion(real_database, batch_id, user_id, item_a_id, now=later) is True
    loaded = await get_batch(real_database, batch_id, user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.QUEUED.value
    assert "previousStatus" not in item
    assert "deletedAt" not in item
    assert item["updatedAt"].replace(tzinfo=UTC) == later
    assert loaded["updatedAt"].replace(tzinfo=UTC) == later

    await _set_item_fields(
        real_database,
        batch_id,
        user_id,
        item_b_id,
        status=ItemState.DELETED.value,
        deletedAt=NOW,
    )
    before = await get_batch(real_database, batch_id, user_id)
    assert before is not None
    assert await undo_item_deletion(real_database, batch_id, user_id, item_b_id, now=later) is False
    assert await get_batch(real_database, batch_id, user_id) == before

    assert await undo_item_deletion(real_database, batch_id, user_id, "missing", now=later) is False
    assert await get_batch(real_database, batch_id, user_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "kwargs"),
    [
        (save_item_extraction_success, {"crop": {}, "draft": {}, "extraction": {}}),
        (save_item_extraction_failure, {"extraction": {}}),
        (reset_item_for_retry, {}),
        (update_item_draft, {"draft_update": {}}),
        (mark_item_deleted, {}),
        (undo_item_deletion, {}),
    ],
    ids=[
        "extraction-success",
        "extraction-failure",
        "reset-retry",
        "update-draft",
        "delete",
        "undo-delete",
    ],
)
async def test_mutations_raise_for_missing_batch(
    real_database: Any,
    user_id: ObjectId,
    mutation: Callable[..., Awaitable[Any]],
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="Batch not found"):
        await mutation(real_database, ObjectId(), user_id, "item", now=NOW, **kwargs)
