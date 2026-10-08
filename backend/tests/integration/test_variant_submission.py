"""Real-Mongo transactional evidence for variant admission (issue #614).

Fake sessions cannot prove atomicity: rollback, write-conflict retries and
concurrent admission interleavings run against the isolated replica set here.
Storage and model calls stay faked; the transaction, session propagation and
document round-trips are real.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion.documents import build_audit_image_key
from app.infrastructure.ingestion.repository import (
    INGESTION_BATCHES_COLLECTION,
    claim_variation_work,
    create_batch,
    request_variation_generation,
    save_variation_candidate_checkpoint,
    save_variation_result,
)
from app.infrastructure.storage.mongo import (
    MongoClientAdapter,
    ensure_database_setup,
)
from app.infrastructure.ingestion.cleanup import run_batch_cleanup
from app.presentation.bulk_ingestion import submit_batch
from app.presentation.bulk_serialization import SubmitSummaryResponse
from app.presentation.errors import ApiError
from app.presentation.variant_submission import (
    admit_variant_item,
    copy_crop_to_audit_storage,
    register_admitted_problem_tags,
)
from app.problem_variation import IngestionMode

pytestmark = pytest.mark.real_mongo

REAL_MONGO_DATABASE_ENV = "LEARNLOOP_REAL_MONGO_DATABASE"
_REAL_MONGO_NAME_RE = re.compile(r"learnloop_test_[A-Za-z0-9_-]+")
S3_BUCKET = "learnloop-media"


def validate_real_mongo_database_name(name: str | None) -> str:
    """Sentinel-name discipline shared with test_ingestion_atomicity.py."""
    if name is None or not _REAL_MONGO_NAME_RE.fullmatch(name):
        raise RuntimeError(
            f"{REAL_MONGO_DATABASE_ENV} must match 'learnloop_test_[A-Za-z0-9_-]+'; "
            f"got {name!r}"
        )
    return name


@pytest_asyncio.fixture(loop_scope="function")
async def real_database() -> Any:
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
    client = AsyncMongoClient(uri)
    try:
        database = client.get_database(database_name)
        await ensure_database_setup(database)
        yield database
    finally:
        try:
            validated = validate_real_mongo_database_name(
                os.environ.get(REAL_MONGO_DATABASE_ENV)
            )
            await client.drop_database(validated)
        finally:
            await client.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(bulk_ingestion_batch_ttl_seconds=3600)


@pytest.fixture
def user_id() -> ObjectId:
    return ObjectId()

ORIGINAL = {
    "text": "What is 2+2?",
    "problemType": "short-answer",
    "graphDsl": None,
    "correctAnswer": "4",
    "subject": "math",
}
CANDIDATE = {
    "text": "What is 3+5?",
    "problemType": "short-answer",
    "graphDsl": None,
    "correctAnswer": "8",
    "subject": "math",
    "generator": {"provider": "fake", "model": "gen-model"},
}
PASSING_VALIDATION = {
    "verdict": "pass",
    "failures": [],
    "reports": [
        {
            "validatorModel": {"provider": "fake", "model": "val-model"},
            "originalSolvedAnswer": "4",
            "variantSolvedAnswer": "8",
            "originalSolutionSummary": "2+2=4",
            "variantSolutionSummary": "3+5=8",
            "checks": {},
            "answerComparisonOriginal": {"result": "equivalent", "evidence": "same"},
            "answerComparisonVariant": {"result": "equivalent", "evidence": "same"},
        }
    ],
}


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_keys: list[str] = []
        self.deleted: list[str] = []

    def get_object(self, bucket: str, object_key: str) -> bytes:
        return self.objects[(bucket, object_key)]

    def put_object(self, bucket: str, object_key: str, payload: bytes, content_type: str) -> None:
        self.objects[(bucket, object_key)] = payload
        self.put_keys.append(object_key)

    def delete_object(self, bucket: str, object_key: str) -> None:
        self.deleted.append(object_key)
        self.objects.pop((bucket, object_key), None)

    def seed(self, bucket: str, object_key: str, payload: bytes) -> None:
        self.objects[(bucket, object_key)] = payload


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def real_adapter(real_database: Any) -> MongoClientAdapter:
    client: AsyncMongoClient[Any] = real_database.client
    return MongoClientAdapter(client_factory=lambda *args, **kwargs: client)


async def _seed_ready_variant_item(
    real_database: Any,
    user_id: Any,
    settings: Settings,
    *,
    item_id: str = "item-1",
    mode: IngestionMode = IngestionMode.DATA_ONLY,
) -> Any:
    now = datetime.now(UTC)
    batch = await create_batch(
        real_database, user_id, settings, ingestion_mode=mode, now=now,
    )
    item = {
        "itemId": item_id,
        "imageId": "image-1",
        "batchId": batch["_id"],
        "status": "ready",
        "order": 0,
        "draft": {
            "text": ORIGINAL["text"],
            "problemType": "short-answer",
            "graphDsl": None,
            "correctAnswer": ORIGINAL["correctAnswer"],
            "tags": ["algebra"],
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
        "origin": {"batchId": str(batch["_id"]), "itemId": item_id},
        "crop": {
            "bucket": S3_BUCKET,
            "objectKey": f"users/{user_id}/ingestion/crops/{item_id}.png",
            "contentType": "image/png",
            "sizeBytes": 10,
            "width": 5,
            "height": 5,
            "uploadedAt": now,
        },
        "leaseUntil": None,
        "createdAt": now,
        "updatedAt": now,
    }
    batch["items"] = [item]
    await real_database[INGESTION_BATCHES_COLLECTION].replace_one(
        {"_id": batch["_id"]}, dict(batch)
    )
    await _drive_to_pass(real_database, user_id, batch["_id"], item_id)
    return batch["_id"]


async def _drive_to_pass(
    real_database: Any,
    user_id: Any,
    batch_id: Any,
    item_id: str,
    *,
    expected_revision: int = 0,
) -> None:
    now = datetime.now(UTC)
    await request_variation_generation(
        real_database, batch_id, user_id, item_id,
        original=dict(ORIGINAL), expected_revision=expected_revision, now=now,
    )
    claimed = await claim_variation_work(
        real_database, batch_id, user_id, item_id,
        lease_timeout_seconds=300, now=now,
    )
    assert claimed is not None
    token = claimed["variation"]["claimToken"]
    claimed_revision = expected_revision + 1
    assert await save_variation_candidate_checkpoint(
        real_database, batch_id, user_id, item_id,
        token=token, claimed_revision=claimed_revision,
        candidate=dict(CANDIDATE), now=now,
    )
    assert await save_variation_result(
        real_database, batch_id, user_id, item_id,
        token=token, claimed_revision=claimed_revision, verdict="pass",
        validation=dict(PASSING_VALIDATION), now=now,
        candidate_present=True,
    )


def _audit_copy(
    storage: FakeStorage, user_id: Any, batch_id: Any, item_id: str
) -> dict[str, Any]:
    crop = {
        "bucket": S3_BUCKET,
        "objectKey": f"users/{user_id}/ingestion/crops/{item_id}.png",
        "contentType": "image/png",
        "sizeBytes": 10,
        "width": 5,
        "height": 5,
        "uploadedAt": datetime.now(UTC),
    }
    storage.seed(S3_BUCKET, crop["objectKey"], b"crop-bytes")
    return copy_crop_to_audit_storage(
        storage, user_id, batch_id, item_id, crop, now=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_concurrent_admission_creates_exactly_one_problem(
    real_database: Any,
    real_adapter: MongoClientAdapter,
    user_id: Any,
    settings: Settings,
    storage: FakeStorage,
) -> None:
    batch_id = await _seed_ready_variant_item(real_database, user_id, settings)
    audit_image = _audit_copy(storage, user_id, batch_id, "item-1")
    now = datetime.now(UTC)

    async def admit() -> dict[str, Any]:
        return await admit_variant_item(
            real_database, real_adapter, user_id, batch_id, "item-1",
            audit_image=audit_image, tags=["algebra"], now=now,
        )

    outcomes = await asyncio.gather(admit(), admit())

    problem_ids = {outcome["problemId"] for outcome in outcomes}
    assert len(problem_ids) == 1
    assert await real_database["problems"].count_documents({}) == 1
    assert await real_database["solution_generation_tasks"].count_documents({}) == 1
    batch_doc = await real_database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    stored_item = batch_doc["items"][0]
    assert stored_item["status"] == "submitted"
    assert stored_item["submit"]["submittedProblemId"] == outcomes[0]["problemId"]
    # Mongo round-trips the stored provenance; the model must still validate it.
    problem = await real_database["problems"].find_one(
        {"_id": ObjectId(outcomes[0]["problemId"])}
    )
    assert problem["variation"]["acceptedVariant"]["correctAnswer"]["display"] == "8"
    assert problem["sourceImage"] is None


@pytest.mark.asyncio
async def test_concurrent_variant_submits_complete_idempotently(
    real_database: Any,
    real_adapter: MongoClientAdapter,
    user_id: Any,
    settings: Settings,
    storage: FakeStorage,
) -> None:
    """Two concurrent submit requests both load the ACTIVE snapshot: both
    admit the same committed Problem and both run completion. The loser of
    the completion update must short-circuit on the terminal completed state
    instead of spinning and raising a 500 after the item already committed.
    """
    batch_id = await _seed_ready_variant_item(real_database, user_id, settings)
    storage.seed(
        S3_BUCKET, f"users/{user_id}/ingestion/crops/item-1.png", b"crop-bytes"
    )
    user = {"_id": user_id}

    async def submit() -> SubmitSummaryResponse:
        return await submit_batch(
            str(batch_id), real_database, user, real_adapter, storage
        )

    summaries = await asyncio.gather(submit(), submit())

    problem_ids = set()
    for summary in summaries:
        assert summary.submitSummary.status == "completed"
        (item,) = summary.submitSummary.items
        assert item.failureCode is None
        assert item.submittedProblemId is not None
        problem_ids.add(item.submittedProblemId)
    assert len(problem_ids) == 1
    assert await real_database["problems"].count_documents({}) == 1
    assert await real_database["solution_generation_tasks"].count_documents({}) == 1
    batch_doc = await real_database[INGESTION_BATCHES_COLLECTION].find_one(
        {"_id": batch_id}
    )
    assert batch_doc["status"] == "completed"


@pytest.mark.asyncio
async def test_generate_wins_mid_admission_transaction_retries_fail_closed(
    real_database: Any,
    real_adapter: MongoClientAdapter,
    user_id: Any,
    settings: Settings,
    storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The committed-insert rollback: Generate's write conflicts the open
    transaction, pymongo retries the callback, and the fresh re-read rejects
    the no-longer-current candidate — leaving no problem behind."""
    import app.presentation.variant_submission as variant_submission_module

    batch_id = await _seed_ready_variant_item(real_database, user_id, settings)
    detail_revision = 1  # request_variation_generation bumped 0 -> 1
    audit_image = _audit_copy(storage, user_id, batch_id, "item-1")

    admission_paused = asyncio.Event()
    release_admission = asyncio.Event()
    real_record = variant_submission_module.record_variant_item_submission

    async def pausing_record(*args: Any, **kwargs: Any) -> bool:
        # First invocation: inside the transaction, after the problem insert.
        admission_paused.set()
        await asyncio.wait_for(release_admission.wait(), timeout=10)
        return await real_record(*args, **kwargs)

    monkeypatch.setattr(
        variant_submission_module, "record_variant_item_submission", pausing_record
    )

    async def admission_with_generate() -> None:
        async def admit() -> None:
            await admit_variant_item(
                real_database, real_adapter, user_id, batch_id, "item-1",
                audit_image=audit_image, tags=["algebra"],
                now=datetime.now(UTC),
            )

        admit_task = asyncio.create_task(admit())
        await asyncio.wait_for(admission_paused.wait(), timeout=10)
        # Generate wins while the transaction holds the inserted problem:
        # a new generation bumps the item revision and clears the candidate.
        await request_variation_generation(
            real_database, batch_id, user_id, "item-1",
            original=dict(ORIGINAL), expected_revision=detail_revision,
            now=datetime.now(UTC),
        )
        release_admission.set()
        with pytest.raises(ApiError) as excinfo:
            await admit_task
        assert excinfo.value.code == "VARIANT_INVALIDATED"

    await admission_with_generate()

    # Rollback evidence: no problem, no task, item queued for regeneration.
    assert await real_database["problems"].count_documents({}) == 0
    assert await real_database["solution_generation_tasks"].count_documents({}) == 0
    batch_doc = await real_database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    stored_item = batch_doc["items"][0]
    assert stored_item["status"] == "ready"
    assert stored_item["variation"]["status"] == "queued"
    assert stored_item["submit"]["submittedProblemId"] is None


