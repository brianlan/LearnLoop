from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.config.settings import Settings
from app.main import create_app
from app.presentation.deps import get_app_settings, get_current_user, get_database


class FakeInsertOneResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class FakeDeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class FakeCollection:
    def __init__(self) -> None:
        self._documents: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for document in self._documents:
            if all(document.get(key) == value for key, value in query.items()):
                return deepcopy(document)
        return None

    async def insert_one(self, document: dict[str, Any]) -> FakeInsertOneResult:
        stored_document = deepcopy(document)
        if "_id" not in stored_document:
            stored_document["_id"] = ObjectId()
        self._documents.append(stored_document)
        return FakeInsertOneResult(stored_document["_id"])

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> FakeUpdateResult:
        for document in self._documents:
            if all(document.get(key) == value for key, value in query.items()):
                for key, value in update.get("$set", {}).items():
                    document[key] = value
                return FakeUpdateResult(1)
        return FakeUpdateResult(0)

    async def delete_one(self, query: dict[str, Any]) -> FakeDeleteResult:
        for index, document in enumerate(self._documents):
            if all(document.get(key) == value for key, value in query.items()):
                del self._documents[index]
                return FakeDeleteResult(1)
        return FakeDeleteResult(0)


class FakeDatabase:
    def __init__(self) -> None:
        self._collections = {
            "users": FakeCollection(),
            "sessions": FakeCollection(),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections[name]


@pytest_asyncio.fixture
async def app() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    settings = Settings()

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_app_settings] = lambda: settings
    # Store database on app for tests that need direct access
    application.state.test_database = database

    @application.get("/api/v1/protected")
    async def protected_route(_: dict[str, Any] = Depends(get_current_user)) -> dict[str, bool]:
        return {"ok": True}

    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_register_creates_user_without_auto_login(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": "student1", "password": "secret"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["username"] == "student1"
    assert body["user"]["id"]

    me_response = await client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json() == {"authenticated": False}


@pytest.mark.asyncio
async def test_duplicate_username_is_rejected(client: AsyncClient) -> None:
    payload = {"username": "student1", "password": "secret"}

    first_response = await client.post("/api/v1/auth/register", json=payload)
    second_response = await client.post("/api/v1/auth/register", json=payload)

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert second_response.json() == {
        "error": {"code": "CONFLICT", "message": "Username already exists"}
    }


@pytest.mark.asyncio
async def test_login_sets_session_cookie_and_me_returns_user(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"username": "student1", "password": "secret"},
    )

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"username": "student1", "password": "secret"},
    )

    assert login_response.status_code == 200
    assert login_response.json()["user"]["username"] == "student1"
    set_cookie = login_response.headers["set-cookie"]
    assert "ll_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Secure" not in set_cookie

    me_response = await client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["authenticated"] is True
    assert me_response.json()["user"]["username"] == "student1"


@pytest.mark.asyncio
async def test_login_wrong_username_and_wrong_password_share_same_error(
    client: AsyncClient,
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"username": "student1", "password": "secret"},
    )

    wrong_username_response = await client.post(
        "/api/v1/auth/login",
        json={"username": "missing", "password": "secret"},
    )
    wrong_password_response = await client.post(
        "/api/v1/auth/login",
        json={"username": "student1", "password": "wrong"},
    )

    expected = {
        "error": {
            "code": "UNAUTHENTICATED",
            "message": "Invalid username or password",
        }
    }
    assert wrong_username_response.status_code == 401
    assert wrong_password_response.status_code == 401
    assert wrong_username_response.json() == expected
    assert wrong_password_response.json() == expected


@pytest.mark.asyncio
async def test_logout_invalidates_session_and_clears_cookie(client: AsyncClient, app: FastAPI) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"username": "student1", "password": "secret"},
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "student1", "password": "secret"},
    )

    logout_response = await client.post("/api/v1/auth/logout")

    assert logout_response.status_code == 200
    assert logout_response.json() == {"ok": True}
    assert 'll_session=""' in logout_response.headers["set-cookie"]
    sessions = app.dependency_overrides[get_database]()["sessions"]._documents
    assert len(sessions) == 1
    assert sessions[0]["invalidatedAt"] is not None

    me_response = await client.get("/api/v1/auth/me")
    assert me_response.json() == {"authenticated": False}


@pytest.mark.asyncio
async def test_protected_route_rejects_unauthenticated_access(client: AsyncClient) -> None:
    response = await client.get("/api/v1/protected")

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "UNAUTHENTICATED",
            "message": "Authentication required",
        }
    }


@pytest.mark.asyncio
async def test_register_sets_teacher_password_hash(client: AsyncClient, app: FastAPI) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": "student1", "password": "secret"},
    )

    assert response.status_code == 201

    # Get the user from the fake database
    users = app.state.test_database["users"]._documents
    assert len(users) == 1
    user = users[0]
    assert "teacherPasswordHash" in user
    assert user["teacherPasswordHash"] is not None
    # Verify it looks like a bcrypt hash (starts with $2b$ or $2a$)
    assert user["teacherPasswordHash"].startswith("$2")
