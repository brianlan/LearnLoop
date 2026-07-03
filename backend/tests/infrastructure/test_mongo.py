from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest

from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion.repository import (
    BATCH_INDEXES,
    INGESTION_BATCHES_COLLECTION,
)
from app.infrastructure.storage.mongo import (
    AsyncMongoClientFactory,
    CANONICAL_SOLUTIONS_COLLECTION,
    COACHING_CONVERSATIONS_COLLECTION,
    FOLDERS_COLLECTION,
    MongoClientAdapter,
    SOLUTION_GENERATION_TASKS_COLLECTION,
    TAGS_COLLECTION,
    ensure_database_setup,
    get_client,
    get_database,
)


# These fakes stay local because they model database-level administration
# (list_collection_names/create_collection) and index-call tracking, which are
# shape-divergent from the shared Mongo/S3 fakes in tests/conftest.py.
class FakeDatabase:
    def __init__(self, name: str) -> None:
        self.name = name
        self.created_collections: list[str] = []
        self.collection_names: list[str] = []
        self.collections: dict[str, FakeCollection] = {}

    async def list_collection_names(self) -> list[str]:
        return list(self.collection_names)

    async def create_collection(self, name: str) -> None:
        self.created_collections.append(name)
        self.collection_names.append(name)
        self.collections.setdefault(name, FakeCollection())

    def __getitem__(self, name: str) -> "FakeCollection":
        return self.collections.setdefault(name, FakeCollection())


class FakeCollection:
    def __init__(self) -> None:
        self.index_calls: list[dict[str, Any]] = []

    async def create_index(self, keys, **kwargs) -> None:
        self.index_calls.append({"keys": list(keys), "kwargs": kwargs})


class FakeSession:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeSession:
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.exited = True


class FakeMongoClient:
    def __init__(self, uri: str) -> None:
        self.uri = uri
        self.closed = False
        self.session = FakeSession()

    def get_database(self, name: str) -> FakeDatabase:
        return FakeDatabase(name)

    def start_session(self) -> FakeSession:
        return self.session

    async def close(self) -> None:
        self.closed = True


FAKE_CLIENT_FACTORY = cast(AsyncMongoClientFactory, FakeMongoClient)


def test_mongo_adapter_creates_client_and_database() -> None:
    settings = Settings(mongodb_uri="mongodb://mongo", mongodb_database="learnloop-test")
    adapter = MongoClientAdapter(settings=settings, client_factory=FAKE_CLIENT_FACTORY)

    client = adapter.get_client()
    database = adapter.get_database()

    assert isinstance(client, FakeMongoClient)
    assert client.uri == "mongodb://mongo"
    assert isinstance(database, FakeDatabase)
    assert database.name == "learnloop-test"


@pytest.mark.asyncio
async def test_mongo_adapter_session_management() -> None:
    adapter = MongoClientAdapter(settings=Settings(), client_factory=FAKE_CLIENT_FACTORY)

    async with adapter.start_session() as session:
        assert isinstance(session, FakeSession)
        assert session.entered is True

    assert adapter.get_client().session.exited is True


@pytest.mark.asyncio
async def test_mongo_adapter_lifecycle_closes_client() -> None:
    adapter = MongoClientAdapter(settings=Settings(), client_factory=FAKE_CLIENT_FACTORY)

    async with adapter.lifecycle() as client:
        assert isinstance(client, FakeMongoClient)
        assert client.closed is False

    assert client.closed is True


def test_module_helpers_return_client_and_database() -> None:
    settings = Settings(mongodb_uri="mongodb://mongo", mongodb_database="helper-db")

    client = get_client(settings)
    database = get_database(settings)

    assert client.options._options["document_class"] is dict
    assert database.name == "helper-db"


@pytest.mark.asyncio
async def test_ensure_database_setup_creates_solution_collections_and_tag_index() -> None:
    database = FakeDatabase("learnloop-test")
    database.collection_names = [TAGS_COLLECTION]
    database.collections[TAGS_COLLECTION] = FakeCollection()

    await ensure_database_setup(database)

    assert database.created_collections == [
        SOLUTION_GENERATION_TASKS_COLLECTION,
        CANONICAL_SOLUTIONS_COLLECTION,
        COACHING_CONVERSATIONS_COLLECTION,
        FOLDERS_COLLECTION,
        INGESTION_BATCHES_COLLECTION,
    ]
    assert database[TAGS_COLLECTION].index_calls == [
        {
            "keys": [("userId", 1), ("name", 1)],
            "kwargs": {"unique": True, "name": "user_tag_unique"},
        }
    ]
    assert database[COACHING_CONVERSATIONS_COLLECTION].index_calls == [
        {
            "keys": [("problem_id", 1), ("user_id", 1)],
            "kwargs": {"unique": True, "name": "problem_user_conversation_unique"},
        }
    ]
    assert database[FOLDERS_COLLECTION].index_calls == [
        {
            "keys": [("userId", 1), ("parentId", 1), ("name", 1)],
            "kwargs": {
                "unique": True,
                "name": "user_parent_folder_unique",
                "collation": {"locale": "en", "strength": 2},
            },
        }
    ]
    assert database[INGESTION_BATCHES_COLLECTION].index_calls == [
        {"keys": list(index["keys"]), "kwargs": {"name": index["name"]}}
        for index in BATCH_INDEXES
    ]
