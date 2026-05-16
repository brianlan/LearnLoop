from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_wf_auth_1_register_creates_user_without_auto_login(
    client: AsyncClient,
    database: Any,
) -> None:
    payload = {"username": "student1", "password": "secret"}

    register_response = await client.post("/api/v1/auth/register", json=payload)

    assert register_response.status_code == 201
    body = register_response.json()
    assert body["user"]["username"] == "student1"
    assert body["user"]["id"]

    stored_user = await database["users"].find_one({"username": "student1", "status": "active"})
    assert stored_user is not None
    assert stored_user["lastLoginAt"] is None

    me_response = await client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json() == {"authenticated": False}

    duplicate_response = await client.post("/api/v1/auth/register", json=payload)
    assert duplicate_response.status_code == 409
    assert duplicate_response.json() == {
        "error": {"code": "CONFLICT", "message": "Username already exists"}
    }


@pytest.mark.asyncio
async def test_wf_auth_2_login_sets_cookie_me_works_and_wrong_credentials_fail(
    client: AsyncClient,
    database: Any,
) -> None:
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

    stored_user = await database["users"].find_one({"username": "student1"})
    assert stored_user is not None
    assert stored_user["lastLoginAt"] is not None

    me_response = await client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["authenticated"] is True
    assert me_response.json()["user"]["username"] == "student1"

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
async def test_wf_auth_3_logout_clears_cookie_and_me_becomes_unauthenticated(
    client: AsyncClient,
    database: Any,
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"username": "student1", "password": "secret"},
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "student1", "password": "secret"},
    )

    sessions_before_logout = list(database["sessions"]._documents)
    assert len(sessions_before_logout) == 1

    logout_response = await client.post("/api/v1/auth/logout")

    assert logout_response.status_code == 200
    assert logout_response.json() == {"ok": True}
    assert 'll_session=""' in logout_response.headers["set-cookie"]
    assert len(database["sessions"]._documents) == 1
    assert database["sessions"]._documents[0]["invalidatedAt"] is not None

    me_response = await client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json() == {"authenticated": False}
