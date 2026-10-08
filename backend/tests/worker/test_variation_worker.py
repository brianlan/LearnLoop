"""Worker tests for the durable variation worker (issue #613 deliverable).

All model behavior is injected: the generator is a fake client, and the
validation-phase orchestration (``generate_and_validate``) is monkeypatched at
the worker module boundary so tests drive checkpointing, reclaim/resume,
stale-result rejection and deletion cancellation deterministically.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain.ingestion.variation import (
    AnswerComparison,
    AssessmentFailure,
    Check,
    ModelIdentity,
    ValidatorReport,
    VariantAssessment,
    VariantCandidate,
    VariantGenerationResult,
)
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion.repository import (
    INGESTION_BATCHES_COLLECTION,
    claim_variation_work,
    create_batch,
    mark_item_deleted,
    renew_variation_lease,
    request_variation_generation,
    save_variation_candidate_checkpoint,
    save_variation_result,
    undo_item_deletion,
    update_item_draft_variant,
)
from app.infrastructure.storage.mongo import Document
from app.infrastructure.worker import variation_worker as variation_worker_module
from app.infrastructure.worker.variation_worker import (
    claim_next_variation_work,
    process_variation,
    run_variation_worker,
)
from app.infrastructure.vlm.base_client import BaseVLMError
from app.problem_variation import IngestionMode, VariationStatus
from tests.domain.test_variant_validation import PASSING_CATEGORIES
from tests.test_utils.db_fakes import FakeDatabase

NOW = datetime.now(UTC)  # repo-direct tests only; process_variation writes with real now, so leases it consumes must anchor to datetime.now(UTC)


@pytest.fixture(autouse=True)
def _fresh_now():
    """Re-anchor NOW at every test's execution time.

    ``NOW`` anchors leases that the worker compares against the real clock
    (``process_variation`` renews and saves with ``datetime.now(UTC)``). A
    module-level value goes stale on slow runs: once a test executes more
    than the 300s lease window after import, ``renew_variation_lease``
    refuses and resume tests get stuck in ``validating``. CI observed
    exactly that (5.5-minute suite). Re-stamping per test keeps the 300s
    window anchored to actual execution time.
    """
    global NOW
    NOW = datetime.now(UTC)

SOURCE_SNAPSHOT = {
    "text": "A train travels 180 km in 3 hours. What is its speed in km/h?",
    "problemType": "short-answer",
    "graphDsl": None,
    "correctAnswer": "60",
    "subject": "math",
}

GENERATED_CANDIDATE = {
    "text": "A car travels 180 km in 3 hours. What is its speed in km/h?",
    "problemType": "short-answer",
    "subject": "math",
    "graphDsl": None,
    "correctAnswer": "60",
    "generator": {"provider": "fake", "model": "gen-model"},
}


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "variation_lease_timeout_seconds": 300,
        "variation_worker_poll_interval_seconds": 3600,
    }
    values.update(overrides)
    return Settings(**values)


async def seed_variant_batch(
    database: FakeDatabase,
    *,
    item_count: int = 1,
) -> tuple[Document, list[dict[str, Any]]]:
    """Seed an active data-only batch with queued items ready for generation."""
    settings = make_settings()
    batch = await create_batch(
        database,
        "user-1",
        settings,
        ingestion_mode=IngestionMode.DATA_ONLY,
        now=NOW,
    )
    items = []
    for index in range(item_count):
        item = {
            "itemId": f"item-{index}",
            "imageId": "image-1",
            "batchId": batch["_id"],
            "status": "ready",
            "order": index,
            "draft": dict(SOURCE_SNAPSHOT, tags=[]),
            "extraction": {},
            "retryCount": 0,
            "contentRevision": 0,
            "variation": {
                "status": VariationStatus.NOT_REQUESTED.value,
                "generationCount": 0,
                "original": None,
                "candidate": None,
                "validation": None,
                "validatedRevision": None,
                "claimToken": None,
                "leaseUntil": None,
                "queuedAt": None,
            },
            "submit": {},
            "origin": {"itemId": f"item-{index}"},
            "leaseUntil": None,
            "createdAt": NOW,
            "updatedAt": NOW,
        }
        items.append(item)
    batch["items"] = items
    # create_batch already inserted the document; replace it with the
    # item-bearing version (the fake stores deep copies).
    await database[INGESTION_BATCHES_COLLECTION].replace_one(
        {"_id": batch["_id"]}, dict(batch)
    )
    return batch, items


class FakeGenerator:
    def __init__(self) -> None:
        self.identity = {"provider": "fake", "model": "gen-model"}
        self.calls: list[dict[str, Any]] = []
        self.responses: list[Any] = []

    async def generate_candidate(self, *, mode: str, source: Any) -> VariantCandidate:
        self.calls.append({"mode": mode, "source": source})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeValidator:
    """Minimal validator client driving the real generate_and_validate."""

    def __init__(
        self,
        *,
        report: ValidatorReport | None = None,
        error: BaseVLMError | None = None,
    ) -> None:
        self.identity = {"provider": "fake", "model": "val-model"}
        self.report = report
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def produce_report(
        self, *, mode: str, source: Any, candidate: Any
    ) -> ValidatorReport:
        self.calls.append({"mode": mode})
        if self.error is not None:
            raise self.error
        assert self.report is not None
        return self.report


class FakeHelper:
    def __init__(self, *, error: BaseVLMError | None = None) -> None:
        self.identity = {"provider": "fake", "model": "helper-model"}
        self.error = error

    async def compare_answer_pairs(
        self, **kwargs: Any
    ) -> tuple[AnswerComparison, AnswerComparison]:
        if self.error is not None:
            raise self.error
        return (
            AnswerComparison(result="equivalent", evidence="both 60"),
            AnswerComparison(result="equivalent", evidence="both 60"),
        )


def passing_validator_report() -> ValidatorReport:
    """A report as produce_report builds it: no helper comparisons yet —
    comparisons are helper-derived and assigned only after a successful
    helper call (#671)."""
    checks = {
        name: Check(category=category, evidence="clear")
        for name, category in PASSING_CATEGORIES.items()
    }
    return ValidatorReport(
        validatorModel=ModelIdentity(provider="fake", model="val-model"),
        originalSolvedAnswer="60",
        variantSolvedAnswer="60",
        originalSolutionSummary="distance over time",
        variantSolutionSummary="distance over time",
        checks=checks,
    )


def passing_result(candidate: dict[str, Any]) -> VariantGenerationResult:
    return VariantGenerationResult(
        candidate=VariantCandidate.model_validate(candidate),
        reports=[],
        assessment=VariantAssessment(verdict="pass", failures=[]),
    )


def failing_result(evidence: str) -> VariantGenerationResult:
    return VariantGenerationResult(
        candidate=VariantCandidate.model_validate(GENERATED_CANDIDATE),
        reports=[],
        assessment=VariantAssessment(
            verdict="fail",
            failures=[AssessmentFailure(kind="content", evidence=evidence)],
        ),
    )


async def _load_item(database: FakeDatabase, batch_id: Any, item_id: str) -> dict[str, Any]:
    batch = await database[INGESTION_BATCHES_COLLECTION].find_one({"_id": batch_id})
    return next(i for i in batch["items"] if i["itemId"] == item_id)


async def test_claim_next_variation_work_claims_queued_item() -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    await request_variation_generation(
        database, batch["_id"], "user-1", items[0]["itemId"],
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )

    claimed = await claim_next_variation_work(database, make_settings(), now=NOW)
    assert claimed is not None
    claimed_batch, claimed_item = claimed
    assert claimed_batch["_id"] == batch["_id"]
    variation = claimed_item["variation"]
    assert variation["status"] == VariationStatus.GENERATING.value
    assert variation["claimToken"]
    assert variation["leaseUntil"] > NOW

    # A second claim finds nothing (the only work is in flight, lease fresh).
    assert await claim_next_variation_work(database, make_settings(), now=NOW) is None


@pytest.mark.asyncio
async def test_legacy_batch_claimed_and_generation_uses_canonical_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy data-and-wording batches stay claimable and continue under the
    canonical transfer-variant contract (#656)."""
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch["_id"]}, {"$set": {"ingestionMode": "data-and-wording"}}
    )
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )

    claimed_pair = await claim_next_variation_work(database, make_settings(), now=NOW)
    assert claimed_pair is not None
    claimed_item, claimed_batch = claimed_pair[1], claimed_pair[0]
    assert claimed_batch["ingestionMode"] == "data-and-wording"

    generator = FakeGenerator()
    generator.responses.append(VariantCandidate.model_validate(GENERATED_CANDIDATE))
    validation_calls: list[dict[str, Any]] = []

    async def fake_generate_and_validate(**kwargs: Any) -> VariantGenerationResult:
        validation_calls.append(kwargs)
        return passing_result(GENERATED_CANDIDATE)

    monkeypatch.setattr(variation_worker_module, "generate_and_validate", fake_generate_and_validate)

    await process_variation(
        claimed_item, claimed_batch, database, generator, [], None, make_settings(), now=NOW
    )

    # Generation and validation both receive the canonical mode.
    assert generator.calls[0]["mode"] == "transfer-variant"
    assert validation_calls[0]["mode"] == "transfer-variant"
    item = await _load_item(database, batch["_id"], item_id)
    assert item["variation"]["status"] == VariationStatus.READY.value


