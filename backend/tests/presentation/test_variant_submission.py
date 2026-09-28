"""Unit tests for transactional variant admission (issue #614).

FakeDatabase tier: admission guards, idempotency, provenance shape, audit
copy determinism and post-commit tag safety. Rollback and concurrency
evidence deliberately lives in the real-Mongo integration suite
(tests/integration/test_variant_submission.py) — fake sessions cannot prove
atomicity.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId

from app.domain.ingestion import BatchState, ItemState
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion.repository import (
    INGESTION_BATCHES_COLLECTION,
    claim_variation_work,
    create_batch,
    request_variation_generation,
    save_variation_candidate_checkpoint,
    save_variation_result,
)
from app.presentation.errors import ApiError
from app.presentation.tag_registration import _register_tags
from app.presentation.variant_submission import (
    admit_variant_item,
    copy_crop_to_audit_storage,
    register_admitted_problem_tags,
)
from app.problem_variation import IngestionMode
from tests.test_utils.db_fakes import FakeDatabase

S3_BUCKET = "learnloop-media"
NOW = datetime.now(UTC)


class FakeSession:
    async def with_transaction(self, callback: Any) -> Any:
        return await callback(self)


class FakeMongoAdapter:
    @contextlib.asynccontextmanager
    async def start_session(self):  # noqa: ANN201
        yield FakeSession()


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[tuple[str, str, str, bytes]] = []
        self.get_calls: list[tuple[str, str]] = []

    def get_object(self, bucket: str, object_key: str) -> bytes:
        self.get_calls.append((bucket, object_key))
        return self.objects[(bucket, object_key)]

    def put_object(self, bucket: str, object_key: str, payload: bytes, content_type: str) -> None:
        self.objects[(bucket, object_key)] = payload
        self.put_calls.append((bucket, object_key, content_type, payload))

    def seed(self, bucket: str, object_key: str, payload: bytes) -> None:
        self.objects[(bucket, object_key)] = payload


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
PASSING_REPORT = {
    "validatorModel": {"provider": "fake", "model": "val-model"},
    "originalSolvedAnswer": "4",
    "variantSolvedAnswer": "8",
    "originalSolutionSummary": "2+2=4",
    "variantSolutionSummary": "3+5=8",
    "checks": {},
    "answerComparisonOriginal": {"result": "equivalent", "evidence": "same"},
    "answerComparisonVariant": {"result": "equivalent", "evidence": "same"},
}
PASSING_VALIDATION = {
    "verdict": "pass",
    "failures": [],
    "reports": [PASSING_REPORT],
}
CROP = {
    "bucket": S3_BUCKET,
    "objectKey": "users/u1/ingestion/crops/crop-1.png",
    "contentType": "image/png",
    "sizeBytes": 10,
    "width": 5,
    "height": 5,
    "uploadedAt": NOW,
}


def _storage_with_crop() -> FakeStorage:
    storage = FakeStorage()
    storage.seed(S3_BUCKET, CROP["objectKey"], b"crop-bytes")
    return storage


async def _seed_ready_variant_item(
    database: FakeDatabase,
    *,
    item_id: str = "item-1",
    validated_revision: int | None = None,
    variation_overrides: dict[str, Any] | None = None,
) -> ObjectId:
    """Active data-only batch with one extraction-ready, validated item."""
    settings = Settings(s3_bucket=S3_BUCKET)
    batch = await create_batch(
        database, "user-1", settings,
        ingestion_mode=IngestionMode.DATA_ONLY, now=NOW,
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
        "crop": dict(CROP),
        "leaseUntil": None,
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    batch["items"] = [item]
    await database[INGESTION_BATCHES_COLLECTION].replace_one(
        {"_id": batch["_id"]}, dict(batch)
    )

    # Drive through the real guarded writers to a current PASS candidate.
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=dict(ORIGINAL), expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id,
        lease_timeout_seconds=300, now=NOW,
    )
    assert claimed is not None
    token = claimed["variation"]["claimToken"]
    assert await save_variation_candidate_checkpoint(
        database, batch["_id"], "user-1", item_id,
        token=token, claimed_revision=1,
        candidate=dict(CANDIDATE), now=NOW,
    )
    assert await save_variation_result(
        database, batch["_id"], "user-1", item_id,
        token=token, claimed_revision=1, verdict="pass",
        validation=dict(PASSING_VALIDATION), now=NOW,
    )
    if variation_overrides or validated_revision is not None:
        update: dict[str, Any] = {"variation": dict(variation_overrides or {})}
        if validated_revision is not None:
            update["contentRevision"] = validated_revision
        await database[INGESTION_BATCHES_COLLECTION].update_one(
            {"_id": batch["_id"], "items.itemId": item_id},
            {"$set": {"items.$.contentRevision": update["contentRevision"]}}
            if validated_revision is not None
            else {"$set": {"items.$.variation": update["variation"]}},
        )
    return batch["_id"]


async def _admit(
    database: FakeDatabase,
    batch_id: ObjectId,
    *,
    item_id: str = "item-1",
    storage: FakeStorage | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    storage = storage or _storage_with_crop()
    audit_image = copy_crop_to_audit_storage(
        storage, "user-1", batch_id, item_id, dict(CROP), now=NOW,
    )
    return await admit_variant_item(
        database, FakeMongoAdapter(), "user-1", batch_id, item_id,
        audit_image=audit_image, tags=tags or ["algebra"], now=NOW,
    )


@pytest.mark.asyncio
async def test_admit_creates_problem_item_record_and_solution_task() -> None:
    database = FakeDatabase()
    batch_id = await _seed_ready_variant_item(database)
    storage = _storage_with_crop()

    outcome = await _admit(database, batch_id, storage=storage)

    assert outcome["alreadySubmitted"] is False
    problem = await database["problems"].find_one({"_id": ObjectId(outcome["problemId"])})
    assert problem is not None
    # Main content comes from the accepted variant, never the source draft.
    assert problem["text"] == "What is 3+5?"
    assert problem["correctAnswer"]["display"] == "8"
    assert problem["sourceImage"] is None
    assert problem["tags"] == ["algebra"]
    variation = problem["variation"]
    assert variation["mode"] == "data-only"
    assert variation["acceptedVariant"]["text"] == "What is 3+5?"
    assert variation["acceptedVariant"]["correctAnswer"]["display"] == "8"
    assert variation["original"]["text"] == "What is 2+2?"
    assert variation["original"]["correctAnswer"]["display"] == "4"
    assert variation["original"]["auditImage"]["objectKey"].startswith(
        f"users/user-1/problems/audit/{batch_id}/item-1."
    )
    assert variation["generator"] == {"provider": "fake", "model": "gen-model"}
    assert variation["generationCount"] == 1
    assert variation["validation"]["verdict"] == "pass"
    assert variation["validation"]["helperModel"] == {
        "provider": "fake", "model": "val-model",
    }
    assert variation["validation"]["reports"] == [PASSING_REPORT]
    # No worker fencing state leaks into the permanent provenance.
    assert not {"claimToken", "leaseUntil", "contentRevision", "validatedRevision", "status"} & set(
        variation
    )

    batch_doc = await database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    stored_item = next(i for i in batch_doc["items"] if i["itemId"] == "item-1")
    assert stored_item["status"] == "submitted"
    assert stored_item["submit"]["submittedProblemId"] == outcome["problemId"]
    assert stored_item["submit"]["success"] is True
    assert stored_item["variation"]["submitReservation"] is None

    task = await database["solution_generation_tasks"].find_one(
        {"problem_id": outcome["problemId"]}
    )
    assert task is not None
    # The audit copy landed in the permanent namespace; the crop stays.
    audit_key = variation["original"]["auditImage"]["objectKey"]
    assert storage.objects[(S3_BUCKET, audit_key)] == b"crop-bytes"
    assert (S3_BUCKET, CROP["objectKey"]) in set(storage.objects)


@pytest.mark.asyncio
async def test_audit_copy_is_deterministic_per_batch_and_item() -> None:
    storage = _storage_with_crop()
    first = copy_crop_to_audit_storage(
        storage, "user-1", "batch-1", "item-1", dict(CROP), now=NOW,
    )
    second = copy_crop_to_audit_storage(
        storage, "user-1", "batch-1", "item-1", dict(CROP), now=NOW,
    )
    assert first["objectKey"] == second["objectKey"]
    assert len(storage.put_calls) == 2
    assert len({call[1] for call in storage.put_calls}) == 1
    assert first["sha256"] == second["sha256"]
    assert first["contentType"] == "image/png"


@pytest.mark.asyncio
async def test_admission_rejects_stale_validated_revision_without_writes() -> None:
    database = FakeDatabase()
    batch_id = await _seed_ready_variant_item(database)
    # A semantic edit after the PASS bumps the item's contentRevision; the
    # validated candidate is no longer current and must never be saved.
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id, "items.itemId": "item-1"},
        {"$set": {"items.$.contentRevision": 2}},
    )

    with pytest.raises(ApiError) as excinfo:
        await _admit(database, batch_id)
    assert excinfo.value.code == "VARIANT_INVALIDATED"
    assert await database["problems"].count_documents({}) == 0
    batch_doc = await database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    stored_item = next(i for i in batch_doc["items"] if i["itemId"] == "item-1")
    assert stored_item["status"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "failed"},
        {"validation": {"verdict": "fail", "failures": [], "reports": []}},
        {"original": None},
        {"candidate": None},
    ],
)
async def test_admission_rejects_non_passing_variations(overrides: dict[str, Any]) -> None:
    database = FakeDatabase()
    batch_id = await _seed_ready_variant_item(database)
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id, "items.itemId": "item-1"},
        {"$set": {f"items.$.variation.{key}": value for key, value in overrides.items()}},
    )

    with pytest.raises(ApiError) as excinfo:
        await _admit(database, batch_id)
    assert excinfo.value.code == "VARIANT_INVALIDATED"
    assert await database["problems"].count_documents({}) == 0


@pytest.mark.asyncio
async def test_admission_rejects_deleted_item_and_expired_batch() -> None:
    database = FakeDatabase()
    batch_id = await _seed_ready_variant_item(database)
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id, "items.itemId": "item-1"},
        {"$set": {"items.$.status": "deleted"}},
    )
    with pytest.raises(ApiError) as excinfo:
        await _admit(database, batch_id)
    assert excinfo.value.code == "VARIANT_INVALIDATED"

    database = FakeDatabase()
    batch_id = await _seed_ready_variant_item(database)
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id},
        {"$set": {"expiresAt": NOW - timedelta(seconds=1)}},
    )
    with pytest.raises(ApiError) as excinfo:
        await _admit(database, batch_id)
    assert excinfo.value.code == "VARIANT_INVALIDATED"


@pytest.mark.asyncio
async def test_admit_returns_recorded_problem_for_already_submitted_item() -> None:
    database = FakeDatabase()
    batch_id = await _seed_ready_variant_item(database)
    existing_problem_id = str(ObjectId())
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch_id, "items.itemId": "item-1"},
        {
            "$set": {
                "items.$.status": "submitted",
                "items.$.submit": {
                    "submittedProblemId": existing_problem_id,
                    "success": True,
                    "failureCode": None,
                    "failureMessage": None,
                },
            }
        },
    )

    outcome = await _admit(database, batch_id)
    assert outcome == {"problemId": existing_problem_id, "alreadySubmitted": True}
    assert await database["problems"].count_documents({}) == 0


@pytest.mark.asyncio
async def test_transaction_receives_the_shared_session(monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase()
    batch_id = await _seed_ready_variant_item(database)

    import app.presentation.variant_submission as variant_submission_module

    captured: dict[str, Any] = {}
    real_enqueue = variant_submission_module.enqueue_solution_generation_task_for_problem

    async def spy_enqueue(*args: Any, **kwargs: Any) -> bool:
        captured["session"] = kwargs.get("session")
        return await real_enqueue(*args, **kwargs)

    monkeypatch.setattr(variant_submission_module, "enqueue_solution_generation_task_for_problem", spy_enqueue)

    await _admit(database, batch_id)
    assert isinstance(captured["session"], FakeSession)


@pytest.mark.asyncio
async def test_tag_registration_failure_after_commit_keeps_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase()
    batch_id = await _seed_ready_variant_item(database)
    outcome = await _admit(database, batch_id)

    async def failing_register(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("tags unavailable")

    monkeypatch.setattr(
        "app.presentation.variant_submission._register_tags", failing_register
    )
    await register_admitted_problem_tags(database, "user-1", ["algebra"])

    # The committed Problem is never deleted by a post-commit tag failure.
    assert await database["problems"].count_documents({"_id": ObjectId(outcome["problemId"])}) == 1


@pytest.mark.asyncio
async def test_post_commit_tag_registration_registers_draft_tags() -> None:
    database = FakeDatabase()
    batch_id = await _seed_ready_variant_item(database)
    await register_admitted_problem_tags(database, "user-1", ["algebra"])
    tag = await database["tags"].find_one({"userId": "user-1", "name": "algebra"})
    assert tag is not None
