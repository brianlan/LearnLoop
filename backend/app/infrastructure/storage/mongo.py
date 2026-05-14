from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from app.infrastructure.config.settings import Settings, get_settings

Document = dict[str, Any]
AsyncMongoClientFactory = Callable[[str], AsyncMongoClient[Document]]


class SupportsAsyncClose(Protocol):
    async def close(self) -> None: ...


class MongoClientAdapter:
    def __init__(
        self,
        settings: Settings | None = None,
        client_factory: AsyncMongoClientFactory = AsyncMongoClient,
    ) -> None:
        self._settings = settings or get_settings()
        self._client_factory = client_factory
        self._client: AsyncMongoClient[Document] | None = None

    def get_client(self) -> AsyncMongoClient[Document]:
        if self._client is None:
            self._client = self._client_factory(self._settings.mongodb_uri)
        return self._client

    def get_database(self) -> AsyncDatabase[Document]:
        return self.get_client().get_database(self._settings.mongodb_database)

    @asynccontextmanager
    async def lifecycle(self) -> AsyncIterator[AsyncMongoClient[Document]]:
        client = self.get_client()
        try:
            yield client
        finally:
            await self.aclose()

    @asynccontextmanager
    async def start_session(self) -> AsyncIterator[AsyncClientSession]:
        async with self.get_client().start_session() as session:
            yield session

    async def aclose(self) -> None:
        if self._client is not None:
            await cast(SupportsAsyncClose, self._client).close()
            self._client = None


_default_adapter: MongoClientAdapter | None = None


def get_mongo_adapter(settings: Settings | None = None) -> MongoClientAdapter:
    global _default_adapter

    if settings is None:
        if _default_adapter is None:
            _default_adapter = MongoClientAdapter()
        return _default_adapter

    return MongoClientAdapter(settings=settings)


def get_client(settings: Settings | None = None) -> AsyncMongoClient[Document]:
    return get_mongo_adapter(settings).get_client()


def get_database(settings: Settings | None = None) -> AsyncDatabase[Document]:
    return get_mongo_adapter(settings).get_database()


@asynccontextmanager
async def mongo_client_lifecycle(
    settings: Settings | None = None,
) -> AsyncIterator[AsyncMongoClient[Document]]:
    adapter = get_mongo_adapter(settings)
    async with adapter.lifecycle() as client:
        yield client