async def test_generation_checkpoint_then_validation_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=datetime.now(UTC)
    )
    assert claimed is not None
    token = claimed["variation"]["claimToken"]

    generator = FakeGenerator()
    generator.responses.append(VariantCandidate.model_validate(GENERATED_CANDIDATE))

    validation_calls: list[dict[str, Any]] = []

    async def fake_generate_and_validate(**kwargs: Any) -> VariantGenerationResult:
        validation_calls.append(kwargs)
        return passing_result(GENERATED_CANDIDATE)

    monkeypatch.setattr(variation_worker_module, "generate_and_validate", fake_generate_and_validate)

    await process_variation(
        claimed, batch, database, generator, [], None, make_settings(), now=NOW
    )

    # The generator ran exactly once; validation received the persisted candidate.
    assert len(generator.calls) == 1
    assert len(validation_calls) == 1
    assert validation_calls[0]["candidate"].text == GENERATED_CANDIDATE["text"]
    assert validation_calls[0]["mode"] == "data-only"

    item = await _load_item(database, batch["_id"], item_id)
    variation = item["variation"]
    assert variation["status"] == VariationStatus.READY.value
    assert variation["claimToken"] is None
    assert variation["validatedRevision"] == 1
    assert variation["validation"]["verdict"] == "pass"
    assert variation["candidate"]["text"] == GENERATED_CANDIDATE["text"]
    # generationCount is only bumped by generate, never by the worker.
    assert variation["generationCount"] == 1


