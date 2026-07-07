from __future__ import annotations

from datetime import UTC, datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import BulkWriteError

from app.infrastructure.storage.mongo import Document
from app.presentation.helpers import normalize_tags


async def _register_tags(database: AsyncDatabase[Document], user_id: ObjectId, tags: list[str]) -> None:
    """Auto-register any new tags that don't already exist for the user.

    Uses insert_many with ordered=False so that duplicate-key errors from
    concurrent requests are silently skipped rather than aborting the batch.
    """
    normalized = normalize_tags(tags)
    if not normalized:
        return
    existing = await database["tags"].find(
        {"userId": user_id, "name": {"$in": normalized}}
    ).to_list(length=None)
    existing_names = {tag["name"] for tag in existing}
    now = datetime.now(UTC)
    new_tags = [
        {"_id": ObjectId(), "userId": user_id, "name": name, "createdAt": now, "updatedAt": now}
        for name in normalized
        if name not in existing_names
    ]
    if new_tags:
        try:
            await database["tags"].insert_many(new_tags, ordered=False)
        except BulkWriteError:
            pass
