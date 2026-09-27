from __future__ import annotations

import asyncio
import contextlib
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.domain.ingestion import ItemState
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion import (
    add_items_for_image,
    add_source_image,
    build_source_image,
)
from app.infrastructure.ingestion.repository import (
    INGESTION_BATCHES_COLLECTION,
    commit_image_boxes,
    create_batch,
    delete_batch_image,
    get_batch,
    mark_item_deleted,
    reset_item_for_retry,
    save_item_extraction_failure,
    save_item_extraction_success,
    undo_item_deletion,
    update_item_draft,
)
from app.infrastructure.storage.mongo import ensure_database_setup

# The whole module requires a real MongoDB server (or fakes for the
# control-flow tests); it is excluded from the Docker-free fast tier.
pytestmark = pytest.mark.real_mongo

REAL_MONGO_DATABASE_ENV = "LEARNLOOP_REAL_MONGO_DATABASE"
_REAL_MONGO_NAME_RE = re.compile(r"learnloop_test_[A-Za-z0-9_-]+")


def validate_real_mongo_database_name(name: str | None) -> str:
    """Return ``name`` only when it is an externally supplied sentinel test db.

    Raises ``RuntimeError`` for any absent, empty, or non-sentinel value so
    the real-Mongo fixture can never write to or clean a shared/production
    database. The match is a full-string match: no trimming or substring
    acceptance is allowed.
    """
    if name is None or not _REAL_MONGO_NAME_RE.fullmatch(name):
        raise RuntimeError(
            f"{REAL_MONGO_DATABASE_ENV} must match 'learnloop_test_[A-Za-z0-9_-]+'; "
            f"got {name!r}"
        )
    return name


async def _real_database_core(
    *,
    client_factory: Callable[[str], Any],
    ensure_setup: Callable[[Any], Awaitable[None]],
) -> AsyncIterator[Any]:
    """Real-Mongo fixture core, parameterized for testability.

    Reads ``MONGODB_URI`` (skips if absent) and the sentinel
    ``LEARNLOOP_REAL_MONGO_DATABASE`` name. A missing sentinel name skips
    (not opted in); a present-but-invalid name raises ``RuntimeError`` before
    any database setup/write. The name is revalidated immediately before
    dropping the whole database in teardown. ``client_factory`` and
    ``ensure_setup`` are injected so tests can substitute fakes without
    touching a Mongo server.
    """
    uri = os.environ.get("MONGODB_URI")
    if uri is None:
        pytest.skip("real Mongo integration tests require MONGODB_URI (run through agent-env.sh)")

    raw_name = os.environ.get(REAL_MONGO_DATABASE_ENV)
    if raw_name is None:
        pytest.skip(
            f"real Mongo integration tests require {REAL_MONGO_DATABASE_ENV} "
            f"(run through agent-env.sh)"
        )
    database_name = validate_real_mongo_database_name(raw_name)
    client = client_factory(uri)
    try:
        database = client.get_database(database_name)
        await ensure_setup(database)
        yield database
    finally:
        # Drop the whole sentinel-prefixed test database, revalidating the
        # name immediately before the destructive call so no cleanup path can
        # target a non-sentinel database even if the environment changed mid-run.
        try:
            validated = validate_real_mongo_database_name(
                os.environ.get(REAL_MONGO_DATABASE_ENV)
            )
            await client.drop_database(validated)
        finally:
            await client.close()


@pytest_asyncio.fixture(loop_scope="function")
async def real_database() -> Any:
    async for database in _real_database_core(
        client_factory=AsyncMongoClient,
        ensure_setup=ensure_database_setup,
    ):
        yield database


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