async def test_generation_provider_failure_lands_failed_with_evidence() -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=datetime.now(UTC)
    )

    generator = FakeGenerator()
    generator.responses.append(
        BaseVLMError("provider down", code="vlm-provider-error", retryable=True)
    )
    await process_variation(
        claimed, batch, database, generator, [], None, make_settings(), now=NOW
    )

    item = await _load_item(database, batch["_id"], item_id)
    variation = item["variation"]
    assert variation["status"] == VariationStatus.FAILED.value
    assert variation["candidate"] is None
    assert variation["validation"]["verdict"] == "fail"
    failure = variation["validation"]["failures"][0]
    assert failure["kind"] == "vlm-provider-error"
    assert "gen-model" in failure["evidence"]
    # Manual Generate Again can requeue this failed attempt.
    assert variation["generationCount"] == 1


async def test_resume_with_persisted_candidate_skips_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=datetime.now(UTC)
    )
    token = claimed["variation"]["claimToken"]
    # Simulate the crash-after-checkpoint recovery path: candidate persisted,
    # attempt in validating with an expired lease.
    assert await save_variation_candidate_checkpoint(
        database, batch["_id"], "user-1", item_id,
        token=token, claimed_revision=1,
        candidate=GENERATED_CANDIDATE, now=NOW,
    )
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch["_id"], "items.itemId": item_id},
        {"$set": {"items.$.variation.leaseUntil": NOW - timedelta(seconds=1)}},
    )

    # Reclaim resumes validation from the persisted candidate.
    reclaimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    )
    assert reclaimed is not None
    assert reclaimed["variation"]["status"] == VariationStatus.VALIDATING.value
    assert reclaimed["variation"]["claimToken"] != token
    assert reclaimed["variation"]["candidate"] == GENERATED_CANDIDATE

    generator = FakeGenerator()

    async def fake_generate_and_validate(**kwargs: Any) -> VariantGenerationResult:
        return passing_result(GENERATED_CANDIDATE)

    monkeypatch.setattr(variation_worker_module, "generate_and_validate", fake_generate_and_validate)

    await process_variation(
        reclaimed, batch, database, generator, [], None, make_settings(), now=NOW
    )
    # Recovery must not regenerate: no generator calls.
    assert generator.calls == []
    item = await _load_item(database, batch["_id"], item_id)
    assert item["variation"]["status"] == VariationStatus.READY.value


