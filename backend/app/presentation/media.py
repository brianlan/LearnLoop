from __future__ import annotations

from io import BytesIO
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.infrastructure.storage.s3 import StorageObjectNotFoundError
from app.presentation.deps import DatabaseDependency, StorageDependency, get_current_user
from app.presentation.errors import ApiError
from app.presentation.helpers import get_owned_problem

router = APIRouter(tags=["media"])

CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]


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
    bucket = source_image.get("bucket")
    object_key = source_image.get("objectKey")
    if not bucket or not object_key:
        raise ApiError(404, "NOT_FOUND", "Problem image not found")

    try:
        image_bytes = storage.get_object(str(bucket), str(object_key))
    except StorageObjectNotFoundError as exc:
        raise ApiError(404, "NOT_FOUND", "Problem image not found") from exc

    return StreamingResponse(
        BytesIO(image_bytes),
        media_type=str(source_image.get("contentType") or "application/octet-stream"),
    )
