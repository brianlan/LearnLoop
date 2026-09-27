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

from app.domain.ingestion import ImageState, ItemState
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion import (
    add_items_for_image,
    add_source_image,
    build_source_image,
)
from app.infrastructure.ingestion import repository as ingestion_repository
from app.infrastructure.ingestion.repository import (
    INGESTION_BATCHES_COLLECTION,
    commit_image_boxes,
    create_batch,
    delete_batch_image,
    get_batch,
    mark_item_deleted,
    reset_item_for_retry,
    save_image_boxes_and_subject,
    save_image_detection_failure,
    save_image_detection_success,
    save_item_extraction_failure,
    save_item_extraction_success,
    start_image_detection,
    submit_items_and_complete_batch,
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


class _ReadWriteGate:
    """Holds each participant after its batch read until all have read."""

    def __init__(self, participants: int) -> None:
        self._pending = participants
        self._released = asyncio.Event()

    async def hold_after_read(
        self,
        original_load: Callable[..., Awaitable[Any]],
        database: Any,
        batch_id: ObjectId,
        user_id: ObjectId,
    ) -> Any:
        doc = await original_load(database, batch_id, user_id)
        self._pending -= 1
        if self._pending == 0:
            self._released.set()
        await self._released.wait()
        return doc


def _source_image(*, order: int) -> dict[str, Any]:
    return build_source_image(
        bucket="media",
        object_key=f"users/u/img-{order}.png",
        content_type="image/png",
        size_bytes=42,
        sha256=f"sha-{order}",
        uploaded_at=NOW,
    )


def _gate_first_reads(monkeypatch: pytest.MonkeyPatch, participants: int) -> None:
    """Force a deterministic stale-snapshot interleaving.

    The first ``participants`` batch reads all complete before any of them
    returns, so every participant reads the same document version before any
    participant writes. Later reads (OCC retries) pass through ungated.
    """
    original_load = ingestion_repository._load_batch_for_update
    gate = _ReadWriteGate(participants)
    ungated = {"remaining": participants}

    async def gated_load(database: Any, batch_id: Any, user_id: Any) -> Any:
        if ungated["remaining"] > 0:
            ungated["remaining"] -= 1
            return await gate.hold_after_read(original_load, database, batch_id, user_id)
        return await original_load(database, batch_id, user_id)

    monkeypatch.setattr(ingestion_repository, "_load_batch_for_update", gated_load)


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
    real_database: Any,
    user_id: ObjectId,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two synchronized draft edits to distinct items must both survive."""
    batch_id, item_a_id, item_b_id = await _batch_with_two_items(real_database, user_id, settings)
    _gate_first_reads(monkeypatch, participants=2)

    async def update_a() -> None:
        await update_item_draft(
            real_database, batch_id, user_id, item_a_id,
            draft_update={"text": "updated by A"}, now=NOW + timedelta(seconds=1),
        )

    async def update_b() -> None:
        await update_item_draft(
            real_database, batch_id, user_id, item_b_id,
            draft_update={"text": "updated by B"}, now=NOW + timedelta(seconds=2),
        )

    await asyncio.gather(update_a(), update_b())

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None

    item_a = next(i for i in final["items"] if i["itemId"] == item_a_id)
    item_b = next(i for i in final["items"] if i["itemId"] == item_b_id)
    assert item_a["draft"]["text"] == "updated by A"
    assert item_b["draft"]["text"] == "updated by B"


@pytest.mark.asyncio
async def test_extraction_completion_and_distinct_draft_edit_both_survive(
    real_database: Any,
    user_id: ObjectId,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker completing item A must not erase a concurrent draft edit to item B."""
    batch_id, item_a_id, item_b_id = await _batch_with_two_items(real_database, user_id, settings)
    lease = NOW + timedelta(minutes=5)
    await _set_item_fields(
        real_database, batch_id, user_id, item_a_id,
        status=ItemState.EXTRACTING.value, leaseUntil=lease,
    )
    _gate_first_reads(monkeypatch, participants=2)

    async def complete_a() -> None:
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
        await update_item_draft(
            real_database, batch_id, user_id, item_b_id,
            draft_update={"text": "edited B"}, now=NOW + timedelta(seconds=2),
        )

    await asyncio.gather(complete_a(), edit_b())

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
    real_database: Any,
    user_id: ObjectId,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit appending items must not erase a concurrent edit to an existing item."""
    batch_id, existing_item_id, _ = await _batch_with_two_items(real_database, user_id, settings)
    # A second, uncommitted image with ready boxes for the commit to append.
    commit_image = await _add_uncommitted_image_with_boxes(
        real_database, batch_id, user_id,
        boxes=[{"x": 1, "y": 1, "w": 2, "h": 2}],
    )
    _gate_first_reads(monkeypatch, participants=2)

    async def do_commit() -> None:
        await commit_image_boxes(real_database, batch_id, user_id, commit_image, now=NOW + timedelta(seconds=1))

    async def do_edit() -> None:
        await update_item_draft(
            real_database, batch_id, user_id, existing_item_id,
            draft_update={"text": "edited during commit"}, now=NOW + timedelta(seconds=2),
        )

    await asyncio.gather(do_commit(), do_edit())

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
    real_database: Any,
    user_id: ObjectId,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two overlapping commits of the same image must land exactly once."""
    batch_id, _existing_item_id, _ = await _batch_with_two_items(real_database, user_id, settings)
    boxes = [{"x": 1, "y": 1, "w": 2, "h": 2}, {"x": 3, "y": 3, "w": 1, "h": 1}]
    commit_image = await _add_uncommitted_image_with_boxes(
        real_database, batch_id, user_id, boxes=boxes,
    )
    _gate_first_reads(monkeypatch, participants=2)

    async def do_commit() -> list[dict[str, Any]]:
        return await commit_image_boxes(
            real_database, batch_id, user_id, commit_image, now=NOW + timedelta(seconds=1)
        )

    results = await asyncio.gather(do_commit(), do_commit())

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
    real_database: Any,
    user_id: ObjectId,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting image A must not erase a concurrent edit to item B."""
    batch_id, _item_a_unrelated, item_b_seed = await _batch_with_two_items(real_database, user_id, settings)

    # Image A (committed, one item) gets deleted concurrently.
    image_a = await _add_uncommitted_image_with_boxes(
        real_database, batch_id, user_id, boxes=[{"x": 0, "y": 0, "w": 1, "h": 1}],
    )
    items_a = await commit_image_boxes(real_database, batch_id, user_id, image_a, now=NOW)
    item_a_id = items_a[0]["itemId"]
    _gate_first_reads(monkeypatch, participants=2)

    async def do_delete() -> None:
        await delete_batch_image(real_database, batch_id, user_id, image_a, now=NOW + timedelta(seconds=1))

    async def do_edit() -> None:
        await update_item_draft(
            real_database, batch_id, user_id, item_b_seed,
            draft_update={"text": "edited during delete"}, now=NOW + timedelta(seconds=2),
        )

    await asyncio.gather(do_delete(), do_edit())

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None
    item_a = next(i for i in final["items"] if i["itemId"] == item_a_id)
    item_b = next(i for i in final["items"] if i["itemId"] == item_b_seed)
    assert item_a["status"] == ItemState.DELETED.value
    assert item_b["draft"]["text"] == "edited during delete"


# ---------------------------------------------------------------------------
# Collision-safety and deleted-image regressions.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_submit_write_rejected_when_edit_lands_same_millisecond(
    real_database: Any,
    user_id: ObjectId,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the OCC token must survive same-millisecond writes.

    The submit writer's caller-supplied ``now`` equals the document's current
    ``updatedAt``, and a targeted edit lands between the writer's read and
    write with the same timestamp. A timestamp-equality guard would still
    match and overwrite the edit; the revision guard rejects the stale write,
    and the retry preserves both outcomes.
    """
    batch_id, item_a_id, item_b_id = await _batch_with_two_items(real_database, user_id, settings)
    same_ts = NOW + timedelta(seconds=1)
    # Warm the document so its updatedAt equals the writer's timestamp —
    # the same-millisecond collision precondition.
    await update_item_draft(
        real_database, batch_id, user_id, item_a_id, draft_update={"text": "warm-up"}, now=same_ts
    )

    original_load = ingestion_repository._load_batch_for_update
    landed = {"edit": False}

    async def load_then_edit(database: Any, batch_id: Any, user_id: Any) -> Any:
        doc = await original_load(database, batch_id, user_id)
        if not landed["edit"]:
            landed["edit"] = True
            assert (
                await update_item_draft(
                    database, batch_id, user_id, item_b_id,
                    draft_update={"text": "interleaved edit"}, now=same_ts,
                )
                is not None
            )
        return doc

    monkeypatch.setattr(ingestion_repository, "_load_batch_for_update", load_then_edit)

    submit = {"submittedProblemId": "p1", "success": True, "failureCode": None, "failureMessage": None}
    await submit_items_and_complete_batch(
        real_database,
        batch_id,
        user_id,
        item_results=[{"itemId": item_a_id, "status": ItemState.SUBMITTED.value, "submit": submit}],
        now=same_ts,
    )

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None
    item_a = next(i for i in final["items"] if i["itemId"] == item_a_id)
    item_b = next(i for i in final["items"] if i["itemId"] == item_b_id)
    assert item_a["status"] == ItemState.SUBMITTED.value
    # The interleaved same-timestamp edit survived the stale write.
    assert item_b["draft"]["text"] == "interleaved edit"


@pytest.mark.asyncio
async def test_commit_racing_with_image_deletion_cannot_resurrect(
    real_database: Any,
    user_id: ObjectId,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a commit that raced with delete must be rejected, not resurrect."""
    batch_id, _existing_item_id, _ = await _batch_with_two_items(real_database, user_id, settings)
    commit_image = await _add_uncommitted_image_with_boxes(
        real_database, batch_id, user_id,
        boxes=[{"x": 1, "y": 1, "w": 2, "h": 2}],
    )

    original_load = ingestion_repository._load_batch_for_update
    landed = {"delete": False}

    async def load_then_delete(database: Any, batch_id: Any, user_id: Any) -> Any:
        doc = await original_load(database, batch_id, user_id)
        if not landed["delete"]:
            landed["delete"] = True
            await delete_batch_image(
                database, batch_id, user_id, commit_image, now=NOW + timedelta(seconds=1)
            )
        return doc

    monkeypatch.setattr(ingestion_repository, "_load_batch_for_update", load_then_delete)

    with pytest.raises(ValueError, match="Image not found"):
        await commit_image_boxes(real_database, batch_id, user_id, commit_image, now=NOW)

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None
    image = next(i for i in final["images"] if i["imageId"] == commit_image)
    assert image["status"] == ImageState.DELETED.value
    # No items were appended for the deleted image.
    assert not [i for i in final["items"] if i["imageId"] == commit_image]


@pytest.mark.asyncio
async def test_add_items_for_image_rejects_deleted_image(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Regression: items must not be appended to (and commit must not resurrect)
    an image that delete_batch_image already removed."""
    batch_id, _existing_item_id, _ = await _batch_with_two_items(real_database, user_id, settings)
    image = await _add_uncommitted_image_with_boxes(
        real_database, batch_id, user_id,
        boxes=[{"x": 1, "y": 1, "w": 2, "h": 2}],
    )
    await delete_batch_image(real_database, batch_id, user_id, image, now=NOW + timedelta(seconds=1))

    with pytest.raises(ValueError, match="Image not found"):
        await add_items_for_image(
            real_database,
            batch_id,
            user_id,
            image,
            item_count=2,
            starting_order=5,
            now=NOW + timedelta(seconds=2),
        )

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None
    stored_image = next(i for i in final["images"] if i["imageId"] == image)
    assert stored_image["status"] == ImageState.DELETED.value
    assert not [i for i in final["items"] if i["imageId"] == image]


# ---------------------------------------------------------------------------
# Deletion vs late image-processing and stale submit regressions.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_late_detection_result_cannot_resurrect_deleted_image(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Regression: a detection result landing after delete must be discarded."""
    batch = await create_batch(real_database, user_id, settings, now=NOW)
    image = await add_source_image(
        real_database, batch["_id"], user_id, _source_image(order=0), order=0, now=NOW
    )
    await start_image_detection(real_database, batch["_id"], user_id, image["imageId"], now=NOW)
    await delete_batch_image(real_database, batch["_id"], user_id, image["imageId"], now=NOW)

    await save_image_detection_success(
        real_database, batch["_id"], user_id, image["imageId"],
        subject="math", boxes=[{"x": 1, "y": 1, "w": 2, "h": 2}],
        model="vlm", raw_provider_response={}, now=NOW,
    )

    final = await get_batch(real_database, batch["_id"], user_id)
    assert final is not None
    stored = next(i for i in final["images"] if i["imageId"] == image["imageId"])
    assert stored["status"] == ImageState.DELETED.value
    assert stored["boxes"] == []


@pytest.mark.asyncio
async def test_late_detection_failure_cannot_resurrect_deleted_image(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Regression: a late failure write must not re-enter a deleted image."""
    batch = await create_batch(real_database, user_id, settings, now=NOW)
    image = await add_source_image(
        real_database, batch["_id"], user_id, _source_image(order=0), order=0, now=NOW
    )
    await start_image_detection(real_database, batch["_id"], user_id, image["imageId"], now=NOW)
    await delete_batch_image(real_database, batch["_id"], user_id, image["imageId"], now=NOW)

    await save_image_detection_failure(
        real_database, batch["_id"], user_id, image["imageId"],
        failure_code="X", failure_message="boom", now=NOW,
    )

    final = await get_batch(real_database, batch["_id"], user_id)
    assert final is not None
    stored = next(i for i in final["images"] if i["imageId"] == image["imageId"])
    assert stored["status"] == ImageState.DELETED.value


@pytest.mark.asyncio
async def test_late_box_save_cannot_resurrect_deleted_image(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Regression: a box edit landing after delete must not make the image ready."""
    batch = await create_batch(real_database, user_id, settings, now=NOW)
    image = await add_source_image(
        real_database, batch["_id"], user_id, _source_image(order=0), order=0, now=NOW
    )
    await save_image_boxes_and_subject(
        real_database, batch["_id"], user_id, image["imageId"],
        subject="math", boxes=[{"x": 1, "y": 1, "w": 2, "h": 2}], now=NOW,
    )
    await delete_batch_image(real_database, batch["_id"], user_id, image["imageId"], now=NOW)

    await save_image_boxes_and_subject(
        real_database, batch["_id"], user_id, image["imageId"],
        subject="math", boxes=[{"x": 3, "y": 3, "w": 1, "h": 1}], now=NOW,
    )

    final = await get_batch(real_database, batch["_id"], user_id)
    assert final is not None
    stored = next(i for i in final["images"] if i["imageId"] == image["imageId"])
    assert stored["status"] == ImageState.DELETED.value
    assert stored["boxes"] == [{"x": 1, "y": 1, "w": 2, "h": 2}]


@pytest.mark.asyncio
async def test_deleted_image_cannot_restart_detection(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    """Regression: a late detection start must not re-enter a deleted image."""
    batch = await create_batch(real_database, user_id, settings, now=NOW)
    image = await add_source_image(
        real_database, batch["_id"], user_id, _source_image(order=0), order=0, now=NOW
    )
    await delete_batch_image(real_database, batch["_id"], user_id, image["imageId"], now=NOW)

    await start_image_detection(real_database, batch["_id"], user_id, image["imageId"], now=NOW)

    final = await get_batch(real_database, batch["_id"], user_id)
    assert final is not None
    stored = next(i for i in final["images"] if i["imageId"] == image["imageId"])
    assert stored["status"] == ImageState.DELETED.value


@pytest.mark.asyncio
async def test_stale_submit_result_cannot_resurrect_deleted_item(
    real_database: Any,
    user_id: ObjectId,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a submit outcome racing a deletion must not resurrect the item
    during the OCC retry, and the batch must not complete from the stale result."""
    batch_id, item_a_id, item_b_id = await _batch_with_two_items(real_database, user_id, settings)
    await _set_item_fields(
        real_database, batch_id, user_id, item_a_id, status=ItemState.READY.value,
    )
    same_ts = NOW + timedelta(seconds=1)
    # Make the document's updatedAt equal the submit timestamp (the
    # same-millisecond precondition) so only the revision guard rejects the
    # first write and the retry path is exercised deterministically.
    await update_item_draft(
        real_database, batch_id, user_id, item_b_id, draft_update={"text": "warm-up"}, now=same_ts
    )

    original_load = ingestion_repository._load_batch_for_update
    landed = {"delete": False}

    async def load_then_delete(database: Any, batch_id: Any, user_id: Any) -> Any:
        doc = await original_load(database, batch_id, user_id)
        if not landed["delete"]:
            landed["delete"] = True
            assert await mark_item_deleted(
                database, batch_id, user_id, item_a_id, now=same_ts
            ) is True
        return doc

    monkeypatch.setattr(ingestion_repository, "_load_batch_for_update", load_then_delete)

    submit = {"submittedProblemId": "p1", "success": True, "failureCode": None, "failureMessage": None}
    await submit_items_and_complete_batch(
        real_database,
        batch_id,
        user_id,
        item_results=[{"itemId": item_a_id, "status": ItemState.SUBMITTED.value, "submit": submit}],
        now=same_ts,
    )

    final = await get_batch(real_database, batch_id, user_id)
    assert final is not None
    item_a = next(i for i in final["items"] if i["itemId"] == item_a_id)
    item_b = next(i for i in final["items"] if i["itemId"] == item_b_id)
    # The deleted item stays deleted and the stale submit payload never lands.
    assert item_a["status"] == ItemState.DELETED.value
    assert item_a["submit"]["submittedProblemId"] is None
    # The batch is not completed from the stale result: B was never submitted.
    assert item_b["status"] == ItemState.QUEUED.value
    assert final["status"] == "active"


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


# ---------------------------------------------------------------------------
# Variant lifecycle concurrency (issue #613)
# ---------------------------------------------------------------------------

from app.problem_variation import IngestionMode  # noqa: E402
from app.infrastructure.ingestion.repository import (  # noqa: E402
    claim_variation_work,
    request_variation_generation,
    save_variation_candidate_checkpoint,
    save_variation_result,
    update_item_draft_variant,
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


async def _variant_batch_with_items(
    database: Any, user_id: ObjectId, settings: Settings, *, item_count: int = 2
) -> tuple[ObjectId, list[str]]:
    """Seed an active data-only batch with ready items awaiting variant work."""
    source_image = build_source_image(
        bucket="media",
        object_key="users/u/variant.png",
        content_type="image/png",
        size_bytes=42,
        sha256="sha-variant",
        uploaded_at=NOW,
    )
    batch = await create_batch(
        database, user_id, settings,
        ingestion_mode=IngestionMode.DATA_ONLY, now=NOW,
    )
    image = await add_source_image(database, batch["_id"], user_id, source_image, order=0, now=NOW)
    items = await add_items_for_image(
        database, batch["_id"], user_id, image["imageId"],
        item_count=item_count, starting_order=0, now=NOW,
    )
    # Extraction done: items are ready for variant work.
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch["_id"], "userId": user_id},
        {
            "$set": {
                **{
                    f"items.{index}.status": ItemState.READY.value
                    for index in range(item_count)
                },
            }
        },
    )
    return batch["_id"], [item["itemId"] for item in items]


@pytest.mark.real_mongo
async def test_same_item_source_edit_cancels_queued_generation_before_claim(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_ids = await _variant_batch_with_items(real_database, user_id, settings)
    item_id = item_ids[0]

    await request_variation_generation(
        real_database, batch_id, user_id, item_id,
        original=VARIANT_ORIGINAL, expected_revision=0, now=NOW,
    )

    # The user semantically edits the confirmed source while generation is queued.
    await update_item_draft_variant(
        real_database, batch_id, user_id, item_id,
        draft_update={"text": "Edited problem statement?"},
        expected_revision=1, now=NOW,
    )
    batch = await get_batch(real_database, batch_id, user_id)
    item = next(i for i in batch["items"] if i["itemId"] == item_id)
    assert item["variation"]["status"] == "not-requested"
    assert item["variation"]["original"] is None
    assert item["contentRevision"] == 2

    # The queued work can no longer be claimed.
    assert await claim_variation_work(
        real_database, batch_id, user_id, item_id,
        lease_timeout_seconds=300, now=NOW,
    ) is None


@pytest.mark.real_mongo
async def test_same_item_source_edit_invalidates_in_flight_claim_and_result(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_ids = await _variant_batch_with_items(real_database, user_id, settings)
    item_id = item_ids[0]

    await request_variation_generation(
        real_database, batch_id, user_id, item_id,
        original=VARIANT_ORIGINAL, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        real_database, batch_id, user_id, item_id,
        lease_timeout_seconds=300, now=NOW,
    )
    assert claimed is not None
    token = claimed["variation"]["claimToken"]

    # The user edits the source while the worker is generating.
    await update_item_draft_variant(
        real_database, batch_id, user_id, item_id,
        draft_update={"text": "Edited problem statement?"},
        expected_revision=1, now=NOW,
    )

    # The stale worker result cannot land.
    assert await save_variation_result(
        real_database, batch_id, user_id, item_id,
        token=token, claimed_revision=1, verdict="pass",
        validation={"verdict": "pass", "failures": [], "reports": []},
        now=NOW,
    ) is False
    batch = await get_batch(real_database, batch_id, user_id)
    item = next(i for i in batch["items"] if i["itemId"] == item_id)
    assert item["variation"]["status"] == "not-requested"


@pytest.mark.real_mongo
async def test_distinct_item_background_work_and_edit_both_survive(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_ids = await _variant_batch_with_items(real_database, user_id, settings)
    item_a, item_b = item_ids

    await request_variation_generation(
        real_database, batch_id, user_id, item_a,
        original=VARIANT_ORIGINAL, expected_revision=0, now=NOW,
    )
    claimed_a = await claim_variation_work(
        real_database, batch_id, user_id, item_a,
        lease_timeout_seconds=300, now=NOW,
    )
    assert claimed_a is not None

    start_b = asyncio.Event()
    claim_checkpoint_done = asyncio.Event()

    async def _checkpoint_a() -> None:
        await save_variation_candidate_checkpoint(
            real_database, batch_id, user_id, item_a,
            token=claimed_a["variation"]["claimToken"], claimed_revision=1,
            candidate=VARIANT_CANDIDATE, now=NOW,
        )
        claim_checkpoint_done.set()
        await start_b.wait()
        await save_variation_result(
            real_database, batch_id, user_id, item_a,
            token=claimed_a["variation"]["claimToken"], claimed_revision=1,
            verdict="pass",
            validation={"verdict": "pass", "failures": [], "reports": []},
            now=NOW,
        )

    async def _edit_b() -> None:
        await claim_checkpoint_done.wait()
        await update_item_draft_variant(
            real_database, batch_id, user_id, item_b,
            draft_update={"text": "Edited other item?"},
            expected_revision=0, now=NOW,
        )
        start_b.set()

    await asyncio.gather(_checkpoint_a(), _edit_b())

    batch = await get_batch(real_database, batch_id, user_id)
    items = {i["itemId"]: i for i in batch["items"]}
    assert items[item_a]["variation"]["status"] == "ready"
    assert items[item_a]["variation"]["validatedRevision"] == 1
    assert items[item_a]["variation"]["candidate"]["text"] == VARIANT_CANDIDATE["text"]
    assert items[item_b]["draft"]["text"] == "Edited other item?"
    assert items[item_b]["variation"]["status"] == "not-requested"
    assert items[item_b]["contentRevision"] == 1


@pytest.mark.real_mongo
async def test_expired_batch_rejects_variation_claims_and_results(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_ids = await _variant_batch_with_items(real_database, user_id, settings)
    item_id = item_ids[0]
    await request_variation_generation(
        real_database, batch_id, user_id, item_id,
        original=VARIANT_ORIGINAL, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        real_database, batch_id, user_id, item_id,
        lease_timeout_seconds=300, now=NOW,
    )
    assert claimed is not None
    token = claimed["variation"]["claimToken"]

    # Expire the batch underneath the in-flight attempt.
    await real_database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id},
        {"$set": {"expiresAt": NOW - timedelta(seconds=1)}},
    )
    assert await save_variation_result(
        real_database, batch_id, user_id, item_id,
        token=token, claimed_revision=1, verdict="pass",
        validation={"verdict": "pass", "failures": [], "reports": []},
        now=NOW,
    ) is False
    # And no new work can be claimed on the expired batch.
    assert await claim_variation_work(
        real_database, batch_id, user_id, item_ids[1],
        lease_timeout_seconds=300, now=NOW,
    ) is None


@pytest.mark.real_mongo
async def test_old_candidate_checkpoint_cannot_land_after_regeneration(
    real_database: Any, user_id: ObjectId, settings: Settings
) -> None:
    batch_id, item_ids = await _variant_batch_with_items(real_database, user_id, settings)
    item_id = item_ids[0]

    await request_variation_generation(
        real_database, batch_id, user_id, item_id,
        original=VARIANT_ORIGINAL, expected_revision=0, now=NOW,
    )
    first_claim = await claim_variation_work(
        real_database, batch_id, user_id, item_id,
        lease_timeout_seconds=300, now=NOW,
    )
    first_token = first_claim["variation"]["claimToken"]

    # First attempt fails; the user manually regenerates.
    assert await save_variation_result(
        real_database, batch_id, user_id, item_id,
        token=first_token, claimed_revision=1, verdict="fail",
        validation={"verdict": "fail", "failures": [], "reports": []},
        now=NOW,
    )
    await request_variation_generation(
        real_database, batch_id, user_id, item_id,
        original=VARIANT_ORIGINAL, expected_revision=1, now=NOW,
    )

    # The stale claim's checkpoint can never overwrite the new attempt.
    assert await save_variation_candidate_checkpoint(
        real_database, batch_id, user_id, item_id,
        token=first_token, claimed_revision=1,
        candidate={"text": "stale candidate"},
        now=NOW,
    ) is False
    batch = await get_batch(real_database, batch_id, user_id)
    item = next(i for i in batch["items"] if i["itemId"] == item_id)
    assert item["variation"]["candidate"] is None
    assert item["variation"]["status"] == "queued"
    assert item["variation"]["generationCount"] == 2