async def test_stale_result_rejected_after_semantic_edit() -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    )
    token = claimed["variation"]["claimToken"]

    # The user semantically edits the confirmed source while the worker runs.
    await update_item_draft_variant(
        database, batch["_id"], "user-1", item_id,
        draft_update={"text": "A totally different problem?"},
        expected_revision=1, now=NOW,
    )
    item = await _load_item(database, batch["_id"], item_id)
    assert item["variation"]["status"] == VariationStatus.NOT_REQUESTED.value
    assert item["contentRevision"] == 2

    # The stale worker result can no longer land.
    saved = await save_variation_result(
        database, batch["_id"], "user-1", item_id,
        token=token, claimed_revision=1, verdict="pass",
        validation={"verdict": "pass", "failures": [], "reports": []},
        candidate_present=True,
        now=NOW,
    )
    assert saved is False
    item = await _load_item(database, batch["_id"], item_id)
    assert item["variation"]["status"] == VariationStatus.NOT_REQUESTED.value

    # And the cancelled claim can never be reclaimed into work.
    assert await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    ) is None


async def test_delete_cancels_in_flight_and_undo_requeues() -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    )
    token = claimed["variation"]["claimToken"]

    assert await mark_item_deleted(database, batch["_id"], "user-1", item_id, now=NOW) is True
    # The worker result after deletion cannot resurrect the item's attempt.
    assert await save_variation_result(
        database, batch["_id"], "user-1", item_id,
        token=token, claimed_revision=1, verdict="pass",
        validation={"verdict": "pass", "failures": [], "reports": []},
        candidate_present=True,
        now=NOW,
    ) is False

    assert await undo_item_deletion(database, batch["_id"], "user-1", item_id, now=NOW) is True
    item = await _load_item(database, batch["_id"], item_id)
    variation = item["variation"]
    assert item["status"] == "ready"
    assert variation["status"] == VariationStatus.QUEUED.value
    # The undo never revives the old claim.
    assert variation["claimToken"] is None
    assert variation["original"] == SOURCE_SNAPSHOT


async def test_lease_renewal_requires_ownership() -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    )
    token = claimed["variation"]["claimToken"]

    assert await renew_variation_lease(
        database, batch["_id"], "user-1", item_id,
        token=token, lease_timeout_seconds=300, now=NOW,
    ) is True
    assert await renew_variation_lease(
        database, batch["_id"], "user-1", item_id,
        token="wrong-token", lease_timeout_seconds=300, now=NOW,
    ) is False


