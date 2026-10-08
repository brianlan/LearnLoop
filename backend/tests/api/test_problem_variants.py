"""API tests for problem-variant sessions (issue #685).

FakeDatabase tier through the real router: create (profile pre-check, audit
three-case rule, legacy-mode rejection, double-create 409), tag-only PATCH
branch, generate override, fail-attest, transactional submit admission with
provenance round-trip, and discard. The in-process executor is stubbed;
its own semantics are covered in
tests/infrastructure/test_problem_variants.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.infrastructure.config.settings import Settings
from app.infrastructure.storage.mongo import get_mongo_adapter
from app.presentation.deps import (
    get_app_settings,
    get_current_user,
    get_database,
    get_mongo_adapter,
    get_s3_storage,
)
from app.problem_variation import VariationStatus
from tests.api.conftest import FakeDatabase
from tests.conftest import FakeStorage

PROBLEM_VARIANT_SESSIONS = "problem_variant_sessions"
NOW = datetime.now(UTC)


class FakeSession:
    async def with_transaction(self, callback: Any) -> Any:
        return await callback(self)


class FakeMongoAdapter:
    @asynccontextmanager
    async def start_session(self) -> AsyncIterator[FakeSession]:
        yield FakeSession()


def make_user() -> dict[str, Any]:
    return {
        "_id": ObjectId(),
        "username": "teacher1",
        "status": "active",
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def make_problem(
    user_id: ObjectId,
    *,
    with_source_image: bool = True,
    variation: dict[str, Any] | None = None,
    is_deleted: bool = False,
    is_disabled: bool = False,
) -> dict[str, Any]:
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": "What is 2+2?",
        "problemType": "short-answer",
        "subject": "math",
        "graphDsl": None,
        "correctAnswer": {
            "display": "4",
            "normalizedText": "4",
            "normalizedSet": [],
            "format": "single",
        },
        "tags": ["algebra"],
        "sourceImage": {
            "bucket": "learnloop-media",
            "objectKey": f"users/{user_id}/images/{ObjectId()}.png",
            "contentType": "image/png",
            "sizeBytes": 4,
            "sha256": "abc",
            "uploadedAt": NOW,
        }
        if with_source_image
        else None,
        "origin": {},
        "tracking": {
            "exposureCount": 0,
            "correctCount": 0,
            "failedCount": 0,
            "lastTestedAt": None,
            "lastAttemptCorrect": None,
        },
        "isDeleted": is_deleted,
        "deletedAt": NOW if is_deleted else None,
        "isDisabled": is_disabled,
        "createdAt": NOW,
        "updatedAt": NOW,
        **({"variation": variation} if variation is not None else {}),
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


def make_session_doc(
    problem_id: ObjectId,
    user_id: Any,
    *,
    status: str = VariationStatus.QUEUED.value,
    content_revision: int = 0,
    candidate: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    validated_revision: int | None = None,
    attestation: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    submit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "_id": ObjectId(),
        "problemId": str(problem_id),
        "userId": user_id,
        "mode": "transfer-variant",
        "contentRevision": content_revision,
        "tags": tags or [],
        "variation": {
            "status": status,
            "generationCount": 1,
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
        "discardedAt": None,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    patch_profiles: bool = True,
) -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    adapter = FakeMongoAdapter()
    storage = FakeStorage()
    user = make_user()

    application.state.fake_database = database
    application.state.fake_adapter = adapter
    application.state.fake_storage = storage
    application.state.primary_user = user
    application.state.generation_starts: list[str] = []

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_current_user] = lambda: deepcopy(user)
    application.dependency_overrides[get_app_settings] = lambda: Settings()
    application.dependency_overrides[get_mongo_adapter] = lambda: adapter
    application.dependency_overrides[get_s3_storage] = lambda: storage

    if patch_profiles:
        monkeypatch.setattr(
            "app.presentation.problem_variants.build_variant_clients",
            lambda settings: (object(), object(), object(), object()),
        )

    async def _fake_start(*args: Any, **kwargs: Any) -> None:
        application.state.generation_starts.append(str(kwargs.get("session_id")))

    monkeypatch.setattr(
        "app.presentation.problem_variants.start_problem_variant_generation",
        _fake_start,
    )
    return application


@pytest_asyncio.fixture
async def variants_app(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FastAPI]:
    yield _build_app(monkeypatch)


@pytest_asyncio.fixture
async def client(variants_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=variants_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


async def create_problem(
    app: FastAPI, **kwargs: Any
) -> dict[str, Any]:
    database: FakeDatabase = app.state.fake_database
    user_id = app.state.primary_user["_id"]
    problem = make_problem(user_id, **kwargs)
    await database["problems"].insert_one(problem)
    return problem


async def test_create_variant_starts_generation_and_freezes_source(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants",
        json={"mode": "transfer-variant"},
    )

    assert response.status_code == 202
    session = response.json()["session"]
    assert session["mode"] == "transfer-variant"
    assert session["contentRevision"] == 0
    assert session["variation"]["status"] == "queued"
    assert session["variation"]["generationCount"] == 1
    assert session["variation"]["original"]["correctAnswer"] == "4"
    assert variants_app.state.generation_starts == [session["sessionId"]]

    # The stored original carries the display string; admission normalizes.
    stored = variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS]._documents[0]
    assert stored["variation"]["original"]["text"] == "What is 2+2?"


async def test_create_variant_rejects_unavailable_problems(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    deleted = await create_problem(variants_app, is_deleted=True)
    response = await client.post(
        f"/api/v1/problems/{deleted['_id']}/variants",
        json={"mode": "transfer-variant"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROBLEM_UNAVAILABLE"

    disabled = await create_problem(variants_app, is_disabled=True)
    response = await client.post(
        f"/api/v1/problems/{disabled['_id']}/variants",
        json={"mode": "data-only"},
    )
    assert response.status_code == 409


async def test_create_variant_rejects_legacy_mode(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants",
        json={"mode": "data-and-wording"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_VARIANT_MODE"
    assert not variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS]._documents


async def test_create_variant_requires_audit_image(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    # No source image and no variant provenance: nothing to audit.
    problem = await create_problem(variants_app, with_source_image=False)
    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants",
        json={"mode": "transfer-variant"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VARIANT_AUDIT_MISSING"
    assert not variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS]._documents


async def test_create_variant_requires_configured_profiles() -> None:
    # No profile patch: the real pre-check must 409 BEFORE any session row.
    monkeypatch = pytest.MonkeyPatch()
    try:
        app = _build_app(monkeypatch, patch_profiles=False)
        problem = await create_problem(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                f"/api/v1/problems/{problem['_id']}/variants",
                json={"mode": "transfer-variant"},
            )
        assert response.status_code == 409
        assert not app.state.fake_database[PROBLEM_VARIANT_SESSIONS]._documents
    finally:
        monkeypatch.undo()


async def test_double_create_conflicts_with_existing_session(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    first = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants",
        json={"mode": "transfer-variant"},
    )
    assert first.status_code == 202
    second = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants",
        json={"mode": "data-only"},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "VARIANT_SESSION_EXISTS"


async def test_get_active_session(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    empty = await client.get(f"/api/v1/problems/{problem['_id']}/variants/active")
    assert empty.status_code == 200
    assert empty.json()["session"] is None

    await client.post(
        f"/api/v1/problems/{problem['_id']}/variants",
        json={"mode": "data-only"},
    )
    active = await client.get(f"/api/v1/problems/{problem['_id']}/variants/active")
    assert active.status_code == 200
    assert active.json()["session"]["mode"] == "data-only"


async def test_patch_candidate_tag_only_is_status_independent(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    session = make_session_doc(problem["_id"], variants_app.state.primary_user["_id"])
    # Tag-only saves ride through while generation is validating.
    session["variation"]["status"] = "validating"
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)
    await client.patch(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/candidate",
        json={"expectedRevision": 0, "tags": ["fractions", "algebra"]},
    )
    stored = variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS]._documents[0]
    assert stored["tags"] == ["fractions", "algebra"]
    assert stored["contentRevision"] == 0
    assert stored["variation"]["status"] == "validating"


async def test_patch_candidate_semantic_edit_enters_needs_validation(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    session = make_session_doc(
        problem["_id"],
        variants_app.state.primary_user["_id"],
        status="ready",
        content_revision=1,
        candidate=dict(CANDIDATE),
        validation=PASSING_VALIDATION,
        validated_revision=1,
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)

    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/candidate",
        json={"expectedRevision": 1, "correctAnswer": "8.5"},
    )
    assert response.status_code == 200
    body = response.json()["session"]
    assert body["contentRevision"] == 2
    assert body["variation"]["status"] == "needs-validation"
    assert body["variation"]["candidate"]["correctAnswer"] == "8.5"


async def test_generate_overrides_in_flight_session(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    session = make_session_doc(
        problem["_id"],
        variants_app.state.primary_user["_id"],
        status="validating",
        content_revision=2,
        candidate=dict(CANDIDATE),
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)

    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/generate",
        json={"expectedRevision": 2},
    )
    assert response.status_code == 202
    body = response.json()["session"]
    assert body["contentRevision"] == 3
    assert body["variation"]["status"] == "queued"
    assert body["variation"]["candidate"] is None
    assert body["variation"]["generationCount"] == 2
    assert len(variants_app.state.generation_starts) == 1


async def test_attest_fail_override_restores_ready(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    session = make_session_doc(
        problem["_id"],
        variants_app.state.primary_user["_id"],
        status="failed",
        content_revision=3,
        candidate=dict(CANDIDATE),
        validation={
            "verdict": "fail",
            "failures": [{"kind": "answer", "evidence": "answer mismatch"}],
            "reports": [],
        },
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)

    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/attest",
        json={"expectedRevision": 3},
    )
    assert response.status_code == 200
    body = response.json()["session"]
    assert body["variation"]["status"] == "ready"
    assert body["variation"]["attestation"]["revision"] == 3


async def test_action_conflicts_return_structured_409s(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    """Codex R3: stale-revision and invalid-state rejections on Generate,
    Revalidate and Attest must surface as structured 409s (like PATCH),
    never 500s, and must leave the session state unchanged."""
    problem = await create_problem(variants_app)
    user_id = variants_app.state.primary_user["_id"]

    # Stale revision on Generate.
    session = make_session_doc(
        problem["_id"], user_id, status="queued", content_revision=5
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)
    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/generate",
        json={"expectedRevision": 2},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_MISMATCH"

    # Stale revision on Revalidate.
    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/revalidate",
        json={"expectedRevision": 2},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_MISMATCH"

    # Attestation refusal on a pass-verdict session (attest exists only to
    # override a failed validation): matching revision, invalid state.
    ready = make_session_doc(
        problem["_id"],
        user_id,
        status="ready",
        content_revision=1,
        candidate=dict(CANDIDATE),
        validation={"verdict": "pass", "failures": [], "reports": []},
        validated_revision=1,
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(ready)
    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{ready['_id']}/attest",
        json={"expectedRevision": 1},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_VARIATION_STATE"

    # All rejected requests preserved their session state.
    stored = await variants_app.state.fake_database[
        PROBLEM_VARIANT_SESSIONS
    ].find_one({"_id": session["_id"]})
    assert stored["contentRevision"] == 5
    assert stored["variation"]["status"] == "queued"
    stored_ready = await variants_app.state.fake_database[
        PROBLEM_VARIANT_SESSIONS
    ].find_one({"_id": ready["_id"]})
    assert stored_ready["variation"]["attestation"] is None


async def test_submit_admits_new_problem_with_provenance(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    session = make_session_doc(
        problem["_id"],
        variants_app.state.primary_user["_id"],
        status="ready",
        content_revision=2,
        candidate=dict(CANDIDATE),
        validation=PASSING_VALIDATION,
        validated_revision=2,
        tags=["algebra", "fractions"],
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)
    source_before = deepcopy(
        await variants_app.state.fake_database["problems"].find_one(
            {"_id": problem["_id"]}
        )
    )

    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/submit",
        json={"expectedRevision": 2},
    )
    assert response.status_code == 200
    admitted_id = response.json()["problemId"]
    assert response.json()["alreadySubmitted"] is False

    database = variants_app.state.fake_database
    admitted = await database["problems"].find_one({"_id": ObjectId(admitted_id)})
    # Content from the accepted candidate; provenance round-trips the
    # display string into the frozen CorrectAnswer shape.
    assert admitted["text"] == "What is 3+5?"
    assert admitted["correctAnswer"]["display"] == "8"
    assert admitted["correctAnswer"]["normalizedText"] == "8"
    assert admitted["sourceImage"] is None
    assert admitted["variation"]["mode"] == "transfer-variant"
    assert admitted["variation"]["original"]["sourceProblemId"] == str(problem["_id"])
    assert admitted["variation"]["original"]["correctAnswer"]["display"] == "4"
    assert admitted["variation"]["original"]["auditImage"]["objectKey"] == (
        problem["sourceImage"]["objectKey"]
    )
    assert admitted["variation"]["validation"]["verdict"] == "pass"
    assert admitted["variation"]["validation"]["attestedByUser"] is False
    assert admitted["tags"] == ["algebra", "fractions"]

    # The source problem is untouched.
    source_after = await database["problems"].find_one({"_id": problem["_id"]})
    assert source_after["updatedAt"] == source_before["updatedAt"]
    assert source_after["text"] == source_before["text"]
    assert source_after["correctAnswer"] == source_before["correctAnswer"]

    # Solution generation is enqueued; tags are registered.
    task = await database["solution_generation_tasks"].find_one(
        {"problem_id": admitted_id}
    )
    assert task is not None
    tag_row = await database["tags"].find_one(
        {"userId": variants_app.state.primary_user["_id"], "name": "fractions"}
    )
    assert tag_row is not None

    # The session recorded the admission.
    stored = database[PROBLEM_VARIANT_SESSIONS]._documents[0]
    assert stored["submit"]["submittedProblemId"] == admitted_id

    # Detail payload exposes the provenance link (and nothing else of `original`).
    detail = await client.get(f"/api/v1/problems/{admitted_id}")
    assert detail.status_code == 200
    variation = detail.json()["problem"]["variation"]
    assert variation["sourceProblemId"] == str(problem["_id"])
    assert "original" not in variation
    assert "auditImage" not in variation

    # Duplicate submit is an explicit 409.
    duplicate = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/submit",
        json={"expectedRevision": 2},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "VARIANT_ALREADY_SUBMITTED"


async def test_submit_inherits_audit_image_through_variant_chain(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    audit_image = {
        "bucket": "learnloop-media",
        "objectKey": "audit/original.png",
        "contentType": "image/png",
        "sizeBytes": 9,
        "sha256": "deadbeef",
        "uploadedAt": NOW,
    }
    grandparent_audit = dict(audit_image, objectKey="audit/chain-origin.png")
    # The source itself is an admitted variant without a source image: its
    # provenance carries the chain's single audit image.
    source_variation = {
        "mode": "transfer-variant",
        "original": {
            "text": "s",
            "problemType": "short-answer",
            "subject": "math",
            "graphDsl": None,
            "correctAnswer": {"display": "4"},
            "auditImage": grandparent_audit,
        },
        "acceptedVariant": {},
        "generator": {"provider": "p", "model": "m"},
        "generationCount": 1,
        "validation": {"verdict": "pass"},
    }
    problem = await create_problem(
        variants_app, with_source_image=False, variation=source_variation
    )
    session = make_session_doc(
        problem["_id"],
        variants_app.state.primary_user["_id"],
        status="ready",
        content_revision=1,
        candidate=dict(CANDIDATE),
        validation=PASSING_VALIDATION,
        validated_revision=1,
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)

    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/submit",
        json={"expectedRevision": 1},
    )
    assert response.status_code == 200
    admitted = await variants_app.state.fake_database["problems"].find_one(
        {"_id": ObjectId(response.json()["problemId"])}
    )
    # A 3-level chain keeps ONE audit image.
    assert (
        admitted["variation"]["original"]["auditImage"]["objectKey"]
        == "audit/chain-origin.png"
    )


async def test_submit_rejects_stale_validation_without_attestation(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    session = make_session_doc(
        problem["_id"],
        variants_app.state.primary_user["_id"],
        status="ready",
        content_revision=5,
        candidate=dict(CANDIDATE),
        validation=PASSING_VALIDATION,
        validated_revision=2,
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)

    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/submit",
        json={"expectedRevision": 5},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VARIANT_INVALIDATED"


async def test_submit_rejects_soft_deleted_source(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app, is_deleted=True)
    session = make_session_doc(
        problem["_id"],
        variants_app.state.primary_user["_id"],
        status="ready",
        content_revision=1,
        candidate=dict(CANDIDATE),
        validation=PASSING_VALIDATION,
        validated_revision=1,
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)

    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/submit",
        json={"expectedRevision": 1},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VARIANT_SOURCE_GONE"


async def test_submit_admits_fail_attested_variant(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    session = make_session_doc(
        problem["_id"],
        variants_app.state.primary_user["_id"],
        status="ready",
        content_revision=3,
        candidate=dict(CANDIDATE),
        validation={
            "verdict": "fail",
            "failures": [{"kind": "check", "evidence": "waved"}],
            "reports": [],
        },
        attestation={"revision": 3, "at": NOW},
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)

    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/submit",
        json={"expectedRevision": 3},
    )
    assert response.status_code == 200
    admitted = await variants_app.state.fake_database["problems"].find_one(
        {"_id": ObjectId(response.json()["problemId"])}
    )
    # The real verdict is frozen honestly with the user attestation flag.
    assert admitted["variation"]["validation"]["verdict"] == "fail"
    assert admitted["variation"]["validation"]["attestedByUser"] is True


async def test_discard_is_terminal(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    problem = await create_problem(variants_app)
    session = make_session_doc(problem["_id"], variants_app.state.primary_user["_id"])
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)

    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/discard",
        json={"expectedRevision": 0},
    )
    assert response.status_code == 200
    assert response.json()["session"]["discardedAt"] is not None

    active = await client.get(f"/api/v1/problems/{problem['_id']}/variants/active")
    assert active.json()["session"] is None


async def test_discard_after_submit_conflicts(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    """Codex P2: a discard that lost a race with submit must return 409 —
    never 200 — so the client does not navigate away believing a problem
    was not admitted."""
    problem = await create_problem(variants_app)
    session = make_session_doc(
        problem["_id"],
        variants_app.state.primary_user["_id"],
        status="ready",
        content_revision=1,
        candidate=dict(CANDIDATE),
        validation={"verdict": "pass", "failures": [], "reports": []},
        submit={"submittedProblemId": str(ObjectId()), "success": True},
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)

    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/discard",
        json={"expectedRevision": 1},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VARIANT_ALREADY_SUBMITTED"


async def test_stale_terminal_requests_are_rejected(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    """Codex R5: submit and discard honor the caller's reviewed revision —
    stale requests are rejected before admission or cancellation."""
    problem = await create_problem(variants_app)
    user_id = variants_app.state.primary_user["_id"]
    session = make_session_doc(
        problem["_id"],
        user_id,
        status="ready",
        content_revision=3,
        candidate=dict(CANDIDATE),
        validation=PASSING_VALIDATION,
        validated_revision=3,
    )
    await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(session)

    # Stale submit admits nothing.
    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/submit",
        json={"expectedRevision": 2},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_MISMATCH"
    assert await variants_app.state.fake_database["problems"].count_documents(
        {}
    ) == 1  # only the source problem exists

    # Stale discard rejects and cancels nothing: the session stays live.
    response = await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/discard",
        json={"expectedRevision": 2},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_MISMATCH"
    stored = await variants_app.state.fake_database[
        PROBLEM_VARIANT_SESSIONS
    ].find_one({"_id": session["_id"]})
    assert stored["discardedAt"] is None
    assert stored["submit"] is None
    assert stored["contentRevision"] == 3


async def test_submit_walks_a_real_three_level_chain(
    variants_app: FastAPI, client: AsyncClient
) -> None:
    """Codex R7: a genuine 3-level chain (P1 → P2 → P3, two admissions from
    variant sources) keeps ONE audit image, points each provenance at its
    DIRECT source, preserves the whole frozen source at admission, and the
    unreadable-intermediate suppression premise holds: the deleted middle
    404s while the leaf keeps its provenance link."""
    source_problem = await create_problem(variants_app)
    source_audit_key = source_problem["sourceImage"]["objectKey"]

    async def admit_variant_from(source: dict[str, Any]) -> dict[str, Any]:
        session = make_session_doc(
            source["_id"],
            variants_app.state.primary_user["_id"],
            status="ready",
            content_revision=1,
            candidate=dict(CANDIDATE),
            validation=PASSING_VALIDATION,
            validated_revision=1,
        )
        # The real create endpoint freezes the SOURCE problem's content into
        # the session original; mirror that instead of the fixture's canned
        # values so level 3 derives from level 2's actual content.
        session["variation"]["original"] = {
            "text": source["text"],
            "problemType": source["problemType"],
            "graphDsl": source["graphDsl"],
            "correctAnswer": source["correctAnswer"]["display"],
            "subject": source["subject"],
        }
        await variants_app.state.fake_database[PROBLEM_VARIANT_SESSIONS].insert_one(
            session
        )
        response = await client.post(
            f"/api/v1/problems/{source['_id']}/variants/{session['_id']}/submit",
            json={"expectedRevision": 1},
        )
        assert response.status_code == 200
        return await variants_app.state.fake_database["problems"].find_one(
            {"_id": ObjectId(response.json()["problemId"])}
        )

    p2 = await admit_variant_from(source_problem)
    p3 = await admit_variant_from(p2)

    # Provenance points at the DIRECT source, never a skip-link to the origin.
    assert p2["variation"]["original"]["sourceProblemId"] == str(source_problem["_id"])
    assert p3["variation"]["original"]["sourceProblemId"] == str(p2["_id"])

    # One audit image inherited through the whole chain.
    assert p2["variation"]["original"]["auditImage"]["objectKey"] == source_audit_key
    assert p3["variation"]["original"]["auditImage"]["objectKey"] == source_audit_key

    # Whole-source preservation: every admission freezes the complete source
    # content with the normalized original answer, not selected fields.
    for child, source in ((p2, source_problem), (p3, p2)):
        frozen = child["variation"]["original"]
        assert frozen["text"] == source["text"]
        assert frozen["problemType"] == source["problemType"]
        assert frozen["subject"] == source["subject"]
        assert frozen["graphDsl"] == source["graphDsl"]
        assert frozen["correctAnswer"] == source["correctAnswer"]

    # Suppression premise: the soft-deleted intermediate becomes unreadable
    # while the leaf retains its provenance link for the UI to suppress.
    delete_response = await client.delete(f"/api/v1/problems/{p2['_id']}")
    assert delete_response.status_code == 200
    assert (
        await client.get(f"/api/v1/problems/{p2['_id']}")
    ).status_code == 404
    leaf = await client.get(f"/api/v1/problems/{p3['_id']}")
    assert leaf.status_code == 200
    assert leaf.json()["problem"]["variation"]["sourceProblemId"] == str(p2["_id"])
