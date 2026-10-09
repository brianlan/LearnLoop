"""Real-Mongo tests for problem-variant sessions (issue #685).

The partial unique index and its concurrency behavior cannot be tested
against fakes (FakeCollection.create_index is a no-op): here the index
definition is asserted via ``index_information()`` and a concurrent second
create is proven to be rejected.
"""

from __future__ import annotations

import asyncio
import os
import re
from bson import ObjectId
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from app.infrastructure.problem_variants.repository import (
    PROBLEM_VARIANT_SESSIONS_COLLECTION,
    build_problem_variant_session_document,
    find_active_problem_variant_session,
)
from app.infrastructure.storage.mongo import MongoClientAdapter, ensure_database_setup

pytestmark = pytest.mark.real_mongo

REAL_MONGO_DATABASE_ENV = "LEARNLOOP_REAL_MONGO_DATABASE"
_REAL_MONGO_NAME_RE = re.compile(r"learnloop_test_[A-Za-z0-9_-]+")
NOW = datetime.now(UTC)


def validate_real_mongo_database_name(name: str | None) -> str:
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


async def test_active_session_partial_unique_index(real_database: Any) -> None:
    collection = real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION]
    index_info = await collection.index_information()
    entry = index_info["problem_user_active_session_unique"]
    assert entry["unique"] is True
    assert entry["key"] == [("problemId", 1), ("userId", 1)]
    assert entry.get("partialFilterExpression") == {
        "submit": None,
        "discardedAt": None,
    }


async def test_concurrent_second_create_is_rejected(real_database: Any) -> None:
    collection = real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION]
    problem_id = str(ObjectId())
    user_id = ObjectId()
    docs = [
        build_problem_variant_session_document(
            problem_id=problem_id,
            user_id=user_id,
            mode="transfer-variant",
            original={"text": "s", "problemType": "short-answer",
                      "graphDsl": None, "correctAnswer": "4", "subject": "math"},
            tags=[],
            now=NOW,
        )
        for _ in range(2)
    ]

    results = await asyncio.gather(
        collection.insert_one(docs[0]),
        collection.insert_one(docs[1]),
        return_exceptions=True,
    )
    inserted = [r for r in results if not isinstance(r, Exception)]
    duplicate = [r for r in results if isinstance(r, Exception)]
    assert len(inserted) == 1
    assert len(duplicate) == 1
    assert isinstance(duplicate[0], DuplicateKeyError)
    assert await find_active_problem_variant_session(
        real_database, user_id, problem_id
    ) is not None


async def test_history_rows_do_not_block_new_sessions(real_database: Any) -> None:
    collection = real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION]
    problem_id = str(ObjectId())
    user_id = ObjectId()
    terminal = build_problem_variant_session_document(
        problem_id=problem_id,
        user_id=user_id,
        mode="data-only",
        original={"text": "s", "problemType": "short-answer",
                  "graphDsl": None, "correctAnswer": "4", "subject": "math"},
        tags=[],
        now=NOW,
    )
    terminal["discardedAt"] = NOW
    await collection.insert_one(terminal)

    # The partial filter keeps the discarded row out of the unique set: a
    # fresh session for the same problem is admitted.
    fresh = build_problem_variant_session_document(
        problem_id=problem_id,
        user_id=user_id,
        mode="transfer-variant",
        original={"text": "s", "problemType": "short-answer",
                  "graphDsl": None, "correctAnswer": "4", "subject": "math"},
        tags=[],
        now=NOW,
    )
    await collection.insert_one(fresh)
    active = await find_active_problem_variant_session(
        real_database, user_id, problem_id
    )
    assert active is not None
    assert active["mode"] == "transfer-variant"




