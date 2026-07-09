from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.presentation.deps import CurrentUserDependency, DatabaseDependency, StorageDependency
from app.presentation.helpers import get_owned_problem, stream_storage_metadata

router = APIRouter(tags=["media"])


@router.get("/problems/{problem_id}/image")
async def stream_problem_image(
    problem_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    storage: StorageDependency,
) -> StreamingResponse:
    problem = await get_owned_problem(
        database,
        problem_id,
        current_user["_id"],
        allow_deleted=False,
    )
    source_image = dict(problem.get("sourceImage") or {})
    return stream_storage_metadata(
        source_image,
        storage,
        missing_metadata_code="NOT_FOUND",
        missing_metadata_message="Problem image not found",
    )
