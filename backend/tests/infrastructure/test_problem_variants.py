"""Unit tests for problem-variant session persistence (issue #685).

FakeDatabase tier: the atomic predicate groups' semantics — generate
override clearing, checkpoint/result fencing, #665 candidate-from-nothing,
status-independent tag writes, attest eligibility, submit recording and
discard. Index/concurrency behavior lives in the real-Mongo integration
suite (tests/integration/test_problem_variants.py); the fake's
create_index is a no-op.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from bson import ObjectId

from app.infrastructure.problem_variants.repository import (
    PROBLEM_VARIANT_SESSIONS_COLLECTION,
    attest_problem_variant_validation,
    discard_problem_variant_session,
    edit_problem_variant_candidate,
    find_active_problem_variant_session,
    mark_problem_variant_session_submitted,
    request_problem_variant_generation,
    request_problem_variant_revalidation,
    save_problem_variant_checkpoint,
    save_problem_variant_result,
    update_problem_variant_session_tags,
)
from app.presentation.errors import ApiError
from app.problem_variation import (
    InvalidVariationStateError,
    RevisionMismatchError,
    VariationNotFoundError,
    VariationStatus,
)
from tests.test_utils.db_fakes import FakeDatabase

NOW = datetime.now(UTC)


def make_session(
    *,
    status: str = VariationStatus.QUEUED.value,
    content_revision: int = 0,
    generation_count: int = 1,
    candidate: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    validated_revision: int | None = None,
    attestation: dict[str, Any] | None = None,
    submit: dict[str, Any] | None = None,
    discarded_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "_id": ObjectId(),
        "problemId": str(ObjectId()),
        "userId": "user-1",
        "mode": "transfer-variant",
        "contentRevision": content_revision,
        "tags": ["algebra"],
        "variation": {
            "status": status,
            "generationCount": generation_count,
            "original": {
                "text": "What is 2+2?",
                "problemType": "short-answer",
                "graphDsl": None,
                "correctAnswer": "4",
                "subject": "math",
            },
            "candidate": candidate,
            "validation": validation,
            "validatedRevision": validated_revision,
            "attestation": attestation,
            "queuedAt": NOW,
        },
        "submit": submit,
        "discardedAt": discarded_at,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def seed_session(database: FakeDatabase, session: dict[str, Any]) -> ObjectId:
    database.seed(PROBLEM_VARIANT_SESSIONS_COLLECTION, [session])
    return session["_id"]


def get_session(database: FakeDatabase, session_id: ObjectId) -> dict[str, Any]:
    return next(
        document
        for document in database[PROBLEM_VARIANT_SESSIONS_COLLECTION]._documents
        if document["_id"] == session_id
    )


PROBLEM_ID = "111111111111111111111111"


async def test_generate_override_clears_stale_fields_and_bumps_revision() -> None:
    database = FakeDatabase()
    session = make_session(
        status=VariationStatus.VALIDATING.value,
        content_revision=3,
        generation_count=2,
        candidate={"text": "old candidate", "correctAnswer": "9"},
        validation={"verdict": "fail", "failures": [], "reports": []},
        validated_revision=None,
        attestation={"revision": 2, "at": NOW},
    )
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)

    queued = await request_problem_variant_generation(
        database, "user-1", PROBLEM_ID, session_id,
        expected_revision=3, now=NOW,
    )

    assert queued is True
    stored = get_session(database, session_id)
    assert stored["contentRevision"] == 4
    assert stored["variation"]["status"] == VariationStatus.QUEUED.value
    assert stored["variation"]["generationCount"] == 3
    assert stored["variation"]["candidate"] is None
    assert stored["variation"]["validation"] is None
    assert stored["variation"]["validatedRevision"] is None
    assert stored["variation"]["attestation"] is None


async def test_generate_override_rejects_stale_revision() -> None:
    database = FakeDatabase()
    session = make_session(status=VariationStatus.READY.value, content_revision=5)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)

    queued = await request_problem_variant_generation(
        database, "user-1", PROBLEM_ID, session_id,
        expected_revision=4, now=NOW,
    )

    assert queued is False
    stored = get_session(database, session_id)
    assert stored["variation"]["status"] == VariationStatus.READY.value
    assert stored["contentRevision"] == 5


async def test_generate_override_rejected_after_submit() -> None:
    database = FakeDatabase()
    session = make_session(
        status=VariationStatus.READY.value,
        content_revision=1,
        submit={"submittedProblemId": str(ObjectId()), "success": True},
    )
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)

    queued = await request_problem_variant_generation(
        database, "user-1", PROBLEM_ID, session_id,
        expected_revision=1, now=NOW,
    )

    assert queued is False


async def test_checkpoint_is_fenced_on_revision_and_liveness() -> None:
    database = FakeDatabase()
    session = make_session(status=VariationStatus.GENERATING.value, content_revision=7)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    candidate = {"text": "c", "problemType": "short-answer", "graphDsl": None,
                 "correctAnswer": "8", "subject": "math",
                 "generator": {"provider": "p", "model": "m"}}

    assert await save_problem_variant_checkpoint(
        database, "user-1", PROBLEM_ID, session_id,
        claimed_revision=7, candidate=candidate, now=NOW,
    ) is True
    stored = get_session(database, session_id)
    assert stored["variation"]["status"] == VariationStatus.VALIDATING.value
    assert stored["variation"]["candidate"]["correctAnswer"] == "8"

    # A superseded attempt (older revision) is dropped.
    assert await save_problem_variant_checkpoint(
        database, "user-1", PROBLEM_ID, session_id,
        claimed_revision=6, candidate=candidate, now=NOW,
    ) is False

    # A submitted session can never receive a checkpoint.
    submitted = make_session(
        status=VariationStatus.GENERATING.value, content_revision=1,
        submit={"submittedProblemId": str(ObjectId()), "success": True},
    )
    submitted["problemId"] = PROBLEM_ID
    submitted_id = seed_session(database, submitted)
    assert await save_problem_variant_checkpoint(
        database, "user-1", PROBLEM_ID, submitted_id,
        claimed_revision=1, candidate=candidate, now=NOW,
    ) is False


def _fail_validation(kind: str) -> dict[str, Any]:
    return {
        "verdict": "fail",
        "failures": [{"kind": kind, "evidence": "boom"}],
        "reports": [],
    }


async def test_result_status_mapping_matches_batch_semantics() -> None:
    database = FakeDatabase()

    # Pass lands ready with validatedRevision.
    session = make_session(status=VariationStatus.VALIDATING.value, content_revision=2)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    assert await save_problem_variant_result(
        database, "user-1", PROBLEM_ID, session_id,
        claimed_revision=2, verdict="pass",
        validation={"verdict": "pass", "failures": [], "reports": []},
        candidate_present=True, now=NOW,
    ) is True
    stored = get_session(database, session_id)
    assert stored["variation"]["status"] == VariationStatus.READY.value
    assert stored["variation"]["validatedRevision"] == 2

    # Execution-only fail with a stored candidate lands needs-validation.
    database = FakeDatabase()
    session = make_session(status=VariationStatus.VALIDATING.value, content_revision=2)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    assert await save_problem_variant_result(
        database, "user-1", PROBLEM_ID, session_id,
        claimed_revision=2, verdict="fail",
        validation=_fail_validation("provider"),
        candidate_present=True, now=NOW,
    ) is True
    assert (
        get_session(database, session_id)["variation"]["status"]
        == VariationStatus.NEEDS_VALIDATION.value
    )

    # Content-kind fail lands failed.
    database = FakeDatabase()
    session = make_session(status=VariationStatus.VALIDATING.value, content_revision=2)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    await save_problem_variant_result(
        database, "user-1", PROBLEM_ID, session_id,
        claimed_revision=2, verdict="fail",
        validation=_fail_validation("content"),
        candidate_present=True, now=NOW,
    )
    assert (
        get_session(database, session_id)["variation"]["status"]
        == VariationStatus.FAILED.value
    )

    # A stale result can never land.
    assert await save_problem_variant_result(
        database, "user-1", PROBLEM_ID, session_id,
        claimed_revision=1, verdict="pass",
        validation={"verdict": "pass", "failures": [], "reports": []},
        candidate_present=True, now=NOW,
    ) is False


async def test_edit_candidate_semantic_change_and_665_candidate_from_nothing() -> None:
    database = FakeDatabase()
    session = make_session(status=VariationStatus.READY.value, content_revision=1)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)

    await edit_problem_variant_candidate(
        database, "user-1", PROBLEM_ID, session_id,
        candidate_update={"correctAnswer": "8", "text": "What is 3+5?"},
        tags=["geometry"],
        expected_revision=1, now=NOW,
    )
    stored = get_session(database, session_id)
    assert stored["contentRevision"] == 2
    assert stored["variation"]["status"] == VariationStatus.NEEDS_VALIDATION.value
    assert stored["variation"]["validatedRevision"] is None
    assert stored["variation"]["candidate"]["correctAnswer"] == "8"
    assert stored["tags"] == ["geometry"]

    # Revision mismatch is the conflict family the route maps to 409.
    with pytest.raises(RevisionMismatchError):
        await edit_problem_variant_candidate(
            database, "user-1", PROBLEM_ID, session_id,
            candidate_update={"correctAnswer": "9"}, tags=None,
            expected_revision=1, now=NOW,
        )

    # A failed session with no candidate creates it from nothing (#665).
    database = FakeDatabase()
    session = make_session(status=VariationStatus.FAILED.value, content_revision=1)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    await edit_problem_variant_candidate(
        database, "user-1", PROBLEM_ID, session_id,
        candidate_update={"text": "hand fix", "problemType": "short-answer",
                          "graphDsl": None, "correctAnswer": "42"},
        tags=None,
        expected_revision=1, now=NOW,
    )
    stored = get_session(database, session_id)
    assert stored["variation"]["candidate"]["correctAnswer"] == "42"
    assert stored["variation"]["status"] == VariationStatus.NEEDS_VALIDATION.value
    # Codex P2: the hand-built candidate must carry the immutable fields the
    # PATCH schema doesn't accept, or the next revalidate fails
    # VariantCandidate.model_validate and drops back to failed.
    assert stored["variation"]["candidate"]["subject"] == "math"
    assert stored["variation"]["candidate"]["generator"] == {
        "provider": "user",
        "model": "manual-edit",
    }
    VariantCandidate.model_validate(stored["variation"]["candidate"])

    # Editing is rejected in in-flight states.
    database = FakeDatabase()
    session = make_session(status=VariationStatus.GENERATING.value, content_revision=1)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    with pytest.raises(InvalidVariationStateError):
        await edit_problem_variant_candidate(
            database, "user-1", PROBLEM_ID, session_id,
            candidate_update={"correctAnswer": "9"}, tags=None,
            expected_revision=1, now=NOW,
        )


async def test_tag_write_is_status_independent_and_never_bumps_revision() -> None:
    database = FakeDatabase()
    for status in (
        VariationStatus.QUEUED.value,
        VariationStatus.GENERATING.value,
        VariationStatus.VALIDATING.value,
        VariationStatus.READY.value,
    ):
        session = make_session(status=status, content_revision=4)
        session["problemId"] = PROBLEM_ID
        session_id = seed_session(database, session)
        assert await update_problem_variant_session_tags(
            database, "user-1", PROBLEM_ID, session_id,
            tags=["fresh"], now=NOW,
        ) is True
        stored = get_session(database, session_id)
        assert stored["tags"] == ["fresh"]
        assert stored["contentRevision"] == 4
        assert stored["variation"]["status"] == status

    # Terminal sessions accept nothing.
    submitted = make_session(
        submit={"submittedProblemId": str(ObjectId()), "success": True}
    )
    submitted["problemId"] = PROBLEM_ID
    submitted_id = seed_session(database, submitted)
    assert await update_problem_variant_session_tags(
        database, "user-1", PROBLEM_ID, submitted_id,
        tags=["late"], now=NOW,
    ) is False
    discarded = make_session(discarded_at=NOW)
    discarded["problemId"] = PROBLEM_ID
    discarded_id = seed_session(database, discarded)
    assert await update_problem_variant_session_tags(
        database, "user-1", PROBLEM_ID, discarded_id,
        tags=["late"], now=NOW,
    ) is False


async def test_attest_eligibility_branches() -> None:
    # Stale PASS on needs-validation: keep validation.
    database = FakeDatabase()
    session = make_session(
        status=VariationStatus.NEEDS_VALIDATION.value, content_revision=2,
        validation={"verdict": "pass", "failures": [], "reports": []},
    )
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    assert await attest_problem_variant_validation(
        database, "user-1", PROBLEM_ID, session_id,
        expected_revision=2, now=NOW,
    ) is True
    stored = get_session(database, session_id)
    assert stored["variation"]["status"] == VariationStatus.READY.value
    assert stored["variation"]["attestation"]["revision"] == 2
    # validatedRevision stays None: user-attested admissions are distinct.
    assert stored["variation"]["validatedRevision"] is None

    # FAIL with only answer-kind failures: override allowed from failed.
    database = FakeDatabase()
    session = make_session(
        status=VariationStatus.FAILED.value, content_revision=3,
        validation=_fail_validation("answer"),
    )
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    assert await attest_problem_variant_validation(
        database, "user-1", PROBLEM_ID, session_id,
        expected_revision=3, now=NOW,
    ) is True

    # Content-kind failures are never overridable.
    database = FakeDatabase()
    session = make_session(
        status=VariationStatus.FAILED.value, content_revision=3,
        validation=_fail_validation("content"),
    )
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    assert await attest_problem_variant_validation(
        database, "user-1", PROBLEM_ID, session_id,
        expected_revision=3, now=NOW,
    ) is False
    assert (
        get_session(database, session_id)["variation"]["status"]
        == VariationStatus.FAILED.value
    )

    # Stale revision cannot attest.
    assert await attest_problem_variant_validation(
        database, "user-1", PROBLEM_ID, session_id,
        expected_revision=99, now=NOW,
    ) is False


async def test_revalidation_requires_needs_validation_and_keeps_candidate() -> None:
    database = FakeDatabase()
    session = make_session(
        status=VariationStatus.NEEDS_VALIDATION.value, content_revision=2,
        candidate={"text": "kept", "correctAnswer": "8"},
        validation=_fail_validation("provider"),
    )
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)

    assert await request_problem_variant_revalidation(
        database, "user-1", PROBLEM_ID, session_id,
        expected_revision=2, now=NOW,
    ) is True
    stored = get_session(database, session_id)
    assert stored["variation"]["status"] == VariationStatus.QUEUED.value
    assert stored["variation"]["candidate"]["text"] == "kept"
    assert stored["variation"]["validation"] is None
    assert stored["contentRevision"] == 2

    # Ready sessions cannot revalidate (Generate is the exit there).
    database = FakeDatabase()
    session = make_session(status=VariationStatus.READY.value, content_revision=1)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    assert await request_problem_variant_revalidation(
        database, "user-1", PROBLEM_ID, session_id,
        expected_revision=1, now=NOW,
    ) is False


async def test_submit_recording_and_discard_liveness() -> None:
    database = FakeDatabase()
    session = make_session(status=VariationStatus.READY.value, content_revision=1)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    admitted = ObjectId()

    assert await mark_problem_variant_session_submitted(
        database, "user-1", PROBLEM_ID, session_id,
        admitted_problem_id=admitted, now=NOW,
    ) is True
    stored = get_session(database, session_id)
    assert stored["submit"]["submittedProblemId"] == str(admitted)
    assert stored["submit"]["success"] is True

    # Double submit records nothing new (the route turns this into 409).
    other = ObjectId()
    assert await mark_problem_variant_session_submitted(
        database, "user-1", PROBLEM_ID, session_id,
        admitted_problem_id=other, now=NOW,
    ) is False
    assert get_session(database, session_id)["submit"]["submittedProblemId"] == str(admitted)

    # A submitted session cannot be discarded afterwards.
    assert await discard_problem_variant_session(
        database, "user-1", PROBLEM_ID, session_id, now=NOW,
    ) is False

    # A live session discards and disappears from the active lookup.
    database = FakeDatabase()
    session = make_session()
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)
    assert await find_active_problem_variant_session(
        database, "user-1", PROBLEM_ID
    ) is not None
    assert await discard_problem_variant_session(
        database, "user-1", PROBLEM_ID, session_id, now=NOW,
    ) is True
    assert await find_active_problem_variant_session(
        database, "user-1", PROBLEM_ID
    ) is None


async def test_unknown_session_conflicts() -> None:
    database = FakeDatabase()
    missing_id = ObjectId()
    queued = await request_problem_variant_generation(
        database, "user-1", PROBLEM_ID, missing_id,
        expected_revision=0, now=NOW,
    )
    assert queued is False
    from app.infrastructure.problem_variants.repository import (
        classify_session_conflict,
    )

    with pytest.raises(VariationNotFoundError):
        classify_session_conflict(None, 0)


# ---------------------------------------------------------------------------
# In-process executor (issue #685 Q7): two-step checkpoint flow with fencing.
# ---------------------------------------------------------------------------

from app.infrastructure.problem_variants import executor as variant_executor  # noqa: E402
from app.infrastructure.problem_variants.executor import (  # noqa: E402
    start_problem_variant_generation,
)
from app.domain.ingestion.variation import (  # noqa: E402
    VariantAssessment,
    VariantCandidate,
    VariantGenerationResult,
)


class FakeGenerator:
    identity = {"provider": "fake", "model": "gen-model"}

    def __init__(self, candidate_text: str = "What is 3+5?") -> None:
        self.candidate_text = candidate_text

    async def generate_candidate(self, *, mode: str, source: Any) -> VariantCandidate:
        return VariantCandidate(
            text=self.candidate_text,
            problem_type=source.problem_type,
            subject=source.subject,
            graph_dsl=source.graph_dsl,
            correct_answer="8",
            generator={"provider": "fake", "model": "gen-model"},
        )


def _pass_result() -> Any:
    return VariantGenerationResult(
        assessment=VariantAssessment(verdict="pass", failures=[]),
        reports=[],
    )


async def test_executor_runs_generate_checkpoint_validate_to_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase()
    session = make_session(status=VariationStatus.QUEUED.value, content_revision=1)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)

    generator = FakeGenerator()

    async def fake_generate_and_validate(**kwargs: Any) -> Any:
        return _pass_result()

    monkeypatch.setattr(
        variant_executor, "generate_and_validate", fake_generate_and_validate
    )
    await start_problem_variant_generation(
        database, settings=None,
        user_id="user-1", problem_id=PROBLEM_ID, session_id=session_id,
        clients=(generator, object(), None, object()),
    )
    await variant_executor._tasks[str(session_id)]

    stored = get_session(database, session_id)
    assert stored["variation"]["status"] == VariationStatus.READY.value
    assert stored["variation"]["validatedRevision"] == 1
    assert stored["variation"]["candidate"]["correctAnswer"] == "8"
    assert stored["variation"]["validation"]["verdict"] == "pass"
    variant_executor.cancel_problem_variant_task(session_id)


async def test_executor_revalidation_validates_stored_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revalidate queues validator-only work: the stored candidate (possibly
    user-edited) must be validated, never regenerated (Codex P1 finding)."""
    database = FakeDatabase()
    session = make_session(
        status=VariationStatus.QUEUED.value,
        content_revision=2,
        candidate={
            "text": "user edit",
            "problemType": "short-answer",
            "graphDsl": None,
            "correctAnswer": "11",
            "subject": "math",
            "generator": {"provider": "fake", "model": "gen-model"},
        },
    )
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)

    class NoGenerateGenerator(FakeGenerator):
        async def generate_candidate(self, *, mode: str, source: Any) -> VariantCandidate:
            raise AssertionError("revalidation must not regenerate")

    async def fake_generate_and_validate(**kwargs: Any) -> Any:
        assert kwargs["candidate"].correct_answer == "11"
        return _pass_result()

    monkeypatch.setattr(
        variant_executor, "generate_and_validate", fake_generate_and_validate
    )
    await start_problem_variant_generation(
        database, settings=None,
        user_id="user-1", problem_id=PROBLEM_ID, session_id=session_id,
        clients=(NoGenerateGenerator(), object(), None, object()),
    )
    await variant_executor._tasks[str(session_id)]

    stored = get_session(database, session_id)
    assert stored["variation"]["status"] == VariationStatus.READY.value
    assert stored["variation"]["candidate"]["correctAnswer"] == "11"
    assert stored["variation"]["validatedRevision"] == 2
    variant_executor.cancel_problem_variant_task(session_id)