def _ready_session_document(problem_id: str, user_id: ObjectId) -> dict[str, Any]:
    doc = build_problem_variant_session_document(
        problem_id=problem_id,
        user_id=user_id,
        mode="transfer-variant",
        original={"text": "s", "problemType": "short-answer",
                  "graphDsl": None, "correctAnswer": "4", "subject": "math"},
        tags=[],
        now=NOW,
    )
    doc["variation"]["status"] = "ready"
    doc["variation"]["candidate"] = {
        "text": "v", "problemType": "short-answer", "graphDsl": None,
        "correctAnswer": "8", "subject": "math",
        "generator": {"provider": "p", "model": "m"},
    }
    doc["variation"]["validation"] = {"verdict": "pass", "failures": [], "reports": []}
    doc["variation"]["validatedRevision"] = 0
    return doc


async def test_live_submit_reservation_blocks_generate_race(
    real_database: Any,
) -> None:
    """Codex R6: a live submit reservation wins the Generate-vs-submit race
    against real Mongo — even when Generate fires concurrently."""
    from datetime import timedelta

    from app.infrastructure.problem_variants.repository import (
        release_problem_variant_submit_reservation,
        reserve_problem_variant_for_submit,
        request_problem_variant_generation,
    )

    problem_id = str(ObjectId())
    user_id = ObjectId()
    doc = _ready_session_document(problem_id, user_id)
    await real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION].insert_one(doc)

    token = await reserve_problem_variant_for_submit(
        real_database, user_id, problem_id, doc["_id"],
        expected_revision=0, now=NOW,
    )
    assert isinstance(token, str) and token

    races = await asyncio.gather(*[
        request_problem_variant_generation(
            real_database, user_id, problem_id, doc["_id"],
            expected_revision=0, now=NOW,
        )
        for _ in range(3)
    ])
    assert not any(races)

    # The reservation releases; Generate then wins and clears fields.
    assert await release_problem_variant_submit_reservation(
        real_database, user_id, problem_id, doc["_id"],
        token=token, now=NOW,
    )
    later = NOW + timedelta(seconds=1)
    assert await request_problem_variant_generation(
        real_database, user_id, problem_id, doc["_id"],
        expected_revision=0, now=later,
    )
    stored = await real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION].find_one(
        {"_id": doc["_id"]}
    )
    assert stored["variation"]["status"] == "queued"
    assert stored["variation"]["candidate"] is None
    assert stored["variation"]["submitReservation"] is None


async def test_submit_reservation_rejects_stale_revision(
    real_database: Any,
) -> None:
    """Codex R10: the acquisition predicate includes contentRevision on a
    real server too, so a stale submit leaves the session untouched and
    does not block a current-revision Generate."""
    from app.infrastructure.problem_variants.repository import (
        reserve_problem_variant_for_submit,
        request_problem_variant_generation,
    )

    problem_id = str(ObjectId())
    user_id = ObjectId()
    doc = _ready_session_document(problem_id, user_id)
    await real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION].insert_one(doc)

    assert await reserve_problem_variant_for_submit(
        real_database, user_id, problem_id, doc["_id"],
        expected_revision=1, now=NOW,
    ) is None
    stored = await real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION].find_one(
        {"_id": doc["_id"]}
    )
    assert stored["variation"].get("submitReservation") is None
    assert await request_problem_variant_generation(
        real_database, user_id, problem_id, doc["_id"],
        expected_revision=0, now=NOW,
    ) is True


async def test_concurrent_submit_reserves_are_mutually_exclusive(
    real_database: Any,
) -> None:
    """Codex R6: only one of two simultaneous submit reservations wins."""
    from app.infrastructure.problem_variants.repository import (
        reserve_problem_variant_for_submit,
    )

    problem_id = str(ObjectId())
    user_id = ObjectId()
    doc = _ready_session_document(problem_id, user_id)
    await real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION].insert_one(doc)

    tokens = await asyncio.gather(*[
        reserve_problem_variant_for_submit(
            real_database, user_id, problem_id, doc["_id"],
            expected_revision=0, now=NOW,
        )
        for _ in range(2)
    ])
    won = [t for t in tokens if isinstance(t, str)]
    assert len(won) == 1


# --- Route-level Submit admission on a real server (Codex R7 items 1+3) ---


def _real_variant_settings() -> Any:
    from app.infrastructure.config.settings import Settings

    return Settings(
        mongodb_uri=os.environ["MONGODB_URI"],
        mongodb_database=validate_real_mongo_database_name(
            os.environ.get(REAL_MONGO_DATABASE_ENV)
        ),
    )


