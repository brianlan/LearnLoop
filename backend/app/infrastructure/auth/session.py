from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.domain.selection import ensure_utc

Document = dict[str, Any]
SESSION_TTL = timedelta(hours=24)


def utc_now() -> datetime:
    return datetime.now(UTC)


def generate_session_token() -> str:
    return token_urlsafe(32)


async def create_session(
    database: AsyncDatabase[Document],
    *,
    user_id: Any,
    token: str | None = None,
    now: datetime | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> Document:
    issued_at = ensure_utc(now or utc_now())
    session_token = token or generate_session_token()
    expires_at = issued_at + SESSION_TTL
    document: Document = {
        "_id": session_token,
        "userId": user_id,
        "token": session_token,
        "createdAt": issued_at,
        "expiresAt": expires_at,
        "lastSeenAt": issued_at,
        "invalidatedAt": None,
        "clientMeta": {
            "ip": client_ip,
            "userAgent": user_agent,
        },
    }
    await database["sessions"].insert_one(document)
    return document


async def get_session_by_token(
    database: AsyncDatabase[Document],
    token: str,
) -> Document | None:
    return await database["sessions"].find_one({"token": token})


async def extend_session(
    database: AsyncDatabase[Document],
    token: str,
    *,
    now: datetime | None = None,
) -> Document | None:
    refreshed_at = ensure_utc(now or utc_now())
    expires_at = refreshed_at + SESSION_TTL
    await database["sessions"].update_one(
        {"token": token},
        {
            "$set": {
                "expiresAt": expires_at,
                "lastSeenAt": refreshed_at,
            }
        },
    )
    return await get_session_by_token(database, token)


async def delete_session(database: AsyncDatabase[Document], token: str) -> None:
    await database["sessions"].update_one(
        {"token": token},
        {"$set": {"invalidatedAt": utc_now()}},
    )