async def _add_uncommitted_image_with_boxes(
    database: Any,
    batch_id: ObjectId,
    user_id: ObjectId,
    *,
    boxes: list[dict[str, Any]],
    order: int = 1,
) -> str:
    """Add a second image in ready state with boxes, ready for commit_image_boxes."""
    source_image = build_source_image(
        bucket="media",
        object_key=f"users/u/img-{order}.png",
        content_type="image/png",
        size_bytes=42,
        sha256=f"sha-{order}",
        uploaded_at=NOW,
    )
    image = await add_source_image(database, batch_id, user_id, source_image, order=order, now=NOW)
    result = await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id, "userId": user_id, "images.imageId": image["imageId"]},
        {"$set": {"images.$.boxes": boxes, "images.$.status": "ready", "images.$.subject": "math"}},
    )
    assert result.modified_count == 1
    return image["imageId"]


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
    assert result.matched_count == 1


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
async def test_concurrent_distinct_item_draft_updates_both_survive(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Two synchronized draft edits to distinct items must both survive."""
    batch_id, item_a_id, item_b_id = await _batch_with_two_items(real_database, user_id, settings)

    barrier = asyncio.Event()

    async def update_a() -> None:
        await barrier.wait()
        await update_item_draft(
            real_database, batch_id, user_id, item_a_id,
            draft_update={"text": "updated by A"}, now=NOW + timedelta(seconds=1),
        )

    async def update_b() -> None:
        await barrier.wait()
        await update_item_draft(
            real_database, batch_id, user_id, item_b_id,
            draft_update={"text": "updated by B"}, now=NOW + timedelta(seconds=2),
        )

    task_a = asyncio.create_task(update_a())
    task_b = asyncio.create_task(update_b())
    # Let both tasks reach the barrier so the two mutations truly overlap.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    barrier.set()
    await asyncio.gather(task_a, task_b)

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None

    item_a = next(i for i in final["items"] if i["itemId"] == item_a_id)
    item_b = next(i for i in final["items"] if i["itemId"] == item_b_id)
    assert item_a["draft"]["text"] == "updated by A"
    assert item_b["draft"]["text"] == "updated by B"


@pytest.mark.asyncio
async def test_extraction_completion_and_distinct_draft_edit_both_survive(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """A worker completing item A must not erase a concurrent draft edit to item B."""
    batch_id, item_a_id, item_b_id = await _batch_with_two_items(real_database, user_id, settings)
    lease = NOW + timedelta(minutes=5)
    await _set_item_fields(
        real_database, batch_id, user_id, item_a_id,
        status=ItemState.EXTRACTING.value, leaseUntil=lease,
    )

    barrier = asyncio.Event()

    async def complete_a() -> None:
        await barrier.wait()
        saved = await save_item_extraction_success(
            real_database, batch_id, user_id, item_a_id,
            crop={"bucket": "b", "objectKey": "k"},
            draft={"text": "A result"},
            extraction={"success": True},
            lease_until=lease,
            now=NOW + timedelta(seconds=1),
        )
        assert saved is True

    async def edit_b() -> None:
        await barrier.wait()
        await update_item_draft(
            real_database, batch_id, user_id, item_b_id,
            draft_update={"text": "edited B"}, now=NOW + timedelta(seconds=2),
        )

    task_a = asyncio.create_task(complete_a())
    task_b = asyncio.create_task(edit_b())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    barrier.set()
    await asyncio.gather(task_a, task_b)

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None
    item_a = next(i for i in final["items"] if i["itemId"] == item_a_id)
    item_b = next(i for i in final["items"] if i["itemId"] == item_b_id)
    assert item_a["status"] == ItemState.READY.value
    assert item_a["draft"]["text"] == "A result"
    assert item_b["draft"]["text"] == "edited B"


@pytest.mark.asyncio
async def test_stale_extraction_owner_cannot_replace_reclaimed_lease(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """A result from a lease lost to a reclaim must be discarded, not overwrite."""
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)

    first_lease = NOW + timedelta(seconds=30)
    await _set_item_fields(
        real_database, batch_id, user_id, item_a_id,
        status=ItemState.EXTRACTING.value, leaseUntil=first_lease,
    )

    # Simulated reclaim: a newer owner takes the item with a fresh lease.
    second_lease = NOW + timedelta(seconds=90)
    await _set_item_fields(
        real_database, batch_id, user_id, item_a_id, leaseUntil=second_lease,
    )

    # The stale owner's completion targets the old lease and must be rejected.
    stale_saved = await save_item_extraction_success(
        real_database, batch_id, user_id, item_a_id,
        crop={"bucket": "b", "objectKey": "stale"},
        draft={"text": "stale result"},
        extraction={"success": True},
        lease_until=first_lease,
        now=NOW + timedelta(seconds=1),
    )
    assert stale_saved is False

    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.EXTRACTING.value
    assert item["leaseUntil"].replace(tzinfo=UTC) == second_lease
    assert "draft" not in item or item["draft"].get("text") is None

    # The current owner can still complete the item.
    fresh_saved = await save_item_extraction_success(
        real_database, batch_id, user_id, item_a_id,
        crop={"bucket": "b", "objectKey": "fresh"},
        draft={"text": "fresh result"},
        extraction={"success": True},
        lease_until=second_lease,
        now=NOW + timedelta(seconds=2),
    )
    assert fresh_saved is True
    loaded = await get_batch(real_database, batch_id, user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.READY.value
    assert item["draft"]["text"] == "fresh result"


@pytest.mark.asyncio
async def test_extraction_failure_cannot_resurrect_deleted_item(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Completion writes must not touch a deleted item."""
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)
    lease = NOW + timedelta(minutes=5)
    await _set_item_fields(
        real_database, batch_id, user_id, item_a_id,
        status=ItemState.EXTRACTING.value, leaseUntil=lease,
    )
    assert await mark_item_deleted(real_database, batch_id, user_id, item_a_id, now=NOW) is True

    saved = await save_item_extraction_failure(
        real_database, batch_id, user_id, item_a_id,
        extraction={"success": False, "failureCode": "X"},
        lease_until=lease,
        now=NOW + timedelta(seconds=1),
    )
    assert saved is False

    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.DELETED.value


