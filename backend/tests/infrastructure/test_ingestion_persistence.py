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
from app.infrastructure.ingestion.repository import (
    INGESTION_BATCHES_COLLECTION,
    commit_image_boxes,
    delete_batch_image,
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


# ---------------------------------------------------------------------------
# Characterization tests for ingestion repository mutation behavior.
#
# These tests lock the exact write payloads (update_one $set field sets and
# resulting stored document state) for the bulk-ingestion repository mutation
# functions so that a future helper-extraction refactor can be verified to
# preserve behavior. claim_item (find_one_and_update / atomic path) is
# intentionally out of scope.
# ---------------------------------------------------------------------------

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _source_image() -> dict[str, Any]:
    return build_source_image(
        bucket="media",
        object_key="users/u/img.png",
        content_type="image/png",
        size_bytes=42,
        sha256="sha",
        uploaded_at=NOW,
    )


async def _batch_with_image(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> tuple[dict[str, Any], dict[str, Any]]:
    batch = await create_batch(database, user_id, settings, now=NOW)
    image = await add_source_image(database, batch["_id"], user_id, _source_image(), order=0, now=NOW)
    return batch, image


async def _batch_with_items(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    batch, image = await _batch_with_image(database, user_id, settings)
    items = await add_items_for_image(
        database, batch["_id"], user_id, image["imageId"], item_count=1, starting_order=0, now=NOW
    )
    return batch, image, items


@pytest.mark.asyncio
async def test_start_image_detection_sets_image_to_detecting(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image = await _batch_with_image(database, user_id, settings)

    await start_image_detection(database, batch["_id"], user_id, image["imageId"], now=NOW)

    loaded = await get_batch(database, batch["_id"], user_id)
    assert loaded is not None
    assert loaded["updatedAt"] == NOW
    stored = loaded["images"][0]
    assert stored["status"] == ImageState.DETECTING.value
    assert stored["updatedAt"] == NOW
    # Untouched fields are preserved.
    assert stored["detection"]["failureCode"] is None
    assert stored["boxes"] == []


@pytest.mark.asyncio
async def test_save_image_detection_success_sets_ready_with_detection_payload(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image = await _batch_with_image(database, user_id, settings)
    boxes = [{"x": 1, "y": 2, "w": 3, "h": 4}]
    raw = {"provider": "resp"}

    await save_image_detection_success(
        database, batch["_id"], user_id, image["imageId"],
        subject="math", boxes=boxes, model="vlm-1", raw_provider_response=raw, now=NOW,
    )

    loaded = await get_batch(database, batch["_id"], user_id)
    stored = loaded["images"][0]
    assert stored["status"] == ImageState.READY.value
    assert stored["subject"] == "math"
    assert stored["boxes"] == boxes
    assert stored["detection"] == {
        "model": "vlm-1",
        "rawProviderResponse": raw,
        "failureCode": None,
        "failureMessage": None,
    }
    assert stored["updatedAt"] == NOW
    assert loaded["updatedAt"] == NOW


@pytest.mark.asyncio
async def test_save_image_detection_failure_sets_detect_failed_with_detection_payload(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image = await _batch_with_image(database, user_id, settings)

    await save_image_detection_failure(
        database, batch["_id"], user_id, image["imageId"],
        failure_code="VLM_FAILED", failure_message="boom", now=NOW,
    )

    loaded = await get_batch(database, batch["_id"], user_id)
    stored = loaded["images"][0]
    assert stored["status"] == ImageState.DETECT_FAILED.value
    assert stored["detection"] == {
        "model": None,
        "rawProviderResponse": None,
        "failureCode": "VLM_FAILED",
        "failureMessage": "boom",
    }
    assert stored["updatedAt"] == NOW
    assert loaded["updatedAt"] == NOW


@pytest.mark.asyncio
async def test_save_image_boxes_and_subject_sets_ready_boxes_and_conditional_subject(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image = await _batch_with_image(database, user_id, settings)
    boxes = [{"x": 0, "y": 0, "w": 1, "h": 1}]

    # With a subject: subject is set.
    await save_image_boxes_and_subject(
        database, batch["_id"], user_id, image["imageId"], subject="english", boxes=boxes, now=NOW
    )
    loaded = await get_batch(database, batch["_id"], user_id)
    stored = loaded["images"][0]
    assert stored["status"] == ImageState.READY.value
    assert stored["subject"] == "english"
    assert stored["boxes"] == boxes
    assert stored["updatedAt"] == NOW

    # With subject=None: subject is preserved (not overwritten).
    later = NOW + timedelta(seconds=10)
    await save_image_boxes_and_subject(
        database, batch["_id"], user_id, image["imageId"], subject=None, boxes=[], now=later
    )
    loaded = await get_batch(database, batch["_id"], user_id)
    stored = loaded["images"][0]
    assert stored["subject"] == "english"
    assert stored["boxes"] == []
    assert stored["updatedAt"] == later


@pytest.mark.asyncio
async def test_delete_batch_image_marks_image_and_items_deleted(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image, items = await _batch_with_items(database, user_id, settings)
    item_id = items[0]["itemId"]

    await delete_batch_image(database, batch["_id"], user_id, image["imageId"], now=NOW)

    loaded = await get_batch(database, batch["_id"], user_id)
    assert loaded["images"][0]["status"] == ImageState.DELETED.value
    assert loaded["images"][0]["updatedAt"] == NOW
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["status"] == ItemState.DELETED.value
    assert item["updatedAt"] == NOW
    assert loaded["updatedAt"] == NOW


@pytest.mark.asyncio
async def test_commit_image_boxes_creates_items_and_is_idempotent(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image = await _batch_with_image(database, user_id, settings)
    boxes = [{"x": 1, "y": 1, "w": 2, "h": 2}, {"x": 3, "y": 3, "w": 1, "h": 1}]
    # Put boxes onto the image without committing (ready state with boxes).
    await save_image_boxes_and_subject(
        database, batch["_id"], user_id, image["imageId"], subject="math", boxes=boxes, now=NOW
    )

    created = await commit_image_boxes(database, batch["_id"], user_id, image["imageId"], now=NOW)
    assert len(created) == 2

    loaded = await get_batch(database, batch["_id"], user_id)
    stored_image = loaded["images"][0]
    assert stored_image["status"] == ImageState.COMMITTED.value
    assert stored_image["committedAt"] == NOW
    assert stored_image["updatedAt"] == NOW
    assert len(loaded["items"]) == 2
    for index, item in enumerate(loaded["items"]):
        assert item["imageId"] == image["imageId"]
        assert item["box"] == boxes[index]
        assert item["order"] == index
        assert item["status"] == ItemState.QUEUED.value
    assert loaded["updatedAt"] == NOW

    # Idempotent: re-committing returns existing non-deleted items without adding new ones.
    again = await commit_image_boxes(database, batch["_id"], user_id, image["imageId"], now=NOW)
    assert {i["itemId"] for i in again} == {i["itemId"] for i in created}
    loaded = await get_batch(database, batch["_id"], user_id)
    assert len(loaded["items"]) == 2


@pytest.mark.asyncio
async def test_save_item_extraction_success_sets_ready_payload_and_clears_lease(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image, items = await _batch_with_items(database, user_id, settings)
    item_id = items[0]["itemId"]
    crop = {"bucket": "media", "objectKey": "users/u/crop.png"}
    draft = {"text": "2+2=?", "problemType": "short-answer"}
    extraction = {"success": True, "model": "vlm-1"}

    await save_item_extraction_success(
        database, batch["_id"], user_id, item_id, crop=crop, draft=draft, extraction=extraction, now=NOW
    )

    loaded = await get_batch(database, batch["_id"], user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["status"] == ItemState.READY.value
    assert item["crop"] == crop
    assert item["draft"] == draft
    assert item["extraction"] == extraction
    assert item["leaseUntil"] is None
    assert item["updatedAt"] == NOW
    assert loaded["updatedAt"] == NOW


@pytest.mark.asyncio
async def test_save_item_extraction_failure_sets_failed_and_clears_lease(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image, items = await _batch_with_items(database, user_id, settings)
    item_id = items[0]["itemId"]
    extraction = {"success": False, "failureCode": "EXTRACTION_FAILED", "failureMessage": "boom"}

    await save_item_extraction_failure(
        database, batch["_id"], user_id, item_id, extraction=extraction, now=NOW
    )

    loaded = await get_batch(database, batch["_id"], user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["status"] == ItemState.FAILED.value
    assert item["extraction"] == extraction
    assert item["leaseUntil"] is None
    assert item["updatedAt"] == NOW
    assert loaded["updatedAt"] == NOW


@pytest.mark.asyncio
async def test_reset_item_for_retry_resets_failed_and_lease_expired_and_skips_ineligible(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image, items = await _batch_with_items(database, user_id, settings)
    item_id = items[0]["itemId"]
    coll = database[INGESTION_BATCHES_COLLECTION]

    # FAILED -> queued.
    await save_item_extraction_failure(
        database, batch["_id"], user_id, item_id,
        extraction={"success": False, "failureCode": "X"}, now=NOW,
    )
    assert await reset_item_for_retry(database, batch["_id"], user_id, item_id, now=NOW) is True
    loaded = await get_batch(database, batch["_id"], user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["status"] == ItemState.QUEUED.value
    assert item["leaseUntil"] is None

    # EXTRACTING with expired lease -> queued.
    past = NOW - timedelta(seconds=1)
    await coll.update_one(
        {"_id": batch["_id"], "items.itemId": item_id},
        {"$set": {"items.$.status": ItemState.EXTRACTING.value, "items.$.leaseUntil": past}},
    )
    assert await reset_item_for_retry(database, batch["_id"], user_id, item_id, now=NOW) is True
    loaded = await get_batch(database, batch["_id"], user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["status"] == ItemState.QUEUED.value
    assert item["leaseUntil"] is None

    # READY (ineligible) -> False, unchanged.
    await save_item_extraction_success(
        database, batch["_id"], user_id, item_id,
        crop={"bucket": "b", "objectKey": "k"}, draft={"text": "t"},
        extraction={"success": True}, now=NOW,
    )
    assert await reset_item_for_retry(database, batch["_id"], user_id, item_id, now=NOW) is False
    loaded = await get_batch(database, batch["_id"], user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["status"] == ItemState.READY.value


@pytest.mark.asyncio
async def test_update_item_draft_merges_allowed_keys_and_returns_none_for_missing(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image, items = await _batch_with_items(database, user_id, settings)
    item_id = items[0]["itemId"]

    updated = await update_item_draft(
        database, batch["_id"], user_id, item_id,
        draft_update={
            "text": "What is 2+2?",
            "problemType": "short-answer",
            "graphDsl": "g",
            "correctAnswer": "4",
            "tags": ["tag-a"],
            "subject": "math",
            "disallowedKey": "ignored",
        },
        now=NOW,
    )
    assert updated is not None
    loaded = await get_batch(database, batch["_id"], user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["draft"]["text"] == "What is 2+2?"
    assert item["draft"]["problemType"] == "short-answer"
    assert item["draft"]["graphDsl"] == "g"
    assert item["draft"]["correctAnswer"] == "4"
    assert item["draft"]["tags"] == ["tag-a"]
    assert item["draft"]["subject"] == "math"
    assert "disallowedKey" not in item["draft"]
    assert item["updatedAt"] == NOW
    assert loaded["updatedAt"] == NOW

    # Missing item -> None.
    assert await update_item_draft(
        database, batch["_id"], user_id, "does-not-exist", draft_update={"text": "x"}, now=NOW
    ) is None


@pytest.mark.asyncio
async def test_mark_item_deleted_saves_previous_status_and_is_idempotent(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image, items = await _batch_with_items(database, user_id, settings)
    item_id = items[0]["itemId"]

    assert await mark_item_deleted(database, batch["_id"], user_id, item_id, now=NOW) is True
    loaded = await get_batch(database, batch["_id"], user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["status"] == ItemState.DELETED.value
    assert item["previousStatus"] == ItemState.QUEUED.value
    assert item["deletedAt"] == NOW
    assert item["updatedAt"] == NOW

    # Idempotent: re-deleting a DELETED item returns True without changing state.
    later = NOW + timedelta(seconds=5)
    assert await mark_item_deleted(database, batch["_id"], user_id, item_id, now=later) is True
    loaded = await get_batch(database, batch["_id"], user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["deletedAt"] == NOW  # unchanged


@pytest.mark.asyncio
async def test_undo_item_deletion_restores_status_and_returns_false_when_not_deleted(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image, items = await _batch_with_items(database, user_id, settings)
    item_id = items[0]["itemId"]

    # Not deleted -> False.
    assert await undo_item_deletion(database, batch["_id"], user_id, item_id, now=NOW) is False

    await mark_item_deleted(database, batch["_id"], user_id, item_id, now=NOW)
    later = NOW + timedelta(seconds=5)

    assert await undo_item_deletion(database, batch["_id"], user_id, item_id, now=later) is True
    loaded = await get_batch(database, batch["_id"], user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["status"] == ItemState.QUEUED.value
    assert "deletedAt" not in item
    assert "previousStatus" not in item
    assert item["updatedAt"] == later
    assert loaded["updatedAt"] == later


@pytest.mark.asyncio
async def test_submit_items_and_complete_batch_completes_only_when_all_submitted(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch, image, items = await _batch_with_items(database, user_id, settings)
    item_id = items[0]["itemId"]
    submit = {"submittedProblemId": "prob-1", "success": True}

    # Not all submitted (submit-failed) -> batch stays ACTIVE.
    result = await submit_items_and_complete_batch(
        database, batch["_id"], user_id,
        item_results=[{"itemId": item_id, "status": ItemState.SUBMIT_FAILED.value, "submit": submit}],
        now=NOW,
    )
    assert result["status"] == BatchState.ACTIVE.value
    loaded = await get_batch(database, batch["_id"], user_id)
    item = next(i for i in loaded["items"] if i["itemId"] == item_id)
    assert item["status"] == ItemState.SUBMIT_FAILED.value
    assert item["submit"] == submit
    assert item["updatedAt"] == NOW

    # All submitted -> batch COMPLETED.
    later = NOW + timedelta(seconds=5)
    result = await submit_items_and_complete_batch(
        database, batch["_id"], user_id,
        item_results=[{"itemId": item_id, "status": ItemState.SUBMITTED.value, "submit": submit}],
        now=later,
    )
    assert result["status"] == BatchState.COMPLETED.value
    assert result["updatedAt"] == later


# ---------------------------------------------------------------------------
# ValueError path tests for _load_batch_for_update (missing batch) and
# commit_image_boxes (missing image). All repository mutation functions that
# use _load_batch_for_update should raise the same ValueError for a
# non-existent batch.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_batch_raises_value_error(
    database: FakeDatabase, user_id: ObjectId
) -> None:
    missing_batch_id = ObjectId()

    with pytest.raises(ValueError, match="Batch not found"):
        await start_image_detection(database, missing_batch_id, user_id, "img-1", now=NOW)

    with pytest.raises(ValueError, match="Batch not found"):
        await save_item_extraction_success(
            database, missing_batch_id, user_id, "item-1",
            crop={}, draft={}, extraction={}, now=NOW,
        )

    with pytest.raises(ValueError, match="Batch not found"):
        await reset_item_for_retry(database, missing_batch_id, user_id, "item-1", now=NOW)

    with pytest.raises(ValueError, match="Batch not found"):
        await delete_batch_image(database, missing_batch_id, user_id, "img-1", now=NOW)

    with pytest.raises(ValueError, match="Batch not found"):
        await submit_items_and_complete_batch(
            database, missing_batch_id, user_id, item_results=[], now=NOW,
        )


@pytest.mark.asyncio
async def test_commit_image_boxes_raises_image_not_found(
    database: FakeDatabase, user_id: ObjectId, settings: Settings
) -> None:
    batch = await create_batch(database, user_id, settings, now=NOW)

    with pytest.raises(ValueError, match="Image not found"):
        await commit_image_boxes(database, batch["_id"], user_id, "nonexistent", now=NOW)