@pytest.mark.asyncio
async def test_edit_after_pass_prevents_admission_and_retry_succeeds(
    real_database: Any,
    real_adapter: MongoClientAdapter,
    user_id: Any,
    settings: Settings,
    storage: FakeStorage,
) -> None:
    batch_id = await _seed_ready_variant_item(real_database, user_id, settings)
    # Semantic edit after the PASS: the validated candidate is stale.
    await real_database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id, "items.itemId": "item-1"},
        {"$set": {"items.$.contentRevision": 2}},
    )
    audit_image = _audit_copy(storage, user_id, batch_id, "item-1")

    with pytest.raises(ApiError) as excinfo:
        await admit_variant_item(
            real_database, real_adapter, user_id, batch_id, "item-1",
            audit_image=audit_image, tags=["algebra"], now=datetime.now(UTC),
        )
    assert excinfo.value.code == "VARIANT_INVALIDATED"
    assert await real_database["problems"].count_documents({}) == 0

    # Revalidation produces a current PASS again; the retry admits it.
    await _drive_to_pass(real_database, user_id, batch_id, "item-1", expected_revision=2)
    outcome = await admit_variant_item(
        real_database, real_adapter, user_id, batch_id, "item-1",
        audit_image=audit_image, tags=["algebra"], now=datetime.now(UTC),
    )
    assert await real_database["problems"].count_documents(
        {"_id": ObjectId(outcome["problemId"])}
    ) == 1
    batch_doc = await real_database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    assert batch_doc["items"][0]["status"] == "submitted"