async def test_executor_removes_completed_task_from_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2: completed attempts leave _tasks (no per-session leak)."""
    database = FakeDatabase()
    session = make_session(status=VariationStatus.QUEUED.value)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)

    async def fake_generate_and_validate(**kwargs: Any) -> Any:
        return _pass_result()

    monkeypatch.setattr(
        variant_executor, "generate_and_validate", fake_generate_and_validate
    )
    await start_problem_variant_generation(
        database, settings=None,
        user_id="user-1", problem_id=PROBLEM_ID, session_id=session_id,
        clients=(FakeGenerator(), object(), None, object()),
    )
    await variant_executor._tasks[str(session_id)]
    # Done callbacks run on the next loop tick.
    await asyncio.sleep(0)
    assert str(session_id) not in variant_executor._tasks


async def test_executor_drops_stale_candidate_after_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A superseded task's checkpoint can never land (revision fencing)."""
    database = FakeDatabase()
    session = make_session(status=VariationStatus.QUEUED.value, content_revision=1)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)

    started = asyncio.Event()

    class SlowGenerator(FakeGenerator):
        async def generate_candidate(self, *, mode: str, source: Any) -> VariantCandidate:
            started.set()
            await asyncio.sleep(0.05)
            return await super().generate_candidate(mode=mode, source=source)

    async def fail_if_called(**kwargs: Any) -> Any:
        raise AssertionError("validation must never run for a stale attempt")

    monkeypatch.setattr(variant_executor, "generate_and_validate", fail_if_called)
    await start_problem_variant_generation(
        database, settings=None,
        user_id="user-1", problem_id=PROBLEM_ID, session_id=session_id,
        clients=(SlowGenerator(), object(), None, object()),
    )
    task = variant_executor._tasks[str(session_id)]
    await started.wait()
    # Generate override lands while the old task is mid-generation.
    await request_problem_variant_generation(
        database, "user-1", PROBLEM_ID, session_id,
        expected_revision=1, now=NOW,
    )
    await task

    stored = get_session(database, session_id)
    # The override's fresh queued attempt is untouched by the stale task.
    assert stored["variation"]["status"] == VariationStatus.QUEUED.value
    assert stored["variation"]["candidate"] is None
    assert stored["contentRevision"] == 2
    variant_executor.cancel_problem_variant_task(session_id)


async def test_executor_lands_fenced_failure_on_generator_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase()
    session = make_session(status=VariationStatus.QUEUED.value, content_revision=1)
    session["problemId"] = PROBLEM_ID
    session_id = seed_session(database, session)

    class ExplodingGenerator(FakeGenerator):
        async def generate_candidate(self, *, mode: str, source: Any) -> VariantCandidate:
            raise RuntimeError("provider down")

    await start_problem_variant_generation(
        database, settings=None,
        user_id="user-1", problem_id=PROBLEM_ID, session_id=session_id,
        clients=(ExplodingGenerator(), object(), None, object()),
    )
    await variant_executor._tasks[str(session_id)]

    stored = get_session(database, session_id)
    assert stored["variation"]["status"] == VariationStatus.FAILED.value
    assert stored["variation"]["candidate"] is None
    assert stored["variation"]["validation"]["verdict"] == "fail"
    variant_executor.cancel_problem_variant_task(session_id)
