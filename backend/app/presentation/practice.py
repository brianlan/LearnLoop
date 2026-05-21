from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.domain.practice_selection import (
    PracticeSelectionConfig,
    select_practice_problem,
)
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.storage.mongo import Document
from app.presentation.deps import DatabaseDependency, get_current_user, get_app_settings
from app.presentation.helpers import build_problem_image_url
from app.presentation.exam_helpers import problem_document_to_model

router = APIRouter(prefix="/practice", tags=["practice"])


CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]


class PracticeNextResponse(BaseModel):
    status: str
    problem: dict[str, Any] | None = None


@router.post("/next", response_model=PracticeNextResponse)
async def get_next_practice_problem(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    settings: SettingsDependency,
) -> PracticeNextResponse:
    problem_documents = await database["problems"].find(
        {"userId": current_user["_id"], "isDeleted": False}
    ).to_list(length=None)

    eligible_documents = [
        problem
        for problem in problem_documents
        if problem.get("correctAnswer")
        and str(problem.get("correctAnswer", {}).get("display", "")).strip()
    ]

    if not eligible_documents:
        return PracticeNextResponse(status="no_problems")

    config = PracticeSelectionConfig(
        cooldown_days=settings.practice_cooldown_days,
        last_wrong_weight=settings.practice_last_wrong_weight,
        failure_rate_weight=settings.practice_failure_rate_weight,
        recency_weight=settings.practice_recency_weight,
    )

    problem_models = [problem_document_to_model(p) for p in eligible_documents]
    now = datetime.now(UTC)

    result = select_practice_problem(problem_models, config, now)

    if result.status != "ok" or result.selected_problem is None:
        return PracticeNextResponse(status=result.status)

    selected_id = result.selected_problem.id
    document_by_id = {str(p["_id"]): p for p in eligible_documents}
    selected_document = document_by_id.get(selected_id)

    if selected_document is None:
        return PracticeNextResponse(status="no_problems")

    tracking = selected_document.get("tracking", {})
    tracking["exposureCount"] = tracking.get("exposureCount", 0) + 1
    tracking["lastTestedAt"] = now

    await database["problems"].update_one(
        {"_id": selected_document["_id"]},
        {"$set": {"tracking": tracking, "updatedAt": now}},
    )

    problem_response = {
        "id": str(selected_document["_id"]),
        "text": selected_document["text"],
        "type": selected_document["problemType"],
    }

    if selected_document.get("sourceImage"):
        problem_response["imageUrl"] = build_problem_image_url(selected_document["_id"])

    return PracticeNextResponse(status="ok", problem=problem_response)
