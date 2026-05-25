from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.domain.models import CoachingConversation
from app.domain.coaching.service import CoachingService, CoachingError
from app.infrastructure.llm.client import CoachingLLMClient
from app.infrastructure.config.settings import Settings, get_settings
from app.presentation.deps import (
    DatabaseDependency,
    get_current_user,
    get_app_settings,
)
from app.presentation.errors import ApiError

router = APIRouter(prefix="/coaching", tags=["coaching"])

CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]

class CoachingMessageRequest(BaseModel):
    message: str

def get_coaching_client(settings: SettingsDependency) -> CoachingLLMClient:
    return CoachingLLMClient(settings=settings)

CoachingLLMDependency = Annotated[CoachingLLMClient, Depends(get_coaching_client)]

def get_coaching_service(
    database: DatabaseDependency,
    llm_client: CoachingLLMDependency
) -> CoachingService:
    return CoachingService(database=database, llm_client=llm_client)

CoachingServiceDependency = Annotated[CoachingService, Depends(get_coaching_service)]


@router.get("/{problem_id}/conversation", response_model=CoachingConversation)
async def get_conversation(
    problem_id: str,
    current_user: CurrentUserDependency,
    service: CoachingServiceDependency,
) -> CoachingConversation:
    try:
        # Check if problem belongs to user
        problem = await service.db["problems"].find_one({
            "_id": problem_id if len(problem_id) != 24 else __import__("bson").ObjectId(problem_id),
            "userId": current_user["_id"],
            "isDeleted": False
        })
        if not problem:
            raise ApiError(404, "NOT_FOUND", "Problem not found")

        conversation = await service.get_conversation(problem_id, str(current_user["_id"]))
        if not conversation:
            from app.domain.models import CoachingConversation as CC
            return CC(problem_id=problem_id, user_id=str(current_user["_id"]))
        return conversation
    except CoachingError as exc:
        raise ApiError(exc.status_code, exc.code, str(exc))


@router.post("/{problem_id}/messages", response_model=CoachingConversation)
async def send_message(
    problem_id: str,
    payload: CoachingMessageRequest,
    current_user: CurrentUserDependency,
    service: CoachingServiceDependency,
) -> CoachingConversation:
    try:
        return await service.send_message(
            problem_id=problem_id,
            user_id=str(current_user["_id"]),
            message=payload.message
        )
    except CoachingError as exc:
        raise ApiError(exc.status_code, exc.code, str(exc))


@router.delete("/{problem_id}/conversation", status_code=204)
async def clear_conversation(
    problem_id: str,
    current_user: CurrentUserDependency,
    service: CoachingServiceDependency,
) -> None:
    try:
        await service.clear_conversation(problem_id, str(current_user["_id"]))
    except CoachingError as exc:
        raise ApiError(exc.status_code, exc.code, str(exc))