@pytest.mark.asyncio
async def test_commit_racing_with_edit_preserves_both(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Commit appending items must not erase a concurrent edit to an existing item."""
    batch_id, existing_item_id, _ = await _batch_with_two_items(real_database, user_id, settings)
    # A second, uncommitted image with ready boxes for the commit to append.
    commit_image = await _add_uncommitted_image_with_boxes(
        real_database, batch_id, user_id,
        boxes=[{"x": 1, "y": 1, "w": 2, "h": 2}],
    )

    barrier = asyncio.Event()

    async def do_commit() -> None:
        await barrier.wait()
        await commit_image_boxes(real_database, batch_id, user_id, commit_image, now=NOW + timedelta(seconds=1))

    async def do_edit() -> None:
        await barrier.wait()
        await update_item_draft(
            real_database, batch_id, user_id, existing_item_id,
            draft_update={"text": "edited during commit"}, now=NOW + timedelta(seconds=2),
        )

    task_a = asyncio.create_task(do_commit())
    task_b = asyncio.create_task(do_edit())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    barrier.set()
    await asyncio.gather(task_a, task_b)

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None
    # Existing item kept its edit.
    edited = next(i for i in final["items"] if i["itemId"] == existing_item_id)
    assert edited["draft"]["text"] == "edited during commit"
    # Appended item exists exactly once alongside the original two.
    assert len(final["items"]) == 3
    orders = [i["order"] for i in final["items"]]
    assert len(orders) == len(set(orders))


@pytest.mark.asyncio
async def test_concurrent_commits_do_not_duplicate_items_or_orders(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Two overlapping commits of the same image must land exactly once."""
    batch_id, _existing_item_id, _ = await _batch_with_two_items(real_database, user_id, settings)
    boxes = [{"x": 1, "y": 1, "w": 2, "h": 2}, {"x": 3, "y": 3, "w": 1, "h": 1}]
    commit_image = await _add_uncommitted_image_with_boxes(
        real_database, batch_id, user_id, boxes=boxes,
    )

    barrier = asyncio.Event()

    async def do_commit() -> list[dict[str, Any]]:
        await barrier.wait()
        return await commit_image_boxes(
            real_database, batch_id, user_id, commit_image, now=NOW + timedelta(seconds=1)
        )

    task_a = asyncio.create_task(do_commit())
    task_b = asyncio.create_task(do_commit())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    barrier.set()
    results = await asyncio.gather(task_a, task_b)

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None
    # Two pre-existing items plus exactly one appended pair.
    assert len(final["items"]) == 4
    item_ids = [i["itemId"] for i in final["items"]]
    assert len(set(item_ids)) == 4
    orders = [i["order"] for i in final["items"]]
    assert len(orders) == len(set(orders))
    # Each caller either created the items or got the idempotent view.
    created = [r for r in results if r]
    assert len(created) == 2


@pytest.mark.asyncio
async def test_delete_image_racing_with_other_item_edit_preserves_both(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Deleting image A must not erase a concurrent edit to item B."""
    batch_id, _item_a_unrelated, item_b_seed = await _batch_with_two_items(real_database, user_id, settings)

    # Image A (committed, one item) gets deleted concurrently.
    image_a = await _add_uncommitted_image_with_boxes(
        real_database, batch_id, user_id, boxes=[{"x": 0, "y": 0, "w": 1, "h": 1}],
    )
    items_a = await commit_image_boxes(real_database, batch_id, user_id, image_a, now=NOW)
    item_a_id = items_a[0]["itemId"]

    barrier = asyncio.Event()

    async def do_delete() -> None:
        await barrier.wait()
        await delete_batch_image(real_database, batch_id, user_id, image_a, now=NOW + timedelta(seconds=1))

    async def do_edit() -> None:
        await barrier.wait()
        await update_item_draft(
            real_database, batch_id, user_id, item_b_seed,
            draft_update={"text": "edited during delete"}, now=NOW + timedelta(seconds=2),
        )

    task_a = asyncio.create_task(do_delete())
    task_b = asyncio.create_task(do_edit())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    barrier.set()
    await asyncio.gather(task_a, task_b)

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None
    item_a = next(i for i in final["items"] if i["itemId"] == item_a_id)
    item_b = next(i for i in final["items"] if i["itemId"] == item_b_seed)
    assert item_a["status"] == ItemState.DELETED.value
    assert item_b["draft"]["text"] == "edited during delete"


# ---------------------------------------------------------------------------
# Sequential contract tests against real Mongo for the six mutation functions.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_item_extraction_success_sets_ready_clears_lease_and_timestamps(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)
    lease_until = NOW + timedelta(minutes=5)
    await _set_item_fields(
        real_database,
        batch_id,
        user_id,
        item_a_id,
        status=ItemState.EXTRACTING.value,
        leaseUntil=lease_until,
    )

    crop = {"bucket": "b", "objectKey": "k"}
    draft = {"text": "t"}
    extraction = {"success": True}

    result = await save_item_extraction_success(
        real_database, batch_id, user_id, item_a_id,
        crop=crop,
        draft=draft,
        extraction=extraction,
        lease_until=lease_until,
        now=NOW,
    )

    assert result is True
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

    # Missing item: no write at all, result False.
    later = NOW + timedelta(seconds=1)
    items_before = loaded["items"]
    assert await save_item_extraction_success(
        real_database, batch_id, user_id, "missing",
        crop={}, draft={}, extraction={}, lease_until=None, now=later,
    ) is False
    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    assert loaded["items"] == items_before
    assert loaded["updatedAt"].replace(tzinfo=UTC) == NOW


@pytest.mark.asyncio
async def test_save_item_extraction_failure_sets_failed_clears_lease_and_timestamps(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_a_id, _ = await _batch_with_two_items(real_database, user_id, settings)
    lease_until = NOW + timedelta(minutes=5)
    await _set_item_fields(
        real_database,
        batch_id,
        user_id,
        item_a_id,
        status=ItemState.EXTRACTING.value,
        leaseUntil=lease_until,
    )

    extraction = {"success": False, "failureCode": "X", "failureMessage": "boom"}
    result = await save_item_extraction_failure(
        real_database, batch_id, user_id, item_a_id,
        extraction=extraction, lease_until=lease_until, now=NOW,
    )

    assert result is True
    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.FAILED.value
    assert item["extraction"] == extraction
    assert item["leaseUntil"] is None
    assert item["updatedAt"].replace(tzinfo=UTC) == NOW
    assert loaded["updatedAt"].replace(tzinfo=UTC) == NOW

    # Missing item: no write at all, result False.
    later = NOW + timedelta(seconds=1)
    items_before = loaded["items"]
    assert await save_item_extraction_failure(
        real_database, batch_id, user_id, "missing",
        extraction={}, lease_until=None, now=later,
    ) is False
    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    assert loaded["items"] == items_before
    assert loaded["updatedAt"].replace(tzinfo=UTC) == NOW


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
    # Lease eligibility is evaluated by the database write, so an expired
    # lease requeues without any client-side datetime comparison.
    changed_at = NOW + timedelta(seconds=3)
    assert await reset_item_for_retry(
        real_database, batch_id, user_id, item_a_id, now=changed_at,
    ) is True
    loaded = await get_batch(real_database, batch_id, user_id)
    assert loaded is not None
    item = next(i for i in loaded["items"] if i["itemId"] == item_a_id)
    assert item["status"] == ItemState.QUEUED.value
    assert item["leaseUntil"] is None
    assert loaded["updatedAt"].replace(tzinfo=UTC) == changed_at

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
    assert updated["updatedAt"].replace(tzinfo=UTC) == NOW

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
        (save_item_extraction_success, {"crop": {}, "draft": {}, "extraction": {}, "lease_until": None}),
        (save_item_extraction_failure, {"extraction": {}, "lease_until": None}),
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


# ---------------------------------------------------------------------------
# Fixture control-flow tests: prove the sentinel name gates setup and cleanup
# without touching a real Mongo server.
# ---------------------------------------------------------------------------


class _FakeSetupDatabase:
    """Minimal async database double for ensure_database_setup."""

    async def list_collection_names(self) -> list[str]:
        return []


class _FakeClient:
    """Records the database name passed to get_database/drop_database."""

    def __init__(self) -> None:
        self.setup_database_name: str | None = None
        self.dropped_database_name: str | None = None
        self.closed = False
        self._database = _FakeSetupDatabase()

    def get_database(self, name: str) -> _FakeSetupDatabase:
        self.setup_database_name = name
        return self._database

    async def drop_database(self, name: str) -> None:
        self.dropped_database_name = name

    async def close(self) -> None:
        self.closed = True


async def _drain_real_database_core(
    *,
    monkeypatch: pytest.MonkeyPatch,
    real_mongo_database: str | None,
    mongodb_uri: str | None,
) -> _FakeClient:
    """Drive ``_real_database_core`` with a fake client and no-op setup.

    No real Mongo I/O occurs. Returns the fake client so the test can inspect
    setup/cleanup calls.
    """
    client = _FakeClient()

    monkeypatch.delenv("MONGODB_URI", raising=False)
    if mongodb_uri is not None:
        monkeypatch.setenv("MONGODB_URI", mongodb_uri)
    monkeypatch.delenv(REAL_MONGO_DATABASE_ENV, raising=False)
    if real_mongo_database is not None:
        monkeypatch.setenv(REAL_MONGO_DATABASE_ENV, real_mongo_database)

    async def _no_op_setup(_db: Any) -> None:
        return None

    gen = _real_database_core(
        client_factory=lambda _uri: client,
        ensure_setup=_no_op_setup,
    )
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        return client
    except pytest.skip.Exception:
        # The fixture called pytest.skip() before yielding (missing MONGODB_URI
        # or sentinel name). Drain the generator so cleanup runs, then return
        # the client so the caller can assert no setup/cleanup occurred. We
        # catch the skip outcome rather than letting it propagate so these
        # control-flow tests pass (verifying the skip path) instead of being
        # skipped themselves, which would fail --require-real-mongo enforcement.
        with contextlib.suppress(StopAsyncIteration, pytest.skip.Exception):
            await gen.__anext__()
        return client
    with contextlib.suppress(StopAsyncIteration):
        await gen.__anext__()
    return client


@pytest.mark.asyncio
async def test_fixture_setup_and_teardown_pass_valid_name_to_drop_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = await _drain_real_database_core(
        monkeypatch=monkeypatch,
        real_mongo_database="learnloop_test_run-42",
        mongodb_uri="mongodb://example/test",
    )
    assert client.setup_database_name == "learnloop_test_run-42"
    assert client.dropped_database_name == "learnloop_test_run-42"
    assert client.closed is True


@pytest.mark.asyncio
async def test_fixture_rejects_invalid_name_before_setup_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match=REAL_MONGO_DATABASE_ENV):
        await _drain_real_database_core(
            monkeypatch=monkeypatch,
            real_mongo_database="learnloop",
            mongodb_uri="mongodb://example/test",
        )


@pytest.mark.asyncio
async def test_fixture_skips_without_mongodb_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = await _drain_real_database_core(
        monkeypatch=monkeypatch,
        real_mongo_database="learnloop_test_a",
        mongodb_uri=None,
    )
    assert client.setup_database_name is None
    assert client.dropped_database_name is None


@pytest.mark.asyncio
async def test_fixture_skips_when_sentinel_name_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # MONGODB_URI is set but LEARNLOOP_REAL_MONGO_DATABASE is absent: not opted
    # in, so the fixture skips instead of erroring.
    client = await _drain_real_database_core(
        monkeypatch=monkeypatch,
        real_mongo_database=None,
        mongodb_uri="mongodb://example/test",
    )
    assert client.setup_database_name is None
    assert client.dropped_database_name is None
