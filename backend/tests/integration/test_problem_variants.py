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
from app.infrastructure.storage.mongo import ensure_database_setup

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


