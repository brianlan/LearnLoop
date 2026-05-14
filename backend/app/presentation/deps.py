from typing import Any

from fastapi import Depends, Request, Response
from pymongo.asynchronous.database import AsyncDatabase

from app.infrastructure.auth.session import (
    SESSION_TTL,
    delete_session,
    ensure_utc,
    extend_session,
    get_session_by_token,
    utc_now,
)
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.storage.mongo import Document, get_database as get_mongo_database
from app.presentation.errors import ApiError


def get_database() -> AsyncDatabase[Document]:
    return get_mongo_database()


def get_app_settings() -> Settings:
    return get_settings()


def serialize_user(user: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(user["_id"]),
        "username": str(user["username"]),
    }


def set_session_cookie(
    response: Response,
    token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_secure,
        samesite=settings.session_samesite,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value="",
        httponly=True,
        secure=settings.session_secure,
        samesite=settings.session_samesite,
        max_age=0,
        expires=0,
        path="/",
    )


async def resolve_current_user(
    request: Request,
    response: Response,
    database: AsyncDatabase[Document],
    settings: Settings,
) -> dict[str, Any] | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None

    session = await get_session_by_token(database, token)
    if session is None:
        clear_session_cookie(response, settings)
        return None

    now = utc_now()
    if ensure_utc(session["expiresAt"]) <= now:
        await delete_session(database, token)
        clear_session_cookie(response, settings)
        return None

    user = await database["users"].find_one(
        {"_id": session["userId"], "status": "active"}
    )
    if user is None:
        await delete_session(database, token)
        clear_session_cookie(response, settings)
        return None

    await extend_session(database, token, now=now)
    set_session_cookie(response, token, settings)
    return user


async def get_current_user(
    request: Request,
    response: Response,
    database: AsyncDatabase[Document] = Depends(get_database),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    user = await resolve_current_user(request, response, database, settings)
    if user is None:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")
    return user
