from collections.abc import AsyncIterator
from typing import Annotated, Any

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
from app.infrastructure.storage.mongo import (
    Document,
    MongoClientAdapter,
    get_database as get_mongo_database,
    get_mongo_adapter as get_mongo_adapter_infra,
)
from app.infrastructure.storage.s3 import S3StorageAdapter
from app.infrastructure.vlm.client import VLMClient
from app.infrastructure.vlm.prompts import ENGLISH_EXTRACTION_SYSTEM_PROMPT
from app.presentation.errors import ApiError


def get_database() -> AsyncDatabase[Document]:
    return get_mongo_database()


DatabaseDependency = Annotated[AsyncDatabase[Document], Depends(get_database)]


def get_mongo_adapter() -> MongoClientAdapter:
    return get_mongo_adapter_infra()


AdapterDependency = Annotated[MongoClientAdapter, Depends(get_mongo_adapter)]


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


def get_s3_storage(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> S3StorageAdapter:
    return S3StorageAdapter(settings=settings)


def create_helper_vlm_client(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> VLMClient:
    return VLMClient(
        endpoint=settings.helper_vlm_endpoint,
        model=settings.helper_vlm_model,
        api_key=settings.helper_vlm_api_key,
        timeout_seconds=settings.helper_vlm_timeout_seconds,
        provider=settings.helper_vlm_provider,
        api_mode=settings.helper_vlm_api_mode,
    )


def create_math_ingestion_vlm_client(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> VLMClient:
    return VLMClient(
        endpoint=settings.math_ingestion_vlm_endpoint,
        model=settings.math_ingestion_vlm_model,
        api_key=settings.math_ingestion_vlm_api_key,
        timeout_seconds=settings.math_ingestion_vlm_timeout_seconds,
        provider=settings.math_ingestion_vlm_provider,
        api_mode=settings.math_ingestion_vlm_api_mode,
    )


def create_english_ingestion_vlm_client(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> VLMClient:
    return VLMClient(
        endpoint=settings.english_ingestion_vlm_endpoint,
        model=settings.english_ingestion_vlm_model,
        api_key=settings.english_ingestion_vlm_api_key,
        timeout_seconds=settings.english_ingestion_vlm_timeout_seconds,
        provider=settings.english_ingestion_vlm_provider,
        api_mode=settings.english_ingestion_vlm_api_mode,
        extraction_system_prompt=ENGLISH_EXTRACTION_SYSTEM_PROMPT,
        request_correct_answer=True,
    )


async def get_grading_vlm_client(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AsyncIterator[VLMClient]:
    client = VLMClient(
        endpoint=settings.grading_vlm_endpoint,
        model=settings.grading_vlm_model,
        api_key=settings.grading_vlm_api_key,
        timeout_seconds=settings.grading_vlm_timeout_seconds,
        provider=settings.grading_vlm_provider,
        api_mode=settings.grading_vlm_api_mode,
    )
    try:
        yield client
    finally:
        await client.aclose()


StorageDependency = Annotated[S3StorageAdapter, Depends(get_s3_storage)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]
HelperVLMDependency = Annotated[VLMClient, Depends(create_helper_vlm_client)]
MathIngestionVLMDependency = Annotated[VLMClient, Depends(create_math_ingestion_vlm_client)]
EnglishIngestionVLMDependency = Annotated[VLMClient, Depends(create_english_ingestion_vlm_client)]
GradingVLMDependency = Annotated[VLMClient, Depends(get_grading_vlm_client)]
