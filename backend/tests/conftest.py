from __future__ import annotations

from collections.abc import AsyncIterator

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