def _build_real_variant_app(
    database: Any,
    adapter: Any,
    user_id: ObjectId,
) -> Any:
    """The variant routes wired to a real database and real adapter, with
    the same dependency-override seams the fake-based API tests use."""
    from app.main import create_app
    from app.presentation.deps import (
        get_app_settings,
        get_current_user,
        get_database,
        get_mongo_adapter,
        get_s3_storage,
    )
    from tests.conftest import FakeStorage

    application = create_app()
    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_current_user] = lambda: {"_id": user_id}
    application.dependency_overrides[get_app_settings] = _real_variant_settings
    application.dependency_overrides[get_mongo_adapter] = lambda: adapter
    application.dependency_overrides[get_s3_storage] = lambda: FakeStorage()
    return application


async def _seed_route_problem(real_database: Any) -> tuple[Any, ObjectId]:
    from tests.api.conftest import make_problem

    user_id = ObjectId()
    problem = make_problem(user_id)
    # transfer-variant admission archives the source image metadata
    # (VARIANT_AUDIT_MISSING otherwise); create requires it too.
    problem["sourceImage"] = {
        "bucket": "learnloop-media",
        "objectKey": f"users/{user_id}/images/{ObjectId()}.png",
        "contentType": "image/png",
        "sizeBytes": 4,
        "sha256": "abc",
        "uploadedAt": NOW,
    }
    await real_database["problems"].insert_one(problem)
    return problem, user_id


async def _seed_route_scenario(real_database: Any) -> tuple[Any, Any, ObjectId]:
    problem, user_id = await _seed_route_problem(real_database)
    session = _ready_session_document(str(problem["_id"]), user_id)
    await real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION].insert_one(session)
    return problem, session, user_id


async def test_route_submit_admits_in_real_transaction_and_preserves_source(
    real_database: Any,
) -> None:
    """Codex R7: the Submit route's admission runs as one real transaction on
    a real server — admitted problem, submit record and exactly one solution
    task land together — and the source problem document is preserved
    whole-document (R7 item 3)."""
    from httpx import ASGITransport, AsyncClient

    adapter = MongoClientAdapter(_real_variant_settings())
    problem, session, user_id = await _seed_route_scenario(real_database)
    application = _build_real_variant_app(adapter.get_database(), adapter, user_id)
    source_before = await real_database["problems"].find_one({"_id": problem["_id"]})

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/submit",
            json={"expectedRevision": 0},
        )

    assert response.status_code == 200
    admitted_id = response.json()["problemId"]
    app_db = adapter.get_database()
    admitted = await app_db["problems"].find_one({"_id": ObjectId(admitted_id)})
    assert admitted is not None
    assert admitted["text"] == "v"
    # Whole-document source preservation across the admission.
    source_after = await real_database["problems"].find_one({"_id": problem["_id"]})
    assert source_after == source_before
    # Exactly one solution task for the admitted problem.
    tasks = await app_db["solution_generation_tasks"].find(
        {"problem_id": admitted_id}
    ).to_list(None)
    assert len(tasks) == 1
    session_after = await real_database[
        PROBLEM_VARIANT_SESSIONS_COLLECTION
    ].find_one({"_id": session["_id"]})
    assert session_after["submit"]["success"] is True
    assert session_after["submit"]["submittedProblemId"] == admitted_id
    assert session_after["variation"]["submitReservation"] is None