async def test_expired_lease_rejects_renewal_checkpoint_and_result() -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    )
    token = claimed["variation"]["claimToken"]
    # The lease expired without any reclaim: the old owner is no longer the
    # owner even though its token is still the current one.
    expired = NOW + timedelta(seconds=301)

    assert await renew_variation_lease(
        database, batch["_id"], "user-1", item_id,
        token=token, lease_timeout_seconds=300, now=expired,
    ) is False
    assert await save_variation_candidate_checkpoint(
        database, batch["_id"], "user-1", item_id,
        token=token, claimed_revision=1,
        candidate=GENERATED_CANDIDATE, now=expired,
    ) is False
    assert await save_variation_result(
        database, batch["_id"], "user-1", item_id,
        token=token, claimed_revision=1, verdict="pass",
        validation={"verdict": "pass", "failures": [], "reports": []},
        candidate_present=True,
        now=expired,
    ) is False

    # None of the fenced writes changed the claimed lifecycle state.
    item = await _load_item(database, batch["_id"], item_id)
    variation = item["variation"]
    assert variation["status"] == VariationStatus.GENERATING.value
    assert variation["claimToken"] == token
    assert variation["candidate"] is None
    assert variation["leaseUntil"] == NOW + timedelta(seconds=300)


async def test_expired_batch_rejects_claims_and_results() -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    # Expire the batch.
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch["_id"]},
        {"$set": {"expiresAt": NOW - timedelta(seconds=1)}},
    )
    assert await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    ) is None

    # A claim taken before expiry cannot land its result after expiry.
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch["_id"]}, {"$set": {"expiresAt": NOW + timedelta(hours=1)}}
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    )
    token = claimed["variation"]["claimToken"]
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch["_id"]},
        {"$set": {"expiresAt": NOW - timedelta(seconds=1)}},
    )
    assert await save_variation_result(
        database, batch["_id"], "user-1", item_id,
        token=token, claimed_revision=1, verdict="pass",
        validation={"verdict": "pass", "failures": [], "reports": []},
        candidate_present=True,
        now=NOW,
    ) is False


async def test_old_candidate_checkpoint_cannot_overwrite_regeneration() -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    first_claim = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    )
    first_token = first_claim["variation"]["claimToken"]

    # The user regenerates (Generate Again from an in-flight attempt is
    # rejected; simulate a failed first attempt then a new generation).
    await save_variation_result(
        database, batch["_id"], "user-1", item_id,
        token=first_token, claimed_revision=1, verdict="fail",
        validation={"verdict": "fail", "failures": [], "reports": []},
        candidate_present=True,
        now=NOW,
    )
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=1, now=NOW,
    )
    second_claim = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    )

    # The old claim's candidate checkpoint can never land.
    assert await save_variation_candidate_checkpoint(
        database, batch["_id"], "user-1", item_id,
        token=first_token, claimed_revision=1,
        candidate={"text": "stale"},
        now=NOW,
    ) is False
    assert second_claim["variation"]["candidate"] is None
    assert second_claim["variation"]["status"] == VariationStatus.GENERATING.value


async def test_worker_loop_stops_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    database = FakeDatabase()
    await seed_variant_batch(database)
    stop_event = asyncio.Event()
    stop_event.set()
    # Returns instead of polling forever when the stop event is already set.
    await asyncio.wait_for(
        run_variation_worker(database, make_settings(), FakeGenerator(), [], None, stop_event),
        timeout=5,
    )


async def test_orchestration_provider_failure_lands_needs_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A BaseVLMError from an orchestration step leaves the checkpointed
    candidate in needs-validation: an execution-only failure set produced no
    verdict, so Revalidate applies without regenerating (#671)."""
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=datetime.now(UTC)
    )
    generator = FakeGenerator()
    generator.responses.append(VariantCandidate.model_validate(GENERATED_CANDIDATE))

    async def failing_generate_and_validate(**kwargs: Any) -> VariantGenerationResult:
        raise BaseVLMError("validator down", code="vlm-provider-error", retryable=True)

    monkeypatch.setattr(
        variation_worker_module, "generate_and_validate", failing_generate_and_validate
    )

    # Previously this raised UnboundLocalError after the failure write.
    await process_variation(
        claimed, batch, database, generator, [], None, make_settings(), now=NOW
    )

    item = await _load_item(database, batch["_id"], item_id)
    variation = item["variation"]
    assert variation["status"] == VariationStatus.NEEDS_VALIDATION.value
    assert variation["candidate"]["text"] == GENERATED_CANDIDATE["text"]
    failure = variation["validation"]["failures"][0]
    assert failure["kind"] == "vlm-provider-error"
    assert "variation validation" in failure["evidence"]


