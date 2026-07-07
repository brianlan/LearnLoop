from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.domain.models import CoachingConversation
from app.domain.coaching.service import CoachingService, CoachingError
from app.presentation.deps import (
    CurrentUserDependency,
    DatabaseDependency,
    SettingsDependency,
)
from app.presentation.errors import ApiError

router = APIRouter(prefix="/coaching", tags=["coaching"])

class CoachingMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=5000)

def get_coaching_service(
    database: DatabaseDependency,
    settings: SettingsDependency
) -> CoachingService:
    return CoachingService(database=database, settings=settings, vlm_client=None)

CoachingServiceDependency = Annotated[CoachingService, Depends(get_coaching_service)]


@router.get("/{problem_id}/conversation", response_model=CoachingConversation)
async def get_conversation(
    problem_id: str,
    current_user: CurrentUserDependency,
    service: CoachingServiceDependency,
) -> CoachingConversation:
    # Check if problem belongs to user
    problem = await service.db["problems"].find_one({
        "_id": problem_id if len(problem_id) != 24 else ObjectId(problem_id),
        "userId": current_user["_id"],
        "isDeleted": False
    })
    if not problem:
        raise ApiError(404, "NOT_FOUND", "Problem not found")

    conversation = await service.get_conversation(problem_id, str(current_user["_id"]))
    if not conversation:
        return CoachingConversation(problem_id=problem_id, user_id=str(current_user["_id"]))
    return conversation


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