async def test_route_concurrent_submits_admit_exactly_once(
    real_database: Any,
) -> None:
    """Codex R7: two concurrent Submit requests through the real route —
    exactly one admits, the other gets a structured 409, and nothing is
    double-created."""
    from httpx import ASGITransport, AsyncClient

    adapter = MongoClientAdapter(_real_variant_settings())
    problem, session, user_id = await _seed_route_scenario(real_database)
    application = _build_real_variant_app(adapter.get_database(), adapter, user_id)
    source_before = await real_database["problems"].find_one({"_id": problem["_id"]})

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        responses = await asyncio.gather(*[
            client.post(
                f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/submit",
                json={"expectedRevision": 0},
            )
            for _ in range(2)
        ])

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409]
    for response in responses:
        if response.status_code == 409:
            # Exact code depends on timing: after the winner committed the
            # loser sees VARIANT_ALREADY_SUBMITTED; against an in-flight
            # winner it sees the ready-state conflict.
            assert response.json()["error"]["code"] in {
                "VARIANT_ALREADY_SUBMITTED",
                "INVALID_VARIATION_STATE",
            }
    problems = await real_database["problems"].find({}).to_list(None)
    assert len(problems) == 2  # source + exactly one admitted
    source_after = await real_database["problems"].find_one({"_id": problem["_id"]})
    assert source_after == source_before
    app_db = adapter.get_database()
    admitted_ids = [
        str(document["_id"]) for document in problems if document["_id"] != problem["_id"]
    ]
    tasks = await app_db["solution_generation_tasks"].find({}).to_list(None)
    assert len(tasks) == 1
    assert tasks[0]["problem_id"] in admitted_ids