async def test_all_validators_invalid_response_lands_needs_validation() -> None:
    """BLOCKER regression (#671): a single-validator deployment whose only
    validator returns unparseable JSON lands needs-validation with an
    execution-only failure set — no synthesized content failures — and the
    candidate is preserved for Revalidate."""
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=datetime.now(UTC)
    )
    generator = FakeGenerator()
    generator.responses.append(VariantCandidate.model_validate(GENERATED_CANDIDATE))
    validator = FakeValidator(
        error=BaseVLMError(
            "VLM provider response content was not valid JSON",
            code="vlm-invalid-response",
            retryable=True,
        )
    )

    # The real generate_and_validate runs (no monkeypatch).
    await process_variation(
        claimed, batch, database, generator, [validator], FakeHelper(),
        make_settings(), now=NOW,
    )

    item = await _load_item(database, batch["_id"], item_id)
    variation = item["variation"]
    assert variation["status"] == VariationStatus.NEEDS_VALIDATION.value
    assert variation["candidate"]["text"] == GENERATED_CANDIDATE["text"]
    failures = variation["validation"]["failures"]
    assert len(failures) == 1
    assert failures[0]["kind"] == "invalid-response"
    assert "not valid JSON" in failures[0]["evidence"]
    assert validator.calls, "the validator must have been invoked"


async def test_helper_crash_lands_needs_validation_with_honest_evidence() -> None:
    """Helper crash with a clean judge report: needs-validation, the stored
    report shows absent comparisons and no fabricated 'uncertain' verdicts
    (#671)."""
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=datetime.now(UTC)
    )
    generator = FakeGenerator()
    generator.responses.append(VariantCandidate.model_validate(GENERATED_CANDIDATE))
    validator = FakeValidator(report=passing_validator_report())
    helper = FakeHelper(
        error=BaseVLMError("helper provider down", code="vlm-provider-error", retryable=True)
    )

    await process_variation(
        claimed, batch, database, generator, [validator], helper,
        make_settings(), now=NOW,
    )

    item = await _load_item(database, batch["_id"], item_id)
    variation = item["variation"]
    assert variation["status"] == VariationStatus.NEEDS_VALIDATION.value
    failures = variation["validation"]["failures"]
    assert [f["kind"] for f in failures] == ["provider"]
    assert "helper" in failures[0]["evidence"]
    assert not any("uncertain" in f["evidence"] for f in failures)
    report = variation["validation"]["reports"][0]
    assert report["answerComparisonOriginal"] is None
    assert report["answerComparisonVariant"] is None


async def test_mixed_judgment_and_execution_failures_stay_failed() -> None:
    """A genuine check verdict mixed with an execution failure keeps the
    item failed with its Generate Again exit (#671 out of scope)."""
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=datetime.now(UTC)
    )
    generator = FakeGenerator()
    generator.responses.append(VariantCandidate.model_validate(GENERATED_CANDIDATE))
    judged_checks = {
        name: Check(
            category="materially-easier" if name == "difficultyShift" else category,
            evidence="too easy" if name == "difficultyShift" else "clear",
        )
        for name, category in PASSING_CATEGORIES.items()
    }
    judged = passing_validator_report().model_copy(update={"checks": judged_checks})
    validator = FakeValidator(report=judged)
    helper = FakeHelper(
        error=BaseVLMError("helper provider down", code="vlm-provider-error", retryable=True)
    )

    await process_variation(
        claimed, batch, database, generator, [validator], helper,
        make_settings(), now=NOW,
    )

    item = await _load_item(database, batch["_id"], item_id)
    variation = item["variation"]
    assert variation["status"] == VariationStatus.FAILED.value
    kinds = {f["kind"] for f in variation["validation"]["failures"]}
    assert kinds == {"check", "provider"}


