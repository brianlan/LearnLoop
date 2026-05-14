from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest

from app.infrastructure.config.settings import Settings
from app.infrastructure.storage.mongo import (
    AsyncMongoClientFactory,
    MongoClientAdapter,
    get_client,
    get_database,
)


class FakeDatabase:
    def __init__(self, name: str) -> None:
        self.name = name


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