@pytest.mark.asyncio
async def test_tag_registration_failure_after_commit_keeps_problem(
    real_database: Any,
    real_adapter: MongoClientAdapter,
    user_id: Any,
    settings: Settings,
    storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_id = await _seed_ready_variant_item(real_database, user_id, settings)
    audit_image = _audit_copy(storage, user_id, batch_id, "item-1")
    outcome = await admit_variant_item(
        real_database, real_adapter, user_id, batch_id, "item-1",
        audit_image=audit_image, tags=["algebra"], now=datetime.now(UTC),
    )
    problem_id = ObjectId(outcome["problemId"])

    async def failing_register(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("tag index unavailable")

    monkeypatch.setattr(
        "app.presentation.variant_submission._register_tags", failing_register
    )
    from app.presentation.variant_submission import register_admitted_problem_tags

    await register_admitted_problem_tags(real_database, user_id, ["algebra"])

    assert await real_database["problems"].count_documents({"_id": problem_id}) == 1


@pytest.mark.asyncio
async def test_cleanup_protects_referenced_audit_copy_and_removes_pending(
    real_database: Any,
    real_adapter: MongoClientAdapter,
    user_id: Any,
    settings: Settings,
    storage: FakeStorage,
) -> None:
    batch_id = await _seed_ready_variant_item(real_database, user_id, settings)
    audit_image = _audit_copy(storage, user_id, batch_id, "item-1")
    audit_key = audit_image["objectKey"]

    # Item A is admitted (problem references the audit copy); a second
    # item's copy is pending with no reference (its admission never ran).
    outcome = await admit_variant_item(
        real_database, real_adapter, user_id, batch_id, "item-1",
        audit_image=audit_image, tags=[], now=datetime.now(UTC),
    )
    assert outcome["problemId"]

    pending_key = build_audit_image_key(user_id, batch_id, "item-pending", "image/png")
    storage.objects[(S3_BUCKET, pending_key)] = b"pending-bytes"

    # A second item whose crop was copied (admission pending) but which has
    # no Problem reference: cleanup must reclaim its audit copy.
    now = datetime.now(UTC)
    await real_database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id},
        {
            "$push": {
                "items": {
                    "itemId": "item-pending",
                    "imageId": "image-1",
                    "batchId": batch_id,
                    "status": "ready",
                    "order": 1,
                    "draft": {"text": "t", "problemType": "short-answer",
                              "graphDsl": None, "correctAnswer": "4", "tags": [],
                              "subject": "math"},
                    "extraction": {}, "retryCount": 0, "contentRevision": 1,
                    "variation": {"status": "ready", "generationCount": 1,
                                  "original": dict(ORIGINAL), "candidate": dict(CANDIDATE),
                                  "validation": dict(PASSING_VALIDATION),
                                  "validatedRevision": 1, "claimToken": None,
                                  "leaseUntil": None, "queuedAt": now},
                    "submit": {"submittedProblemId": None, "success": None,
                               "failureCode": None, "failureMessage": None},
                    "origin": {"itemId": "item-pending"},
                    "crop": {"bucket": S3_BUCKET,
                             "objectKey": f"users/{user_id}/ingestion/crops/item-pending.png",
                             "contentType": "image/png", "sizeBytes": 10,
                             "width": 5, "height": 5, "uploadedAt": now},
                    "leaseUntil": None, "createdAt": now, "updatedAt": now,
                }
            }
        },
    )

    # Make the batch a cleanup candidate (expired while ACTIVE).
    await real_database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id},
        {"$set": {"expiresAt": datetime.now(UTC) - timedelta(seconds=1)}},
    )

    cleaned = await run_batch_cleanup(real_database, storage, now=datetime.now(UTC))
    assert cleaned == 1

    # The referenced audit object survives cleanup; the unreferenced pending
    # copy and both temporary crops are gone.
    assert storage.objects.get((S3_BUCKET, audit_key)) == b"crop-bytes"
    assert pending_key in storage.deleted
    crop_key = f"users/{user_id}/ingestion/crops/item-1.png"
    assert crop_key in storage.deleted
    assert f"users/{user_id}/ingestion/crops/item-pending.png" in storage.deleted
