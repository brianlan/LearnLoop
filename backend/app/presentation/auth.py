from datetime import UTC, datetime
from typing import Annotated, Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.infrastructure.auth.password import hash_password, verify_password
from app.infrastructure.auth.session import create_session, delete_session
from app.infrastructure.config.settings import Settings
from app.observability import log_auth_event
from app.presentation.deps import (
    clear_session_cookie,
    get_app_settings,
    get_database,
    resolve_current_user,
    serialize_user,
    set_session_cookie,
)
from app.presentation.errors import ApiError

router = APIRouter(prefix="/auth", tags=["auth"])


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class UserPayload(BaseModel):
    id: str
    username: str


class UserResponse(BaseModel):
    user: UserPayload


class LogoutResponse(BaseModel):
    ok: bool


class AuthenticatedMeResponse(BaseModel):
    authenticated: Literal[True]
    user: UserPayload


class UnauthenticatedMeResponse(BaseModel):
    authenticated: Literal[False]


def normalize_username(username: str) -> str:
    return username.strip()


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    payload: CredentialsRequest,
    database=Depends(get_database),
) -> UserResponse:
    username = normalize_username(payload.username)
    existing_user = await database["users"].find_one({"username": username})
    if existing_user is not None:
        log_auth_event("register_failure", username=username, reason="duplicate_username")
        raise ApiError(409, "CONFLICT", "Username already exists")

    now = datetime.now(UTC)
    user = {
        "_id": ObjectId(),
        "username": username,
        "passwordHash": hash_password(payload.password),
        "createdAt": now,
        "updatedAt": now,
        "lastLoginAt": None,
        "status": "active",
    }

    try:
        await database["users"].insert_one(user)
    except DuplicateKeyError as exc:
        log_auth_event("register_failure", username=username, reason="duplicate_username")
        raise ApiError(409, "CONFLICT", "Username already exists") from exc

    log_auth_event("register_success", username=username, user_id=str(user["_id"]))
    return UserResponse(user=UserPayload.model_validate(serialize_user(user)))


@router.post("/login", response_model=UserResponse)
async def login(
    payload: CredentialsRequest,
    request: Request,
    response: Response,
    database=Depends(get_database),
    settings: Settings = Depends(get_app_settings),
) -> UserResponse:
    username = normalize_username(payload.username)
    user = await database["users"].find_one({"username": username, "status": "active"})
    invalid_credentials = user is None or not verify_password(
        payload.password, str(user["passwordHash"]) if user is not None else ""
    )
    if invalid_credentials:
        log_auth_event("login_failure", username=username, reason="invalid_credentials")
        raise ApiError(401, "UNAUTHENTICATED", "Invalid username or password")

    session = await create_session(
        database,
        user_id=user["_id"],
        client_ip=request.client.host if request.client is not None else None,
        user_agent=request.headers.get("user-agent"),
    )
    now = datetime.now(UTC)
    await database["users"].update_one(
        {"_id": user["_id"]},
        {"$set": {"lastLoginAt": now, "updatedAt": now}},
    )

    set_session_cookie(response, session["token"], settings)
    log_auth_event("login_success", username=username, user_id=str(user["_id"]))
    return UserResponse(user=UserPayload.model_validate(serialize_user(user)))


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    database=Depends(get_database),
    settings: Settings = Depends(get_app_settings),
) -> LogoutResponse:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await delete_session(database, token)
    clear_session_cookie(response, settings)
    log_auth_event("logout", had_session=token is not None)
    return LogoutResponse(ok=True)


@router.get("/me", response_model=AuthenticatedMeResponse | UnauthenticatedMeResponse)
async def me(
    request: Request,
    response: Response,
    database=Depends(get_database),
    settings: Settings = Depends(get_app_settings),
) -> AuthenticatedMeResponse | UnauthenticatedMeResponse:
    user = await resolve_current_user(request, response, database, settings)
    if user is None:
        return UnauthenticatedMeResponse(authenticated=False)

    return AuthenticatedMeResponse(
        authenticated=True,
        user=UserPayload.model_validate(serialize_user(user)),
    )
