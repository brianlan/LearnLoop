from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

from pymongo import ASCENDING
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from app.infrastructure.config.settings import Settings, get_settings

Document = dict[str, Any]
AsyncMongoClientFactory = Callable[[str], AsyncMongoClient[Document]]

TAGS_COLLECTION = "tags"
SOLUTION_GENERATION_TASKS_COLLECTION = "solution_generation_tasks"
CANONICAL_SOLUTIONS_COLLECTION = "canonical_solutions"
COACHING_CONVERSATIONS_COLLECTION = "coaching_conversations"
FOLDERS_COLLECTION = "folders"


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


async def ensure_database_setup(database: AsyncDatabase[Document]) -> None:
    try:
        existing_collections = set(await database.list_collection_names())
    except AttributeError:
        existing_collections = set()

    for collection_name in (
        SOLUTION_GENERATION_TASKS_COLLECTION,
        CANONICAL_SOLUTIONS_COLLECTION,
        COACHING_CONVERSATIONS_COLLECTION,
        FOLDERS_COLLECTION,
    ):
        if collection_name not in existing_collections and hasattr(database, "create_collection"):
            await database.create_collection(collection_name)

    create_index = getattr(database[TAGS_COLLECTION], "create_index", None)
    if callable(create_index):
        await create_index(
            [("userId", ASCENDING), ("name", ASCENDING)],
            unique=True,
            name="user_tag_unique",
        )

    create_coaching_index = getattr(database[COACHING_CONVERSATIONS_COLLECTION], "create_index", None)
    if callable(create_coaching_index):
        await create_coaching_index(
            [("problem_id", ASCENDING), ("user_id", ASCENDING)],
            unique=True,
            name="problem_user_conversation_unique",
        )

    # Folder indexes: user lookup and sibling uniqueness (case-insensitive via collation)
    create_folder_user_parent_index = getattr(database[FOLDERS_COLLECTION], "create_index", None)
    if callable(create_folder_user_parent_index):
        await create_folder_user_parent_index(
            [("userId", ASCENDING), ("parentId", ASCENDING), ("name", ASCENDING)],
            unique=True,
            name="user_parent_folder_unique",
            collation={"locale": "en", "strength": 2},  # case-insensitive
        )


@asynccontextmanager
async def mongo_client_lifecycle(
    settings: Settings | None = None,
) -> AsyncIterator[AsyncMongoClient[Document]]:
    adapter = get_mongo_adapter(settings)
    async with adapter.lifecycle() as client:
        yield client
