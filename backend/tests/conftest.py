from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.storage.s3 import StorageObjectNotFoundError
from app.main import create_app
from tests.test_utils.db_fakes import (
    FakeCollection as FakeCollection,
    FakeCursor as FakeCursor,
    FakeDatabase as FakeDatabase,
    matches_query as matches_query,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-real-mongo",
        action="store_true",
        default=False,
        help="Fail the session when zero real_mongo tests are selected or any selected real_mongo test skips.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config._require_real_mongo_enabled = config.getoption("--require-real-mongo", default=False)
    config._require_real_mongo_selected: list[pytest.Item] = []
    config._require_real_mongo_skip_seen = False


@pytest.hookimpl
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config._require_real_mongo_enabled:
        return
    config._require_real_mongo_selected = [
        item for item in items if item.get_closest_marker("real_mongo") is not None
    ]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call):
    outcome = yield
    config = item.config
    if not config._require_real_mongo_enabled:
        return
    report = outcome.get_result()
    if item.get_closest_marker("real_mongo") is not None and report.when == "call" and report.skipped:
        config._require_real_mongo_skip_seen = True


@pytest.hookimpl
def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    if not config._require_real_mongo_enabled:
        return
    if not config._require_real_mongo_selected:
        session.exitstatus = 2
        print("\n--require-real-mongo: no real_mongo tests were selected")
    elif config._require_real_mongo_skip_seen:
        session.exitstatus = 2
        print("\n--require-real-mongo: a selected real_mongo test was skipped")


class FakeStorage:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[tuple[str, str, str | None, bytes]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []
        self._counter = 0

    def build_object_key(
        self, user_id: str, extension: str, *, category: str = "images"
    ) -> str:
        self._counter += 1
        return f"users/{user_id}/{category}/preview-{self._counter}{extension}"

    def put_object(self, bucket: str, object_key: str, payload: bytes, content_type: str | None) -> None:
        self._objects[(bucket, object_key)] = payload
        self.put_calls.append((bucket, object_key, content_type, payload))

    def get_object(self, bucket: str, object_key: str) -> bytes:
        self.get_calls.append((bucket, object_key))
        payload = self._objects.get((bucket, object_key))
        if payload is None:
            raise StorageObjectNotFoundError(object_key)
        return payload

    def delete_object(self, bucket: str, object_key: str) -> None:
        self.delete_calls.append((bucket, object_key))
        self._objects.pop((bucket, object_key), None)

    def seed(self, bucket: str, object_key: str, payload: bytes) -> None:
        self._objects[(bucket, object_key)] = payload


@pytest_asyncio.fixture
async def app() -> FastAPI:
    return create_app()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
