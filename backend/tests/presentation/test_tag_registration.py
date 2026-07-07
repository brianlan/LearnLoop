from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId
from pymongo.errors import BulkWriteError

from app.presentation.tag_registration import _register_tags
from tests.conftest import FakeDatabase


def _make_tag(user_id: ObjectId, name: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "name": name,
        "createdAt": now,
        "updatedAt": now,
    }


@pytest.mark.asyncio
async def test_register_tags_empty_input_inserts_nothing() -> None:
    database = FakeDatabase()
    user_id = ObjectId()

    await _register_tags(database, user_id, [])
    assert len(database["tags"]._documents) == 0

    await _register_tags(database, user_id, ["   ", ""])
    assert len(database["tags"]._documents) == 0


@pytest.mark.asyncio
async def test_register_tags_skips_existing_tags() -> None:
    database = FakeDatabase()
    user_id = ObjectId()
    database["tags"].seed(_make_tag(user_id, "algebra"))

    await _register_tags(database, user_id, ["algebra", "geometry"])

    tags = database["tags"]._documents
    assert len(tags) == 2
    names = {t["name"] for t in tags}
    assert names == {"algebra", "geometry"}


@pytest.mark.asyncio
async def test_register_tags_all_duplicates_is_noop() -> None:
    database = FakeDatabase()
    user_id = ObjectId()
    database["tags"].seed(_make_tag(user_id, "alpha"))
    database["tags"].seed(_make_tag(user_id, "beta"))

    await _register_tags(database, user_id, ["alpha", "beta"])

    assert len(database["tags"]._documents) == 2


@pytest.mark.asyncio
async def test_register_tags_inserts_new_tags_with_correct_fields() -> None:
    database = FakeDatabase()
    user_id = ObjectId()

    await _register_tags(database, user_id, ["calculus", "algebra"])

    tags = database["tags"]._documents
    assert len(tags) == 2
    for tag in tags:
        assert tag["userId"] == user_id
        assert tag["name"] in {"calculus", "algebra"}
        assert "_id" in tag
        assert "createdAt" in tag
        assert "updatedAt" in tag
        assert tag["createdAt"] == tag["updatedAt"]


@pytest.mark.asyncio
async def test_register_tags_normalizes_input() -> None:
    database = FakeDatabase()
    user_id = ObjectId()

    await _register_tags(database, user_id, ["  Algebra  ", "Algebra", "  Geometry  "])

    tags = database["tags"]._documents
    assert len(tags) == 2
    names = {t["name"] for t in tags}
    assert names == {"Algebra", "Geometry"}


@pytest.mark.asyncio
async def test_register_tags_tolerates_bulk_write_error() -> None:
    database = FakeDatabase()
    user_id = ObjectId()
    mock_insert = AsyncMock(side_effect=BulkWriteError({}))

    with patch.object(database["tags"], "insert_many", new=mock_insert):
        await _register_tags(database, user_id, ["new-tag"])

    mock_insert.assert_called_once()
    _, kwargs = mock_insert.call_args
    assert kwargs["ordered"] is False
    assert len(database["tags"]._documents) == 0