async def test_route_generate_blocked_by_live_reservation_then_proceeds(
    real_database: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex R7: the Submit-vs-Generate exclusion through the real routes —
    a live reservation blocks Generate with a structured 409, and after the
    reservation is released Generate proceeds."""
    from httpx import ASGITransport, AsyncClient

    from app.infrastructure.problem_variants.repository import (
        release_problem_variant_submit_reservation,
        reserve_problem_variant_for_submit,
    )

    adapter = MongoClientAdapter(_real_variant_settings())
    problem, session, user_id = await _seed_route_scenario(real_database)
    application = _build_real_variant_app(adapter.get_database(), adapter, user_id)
    token = await reserve_problem_variant_for_submit(
        real_database, user_id, str(problem["_id"]), session["_id"],
        expected_revision=0, now=NOW,
    )
    assert isinstance(token, str)
    monkeypatch.setattr(
        "app.presentation.problem_variants._require_variant_profiles",
        lambda settings: (object(), object(), object(), object()),
    )
    started: list[str] = []

    async def _fake_start(*args: Any, **kwargs: Any) -> None:
        started.append(str(kwargs.get("session_id")))

    monkeypatch.setattr(
        "app.presentation.problem_variants.start_problem_variant_generation",
        _fake_start,
    )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        blocked = await client.post(
            f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/generate",
            json={"expectedRevision": 0},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "INVALID_VARIATION_STATE"

        assert await release_problem_variant_submit_reservation(
            real_database, user_id, str(problem["_id"]), session["_id"],
            token=token, now=NOW,
        )

        generate = await client.post(
            f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/generate",
            json={"expectedRevision": 0},
        )

    assert generate.status_code == 202
    body = generate.json()["session"]
    assert body["contentRevision"] == 1
    assert body["variation"]["status"] == "queued"
    assert started == [str(session["_id"])]


class _FindOneHookCollection:
    """Pass-through collection wrapper firing a hook after every find_one."""

    def __init__(self, collection: Any, hook: Any) -> None:
        self._collection = collection
        self._hook = hook

    def __getattr__(self, name: str) -> Any:
        return getattr(self._collection, name)

    async def find_one(self, filter: Any, **kwargs: Any) -> Any:  # noqa: A002
        document = await self._collection.find_one(filter, **kwargs)
        await self._hook(filter, document)
        return document


class _HookedDatabase:
    """Database wrapper routing selected collections through hooks, so a
    test can interleave a concurrent mutation at the exact seam the route
    reads (no pymongo Collection instance caching — patching an instance
    would not be seen by the route)."""

    def __init__(self, database: Any, hooks: dict[str, Any]) -> None:
        self._database = database
        self._hooks = hooks

    def __getattr__(self, name: str) -> Any:
        return getattr(self._database, name)

    def __getitem__(self, name: str) -> Any:
        if name in self._hooks:
            return _FindOneHookCollection(self._database[name], self._hooks[name])
        return self._database[name]


async def test_route_submit_rolls_back_owner_lost_admission_and_releases(
    real_database: Any,
) -> None:
    """Codex R7: owner-loss mid-admission through the real route — a
    concurrent revision bump between the transaction's session read and the
    recorder write invalidates the transaction's snapshot (WriteConflict on
    the owner-checked recorder update), so ``with_transaction`` re-executes
    the callback, the fenced in-transaction revision check rejects the
    retried attempt, the rolled-back attempt leaves no admitted problem and
    the route releases the reservation."""
    from httpx import ASGITransport, AsyncClient

    adapter = MongoClientAdapter(_real_variant_settings())
    problem, session, user_id = await _seed_route_scenario(real_database)
    app_db = adapter.get_database()

    async def _bump_revision(filter: Any, document: Any) -> None:
        if document is not None and document.get("contentRevision") == 0:
            await app_db[PROBLEM_VARIANT_SESSIONS_COLLECTION].update_one(
                {"_id": document["_id"]},
                {"$set": {"contentRevision": 1}},
            )

    hooked_database = _HookedDatabase(
        app_db, {PROBLEM_VARIANT_SESSIONS_COLLECTION: _bump_revision}
    )
    application = _build_real_variant_app(hooked_database, adapter, user_id)
    source_before = await real_database["problems"].find_one({"_id": problem["_id"]})

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/submit",
            json={"expectedRevision": 0},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_MISMATCH"
    # The admitted problem and its solution task were rolled back.
    problems = await real_database["problems"].find({}).to_list(None)
    assert len(problems) == 1
    assert problems[0]["_id"] == problem["_id"]
    source_after = await real_database["problems"].find_one({"_id": problem["_id"]})
    assert source_after == source_before
    tasks = await real_database["solution_generation_tasks"].find({}).to_list(None)
    assert tasks == []
    # The reservation was released; the session shows the concurrent bump.
    session_after = await real_database[
        PROBLEM_VARIANT_SESSIONS_COLLECTION
    ].find_one({"_id": session["_id"]})
    assert session_after["contentRevision"] == 1
    assert session_after["submit"] is None
    assert session_after["variation"]["submitReservation"] is None
    assert session_after["variation"]["status"] == "ready"


# --- Connected flows: create/retry -> executor -> Submit (Codex R7 item 2) ---


def _install_connected_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real create/retry routes and real in-process executor; only the
    provider clients are faked."""
    from app.domain.ingestion.variation import (
        VariantAssessment,
        VariantCandidate,
        VariantGenerationResult,
    )
    from app.infrastructure.problem_variants import executor as variant_executor

    class FakeGenerator:
        identity = {"provider": "fake", "model": "gen-model"}

        async def generate_candidate(
            self, *, mode: str, source: Any
        ) -> VariantCandidate:
            return VariantCandidate(
                text="What is 3+5?",
                problem_type=source.problem_type,
                subject=source.subject,
                graph_dsl=source.graph_dsl,
                correct_answer="8",
                generator={"provider": "fake", "model": "gen-model"},
            )

    async def fake_generate_and_validate(**kwargs: Any) -> Any:
        return VariantGenerationResult(
            assessment=VariantAssessment(verdict="pass", failures=[]),
            reports=[],
        )

    monkeypatch.setattr(
        variant_executor, "generate_and_validate", fake_generate_and_validate
    )
    monkeypatch.setattr(
        "app.presentation.problem_variants._require_variant_profiles",
        lambda settings: (FakeGenerator(), object(), None, object()),
    )


async def _await_connected_generation(session_id: Any) -> None:
    from app.infrastructure.problem_variants import executor as variant_executor

    task = variant_executor._tasks.get(str(session_id))
    if task is not None:
        await task
    variant_executor.cancel_problem_variant_task(session_id)


async def _submit_connected(
    client: Any, problem: Any, session_id: str, expected_revision: int
) -> Any:
    return await client.post(
        f"/api/v1/problems/{problem['_id']}/variants/{session_id}/submit",
        json={"expectedRevision": expected_revision},
    )