async def test_malformed_persisted_candidate_fails_with_evidence() -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=datetime.now(UTC)
    )
    token = claimed["variation"]["claimToken"]
    # A candidate edited (or persisted) into a schema-invalid shape.
    malformed = dict(GENERATED_CANDIDATE, text=None)
    assert await save_variation_candidate_checkpoint(
        database, batch["_id"], "user-1", item_id,
        token=token, claimed_revision=1, candidate=malformed, now=NOW,
    )
    await database[INGESTION_BATCHES_COLLECTION].update_one(
        {"_id": batch["_id"], "items.itemId": item_id},
        {"$set": {"items.$.variation.leaseUntil": NOW - timedelta(seconds=1)}},
    )
    reclaimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=300, now=NOW
    )
    assert reclaimed is not None

    generator = FakeGenerator()
    await process_variation(
        reclaimed, batch, database, generator, [], None, make_settings(), now=NOW
    )

    # The task must not crash and leave the item in-flight forever.
    assert generator.calls == []
    item = await _load_item(database, batch["_id"], item_id)
    variation = item["variation"]
    assert variation["status"] == VariationStatus.FAILED.value
    failure = variation["validation"]["failures"][0]
    assert failure["kind"] == "invalid-candidate"
    assert "text" in failure["evidence"]


class SlowGenerator(FakeGenerator):
    def __init__(self, seconds: float) -> None:
        super().__init__()
        self._seconds = seconds

    async def generate_candidate(self, *, mode: str, source: Any) -> VariantCandidate:
        await asyncio.sleep(self._seconds)
        return VariantCandidate.model_validate(GENERATED_CANDIDATE)


async def test_heartbeat_maintains_lease_across_slow_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    # Wall-clock claim: the heartbeat and reclaim below use the real clock,
    # so the lease must start at execution time, not module-import time.
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id,
        lease_timeout_seconds=2, now=datetime.now(UTC),
    )
    assert claimed is not None

    async def fake_generate_and_validate(**kwargs: Any) -> VariantGenerationResult:
        return passing_result(GENERATED_CANDIDATE)

    monkeypatch.setattr(
        variation_worker_module, "generate_and_validate", fake_generate_and_validate
    )

    task = asyncio.create_task(
        process_variation(
            claimed, batch, database, SlowGenerator(2.5), [], None,
            make_settings(variation_lease_timeout_seconds=2), now=NOW,
        )
    )
    # Cross the original lease boundary (2s) while the provider call runs:
    # the heartbeat renewed at ~1s (strictly inside the lease), so a
    # reclaiming worker is refused.
    await asyncio.sleep(2.2)
    assert await claim_variation_work(
        database, batch["_id"], "user-1", item_id,
        lease_timeout_seconds=1, now=datetime.now(UTC),
    ) is None
    await asyncio.wait_for(task, timeout=10)

    item = await _load_item(database, batch["_id"], item_id)
    assert item["variation"]["status"] == VariationStatus.READY.value


async def test_heartbeat_loss_discards_stale_call(monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase()
    batch, items = await seed_variant_batch(database)
    item_id = items[0]["itemId"]
    await request_variation_generation(
        database, batch["_id"], "user-1", item_id,
        original=SOURCE_SNAPSHOT, expected_revision=0, now=NOW,
    )
    claimed = await claim_variation_work(
        database, batch["_id"], "user-1", item_id, lease_timeout_seconds=1, now=NOW
    )
    assert claimed is not None
    token = claimed["variation"]["claimToken"]

    async def losing_renew(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(variation_worker_module, "renew_variation_lease", losing_renew)

    await asyncio.wait_for(
        process_variation(
            claimed, batch, database, SlowGenerator(1.5), [], None,
            make_settings(variation_lease_timeout_seconds=1), now=NOW,
        ),
        timeout=10,
    )

    # The first heartbeat tick lost the claim: the stale result is discarded
    # and the item stays in-flight for the reclaiming worker.
    item = await _load_item(database, batch["_id"], item_id)
    variation = item["variation"]
    assert variation["status"] == VariationStatus.GENERATING.value
    assert variation["candidate"] is None
    assert variation["claimToken"] == token
