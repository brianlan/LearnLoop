from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.auth.password import hash_password
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


@pytest_asyncio.fixture
async def authenticated_client(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    # Register and login a user
    await client.post(
        "/api/v1/auth/register",
        json={"username": "student1", "password": "secret"},
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "student1", "password": "secret"},
    )
    yield client


@pytest.mark.asyncio
async def test_verify_teacher_password_success(authenticated_client: AsyncClient) -> None:
    response = await authenticated_client.post(
        "/api/v1/teacher-password/verify",
        json={"password": "default-teacher-password"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_verify_teacher_password_incorrect(authenticated_client: AsyncClient) -> None:
    response = await authenticated_client.post(
        "/api/v1/teacher-password/verify",
        json={"password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHENTICATED", "message": "Incorrect teacher password"}
    }


@pytest.mark.asyncio
async def test_change_teacher_password_success(
    authenticated_client: AsyncClient, app: FastAPI
) -> None:
    # First change the password
    change_response = await authenticated_client.post(
        "/api/v1/teacher-password/change",
        json={
            "current_password": "default-teacher-password",
            "new_password": "new-teacher-password",
            "confirm_password": "new-teacher-password",
        },
    )
    assert change_response.status_code == 200
    assert change_response.json() == {"ok": True}

    # Verify the new password works
    verify_response = await authenticated_client.post(
        "/api/v1/teacher-password/verify",
        json={"password": "new-teacher-password"},
    )
    assert verify_response.status_code == 200
    assert verify_response.json() == {"ok": True}

    # Verify the old password no longer works
    old_verify_response = await authenticated_client.post(
        "/api/v1/teacher-password/verify",
        json={"password": "default-teacher-password"},
    )
    assert old_verify_response.status_code == 401


@pytest.mark.asyncio
async def test_change_teacher_password_incorrect_current(authenticated_client: AsyncClient) -> None:
    response = await authenticated_client.post(
        "/api/v1/teacher-password/change",
        json={
            "current_password": "wrong-password",
            "new_password": "new-teacher-password",
            "confirm_password": "new-teacher-password",
        },
    )
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHENTICATED", "message": "Incorrect teacher password"}
    }


@pytest.mark.asyncio
async def test_change_teacher_password_mismatched_confirm(authenticated_client: AsyncClient) -> None:
    response = await authenticated_client.post(
        "/api/v1/teacher-password/change",
        json={
            "current_password": "default-teacher-password",
            "new_password": "new-teacher-password",
            "confirm_password": "different-password",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_verify_teacher_password_lazy_migration(app: FastAPI, client: AsyncClient) -> None:
    """Test that a user without teacherPasswordHash can verify with the default password."""
    # Create a user WITHOUT teacherPasswordHash (simulating pre-migration user)
    database = app.state.test_database
    from datetime import UTC, datetime

    user_doc = {
        "_id": ObjectId(),
        "username": "legacy_user",
        "passwordHash": hash_password("user_password"),
        "createdAt": datetime.now(UTC),
        "updatedAt": datetime.now(UTC),
        "lastLoginAt": None,
        "status": "active",
    }
    await database["users"].insert_one(user_doc)

    # Login as this user
    await client.post(
        "/api/v1/auth/login",
        json={"username": "legacy_user", "password": "user_password"},
    )

    # Verify default teacher password works (lazy migration assigns it)
    response = await client.post(
        "/api/v1/teacher-password/verify",
        json={"password": "default-teacher-password"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # Verify the user now has teacherPasswordHash assigned
    updated_user = await database["users"].find_one({"username": "legacy_user"})
    assert "teacherPasswordHash" in updated_user
    assert updated_user["teacherPasswordHash"] is not None