async def test_connected_data_only_create_completes_and_admits(
    real_database: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex R7: real creation -> real executor completion -> Submit
    admission for data-only, with whole-document source preservation."""
    from httpx import ASGITransport, AsyncClient

    _install_connected_executor(monkeypatch)
    adapter = MongoClientAdapter(_real_variant_settings())
    problem, user_id = await _seed_route_problem(real_database)
    application = _build_real_variant_app(adapter.get_database(), adapter, user_id)
    source_before = await real_database["problems"].find_one({"_id": problem["_id"]})

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            f"/api/v1/problems/{problem['_id']}/variants",
            json={"mode": "data-only"},
        )
        assert created.status_code == 202
        session_view = created.json()["session"]
        session_id = session_view["sessionId"]
        assert session_view["variation"]["status"] == "queued"

        await _await_connected_generation(session_id)

        stored = await real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION].find_one(
            {"_id": ObjectId(session_id)}
        )
        assert stored["variation"]["status"] == "ready"
        assert stored["variation"]["candidate"]["correctAnswer"] == "8"
        assert stored["variation"]["validation"]["verdict"] == "pass"
        assert stored["variation"]["validatedRevision"] == 0

        admitted = await _submit_connected(client, problem, session_id, 0)

    assert admitted.status_code == 200
    admitted_id = admitted.json()["problemId"]
    app_db = adapter.get_database()
    assert await app_db["problems"].find_one({"_id": ObjectId(admitted_id)})
    assert await real_database["problems"].find_one(
        {"_id": problem["_id"]}
    ) == source_before
    tasks = await app_db["solution_generation_tasks"].find(
        {"problem_id": admitted_id}
    ).to_list(None)
    assert len(tasks) == 1
    stored_after = await real_database[
        PROBLEM_VARIANT_SESSIONS_COLLECTION
    ].find_one({"_id": ObjectId(session_id)})
    assert stored_after["submit"]["success"] is True
    assert stored_after["variation"]["submitReservation"] is None


async def test_connected_transfer_variant_create_completes_and_admits(
    real_database: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex R7: same connected flow for transfer-variant — creation through
    the real route, executor completion on the real session, admission via
    the real Submit transaction."""
    from httpx import ASGITransport, AsyncClient

    _install_connected_executor(monkeypatch)
    adapter = MongoClientAdapter(_real_variant_settings())
    problem, user_id = await _seed_route_problem(real_database)
    application = _build_real_variant_app(adapter.get_database(), adapter, user_id)
    source_before = await real_database["problems"].find_one({"_id": problem["_id"]})

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            f"/api/v1/problems/{problem['_id']}/variants",
            json={"mode": "transfer-variant"},
        )
        assert created.status_code == 202
        session_id = created.json()["session"]["sessionId"]

        await _await_connected_generation(session_id)

        admitted = await _submit_connected(client, problem, session_id, 0)

    assert admitted.status_code == 200
    admitted_id = admitted.json()["problemId"]
    assert await real_database["problems"].find_one(
        {"_id": problem["_id"]}
    ) == source_before
    tasks = await adapter.get_database()["solution_generation_tasks"].find(
        {"problem_id": admitted_id}
    ).to_list(None)
    assert len(tasks) == 1


async def test_connected_retry_after_persisted_interruption_completes_and_admits(
    real_database: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex R7: a session persisted mid-flight (checkpoint lost when the
    process died) is healed by a real Generate Retry: the override bumps the
    revision, the real executor re-runs, and the completed candidate admits
    through the real Submit transaction."""
    from httpx import ASGITransport, AsyncClient

    from app.infrastructure.problem_variants.repository import (
        build_problem_variant_session_document,
    )

    _install_connected_executor(monkeypatch)
    adapter = MongoClientAdapter(_real_variant_settings())
    problem, user_id = await _seed_route_problem(real_database)
    session = build_problem_variant_session_document(
        problem_id=str(problem["_id"]),
        user_id=user_id,
        mode="data-only",
        original={
            "text": "What is 2+2?",
            "problemType": "short-answer",
            "graphDsl": None,
            "correctAnswer": "4",
            "subject": "math",
        },
        tags=[],
        now=NOW,
    )
    # Persisted in-flight: the process died during validation.
    session["contentRevision"] = 2
    session["variation"]["status"] = "validating"
    session["variation"]["candidate"] = {
        "text": "partial checkpoint",
        "problemType": "short-answer",
        "graphDsl": None,
        "correctAnswer": "8",
        "subject": "math",
        "generator": {"provider": "fake", "model": "gen-model"},
    }
    await real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION].insert_one(session)
    application = _build_real_variant_app(adapter.get_database(), adapter, user_id)
    source_before = await real_database["problems"].find_one({"_id": problem["_id"]})

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        retry = await client.post(
            f"/api/v1/problems/{problem['_id']}/variants/{session['_id']}/generate",
            json={"expectedRevision": 2},
        )
        assert retry.status_code == 202
        body = retry.json()["session"]
        assert body["contentRevision"] == 3
        assert body["variation"]["status"] == "queued"
        assert body["variation"]["candidate"] is None

        await _await_connected_generation(session["_id"])

        stored = await real_database[PROBLEM_VARIANT_SESSIONS_COLLECTION].find_one(
            {"_id": session["_id"]}
        )
        assert stored["variation"]["status"] == "ready"
        assert stored["variation"]["validatedRevision"] == 3

        admitted = await _submit_connected(client, problem, session["_id"], 3)

    assert admitted.status_code == 200
    admitted_id = admitted.json()["problemId"]
    assert await real_database["problems"].find_one(
        {"_id": problem["_id"]}
    ) == source_before
    tasks = await adapter.get_database()["solution_generation_tasks"].find(
        {"problem_id": admitted_id}
    ).to_list(None)
    assert len(tasks) == 1


async def test_connected_chain_preserves_each_source_whole_document(
    real_database: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex R7: a two-hop connected chain — P0 admits P1, P1 admits P2 —
    preserves every complete source document around each admission,
    including the intermediate variant source."""
    from httpx import ASGITransport, AsyncClient

    _install_connected_executor(monkeypatch)
    adapter = MongoClientAdapter(_real_variant_settings())
    problem, user_id = await _seed_route_problem(real_database)
    application = _build_real_variant_app(adapter.get_database(), adapter, user_id)
    p0_before = await real_database["problems"].find_one({"_id": problem["_id"]})

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Hop 1: P0 -> P1.
        created = await client.post(
            f"/api/v1/problems/{problem['_id']}/variants",
            json={"mode": "transfer-variant"},
        )
        assert created.status_code == 202
        session_id = created.json()["session"]["sessionId"]
        await _await_connected_generation(session_id)
        admitted = await _submit_connected(client, problem, session_id, 0)
        assert admitted.status_code == 200
        p1_id = ObjectId(admitted.json()["problemId"])
        p1_before = await real_database["problems"].find_one({"_id": p1_id})
        assert p1_before is not None
        assert await real_database["problems"].find_one(
            {"_id": problem["_id"]}
        ) == p0_before

        # Hop 2: P1 (a variant problem) -> P2.
        created2 = await client.post(
            f"/api/v1/problems/{p1_id}/variants",
            json={"mode": "transfer-variant"},
        )
        assert created2.status_code == 202
        session2_id = created2.json()["session"]["sessionId"]
        await _await_connected_generation(session2_id)
        admitted2 = await _submit_connected(client, {"_id": p1_id}, session2_id, 0)
        assert admitted2.status_code == 200
        p2_id = ObjectId(admitted2.json()["problemId"])
        assert await real_database["problems"].find_one({"_id": p2_id})

    # Both sources are preserved whole-document around each admission.
    assert await real_database["problems"].find_one(
        {"_id": problem["_id"]}
    ) == p0_before
    assert await real_database["problems"].find_one({"_id": p1_id}) == p1_before
    app_db = adapter.get_database()
    for admitted_problem_id in (str(p1_id), str(p2_id)):
        tasks = await app_db["solution_generation_tasks"].find(
            {"problem_id": admitted_problem_id}
        ).to_list(None)
        assert len(tasks) == 1
