from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, cast

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.presentation.deps import get_current_user, get_database
from app.presentation.errors import ApiError


class FakeInsertOneResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class FakeDeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = [deepcopy(document) for document in documents]

    def sort(self, field: str, direction: int) -> FakeCursor:
        reverse = direction < 0
        self._documents.sort(
            key=lambda document: cast(Any, document.get(field, "")),
            reverse=reverse,
        )
        return self

    def limit(self, amount: int) -> FakeCursor:
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return [deepcopy(document) for document in self._documents]


class FakeCollection:
    def __init__(self) -> None:
        self._documents: list[dict[str, Any]] = []

    def seed(self, *documents: dict[str, Any]) -> None:
        self._documents.extend(deepcopy(list(documents)))

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for document in self._documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

    async def insert_one(self, document: dict[str, Any]) -> FakeInsertOneResult:
        stored = deepcopy(document)
        if "_id" not in stored:
            stored["_id"] = ObjectId()
        self._documents.append(stored)
        return FakeInsertOneResult(stored["_id"])

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> FakeUpdateResult:
        for document in self._documents:
            if _matches(document, query):
                for key, value in update.get("$set", {}).items():
                    document[key] = deepcopy(value)
                return FakeUpdateResult(1)
        return FakeUpdateResult(0)

    async def delete_one(self, query: dict[str, Any]) -> FakeDeleteResult:
        for i, document in enumerate(self._documents):
            if _matches(document, query):
                del self._documents[i]
                return FakeDeleteResult(1)
        return FakeDeleteResult(0)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for document in self._documents if _matches(document, query))

    def find(self, query: dict[str, Any], projection: dict[str, Any] | None = None) -> FakeCursor:
        matches = [document for document in self._documents if _matches(document, query)]
        return FakeCursor(matches)


class FakeDatabase:
    def __init__(self) -> None:
        self._collections = {
            "problems": FakeCollection(),
            "tags": FakeCollection(),
            "folders": FakeCollection(),
            "users": FakeCollection(),
            "sessions": FakeCollection(),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections[name]


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        actual = document.get(key)
        if isinstance(value, dict):
            if "$in" in value:
                if actual not in value["$in"]:
                    return False
                continue
            if "$ne" in value:
                if actual == value["$ne"]:
                    return False
                continue
            if "$regex" in value:
                import re
                pattern = value["$regex"]
                options = value.get("$options", "")
                flags = re.IGNORECASE if "i" in options else 0
                if not re.search(pattern, str(actual or ""), flags):
                    return False
                continue
        if isinstance(actual, list):
            if value not in actual:
                return False
            continue
        if actual != value:
            return False
    return True


def make_user(user_id: ObjectId, username: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": user_id,
        "username": username,
        "status": "active",
        "createdAt": now,
        "updatedAt": now,
        "lastLoginAt": None,
    }


def make_folder(
    user_id: ObjectId,
    name: str,
    *,
    parent_id: ObjectId | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "name": name,
        "parentId": parent_id,
        "createdAt": now,
        "updatedAt": now,
    }


def make_problem(
    user_id: ObjectId,
    *,
    text: str = "What is 2+2?",
    folder_id: str | None = None,
    is_deleted: bool = False,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": text,
        "problemType": "short-answer",
        "graphDsl": None,
        "correctAnswer": {
            "display": "4",
            "normalizedText": "4",
            "normalizedSet": [],
            "format": "single",
        },
        "tags": ["math"],
        "folderId": folder_id,
        "isDeleted": is_deleted,
        "deletedAt": now if is_deleted else None,
        "createdAt": now,
        "updatedAt": now,
    }


@pytest_asyncio.fixture
async def folders_app() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    primary_user = make_user(ObjectId(), "student1")
    secondary_user = make_user(ObjectId(), "student2")

    application.state.fake_database = database
    application.state.primary_user = primary_user
    application.state.secondary_user = secondary_user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_current_user] = lambda: deepcopy(primary_user)
    return application


@pytest_asyncio.fixture
async def client(folders_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=folders_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


# Tests


@pytest.mark.asyncio
async def test_create_root_folder(folders_app: FastAPI, client: AsyncClient) -> None:
    response = await client.post("/api/v1/folders", json={"name": "Chapter 1"})
    assert response.status_code == 201
    body = response.json()["folder"]
    assert body["name"] == "Chapter 1"
    assert body["parentId"] is None


@pytest.mark.asyncio
async def test_create_child_folder(folders_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    parent = make_folder(user_id, "Chapter 1")
    database["folders"].seed(parent)

    response = await client.post(
        "/api/v1/folders",
        json={"name": "Section 1.1", "parentId": str(parent["_id"])},
    )
    assert response.status_code == 201
    body = response.json()["folder"]
    assert body["name"] == "Section 1.1"
    assert body["parentId"] == str(parent["_id"])


@pytest.mark.asyncio
async def test_reject_empty_folder_name(folders_app: FastAPI, client: AsyncClient) -> None:
    response = await client.post("/api/v1/folders", json={"name": ""})
    assert response.status_code == 422  # validation error


@pytest.mark.asyncio
async def test_reject_whitespace_only_name(folders_app: FastAPI, client: AsyncClient) -> None:
    response = await client.post("/api/v1/folders", json={"name": "   "})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_reject_over_200_char_name(folders_app: FastAPI, client: AsyncClient) -> None:
    long_name = "x" * 201
    response = await client.post("/api/v1/folders", json={"name": long_name})
    assert response.status_code == 422  # validation error


@pytest.mark.asyncio
async def test_reject_duplicate_sibling_name_case_insensitive(
    folders_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    parent = make_folder(user_id, "Chapter 1")
    child = make_folder(user_id, "Section 1", parent_id=parent["_id"])
    database["folders"].seed(parent, child)

    response = await client.post(
        "/api/v1/folders",
        json={"name": "SECTION 1", "parentId": str(parent["_id"])},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_FOLDER_NAME"


@pytest.mark.asyncio
async def test_allow_same_name_under_different_parents(
    folders_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    parent1 = make_folder(user_id, "Chapter 1")
    parent2 = make_folder(user_id, "Chapter 2")
    child1 = make_folder(user_id, "Section 1", parent_id=parent1["_id"])
    database["folders"].seed(parent1, parent2, child1)

    response = await client.post(
        "/api/v1/folders",
        json={"name": "Section 1", "parentId": str(parent2["_id"])},
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_rename_folder(folders_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    database["folders"].seed(folder)

    response = await client.patch(
        f"/api/v1/folders/{folder['_id']}",
        json={"name": "Chapter 1 Revised"},
    )
    assert response.status_code == 200
    body = response.json()["folder"]
    assert body["name"] == "Chapter 1 Revised"
    assert body["parentId"] is None


@pytest.mark.asyncio
async def test_move_folder_to_another_parent(folders_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    parent1 = make_folder(user_id, "Chapter 1")
    parent2 = make_folder(user_id, "Chapter 2")
    child = make_folder(user_id, "Section 1", parent_id=parent1["_id"])
    database["folders"].seed(parent1, parent2, child)

    response = await client.patch(
        f"/api/v1/folders/{child['_id']}",
        json={"parentId": str(parent2["_id"])},
    )
    assert response.status_code == 200
    body = response.json()["folder"]
    assert body["parentId"] == str(parent2["_id"])


@pytest.mark.asyncio
async def test_move_folder_to_root(folders_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    parent = make_folder(user_id, "Chapter 1")
    child = make_folder(user_id, "Section 1", parent_id=parent["_id"])
    database["folders"].seed(parent, child)

    response = await client.patch(
        f"/api/v1/folders/{child['_id']}",
        json={"parentId": None},
    )
    assert response.status_code == 200
    body = response.json()["folder"]
    assert body["parentId"] is None


@pytest.mark.asyncio
async def test_reject_move_under_self(folders_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    database["folders"].seed(folder)

    response = await client.patch(
        f"/api/v1/folders/{folder['_id']}",
        json={"parentId": str(folder["_id"])},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_MOVE"


@pytest.mark.asyncio
async def test_reject_move_under_descendant(folders_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    parent = make_folder(user_id, "Chapter 1")
    child = make_folder(user_id, "Section 1", parent_id=parent["_id"])
    grandchild = make_folder(user_id, "Subsection 1.1", parent_id=child["_id"])
    database["folders"].seed(parent, child, grandchild)

    response = await client.patch(
        f"/api/v1/folders/{parent['_id']}",
        json={"parentId": str(grandchild["_id"])},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_MOVE"


@pytest.mark.asyncio
async def test_block_delete_folder_with_subfolders(
    folders_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    parent = make_folder(user_id, "Chapter 1")
    child = make_folder(user_id, "Section 1", parent_id=parent["_id"])
    database["folders"].seed(parent, child)

    response = await client.delete(f"/api/v1/folders/{parent['_id']}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FOLDER_NOT_EMPTY"


@pytest.mark.asyncio
async def test_block_delete_folder_with_problems(
    folders_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    problem = make_problem(user_id, folder_id=str(folder["_id"]))
    database["folders"].seed(folder)
    database["problems"].seed(problem)

    response = await client.delete(f"/api/v1/folders/{folder['_id']}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FOLDER_NOT_EMPTY"


@pytest.mark.asyncio
async def test_delete_empty_folder(folders_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    database["folders"].seed(folder)

    response = await client.delete(f"/api/v1/folders/{folder['_id']}")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_folder_tree_with_recursive_counts(
    folders_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    # Create folder structure:
    # Chapter 1 (2 problems)
    #   Section 1.1 (1 problem)
    # Chapter 2 (1 problem)
    # Unfiled: 2 problems

    chapter1 = make_folder(user_id, "Chapter 1")
    section1_1 = make_folder(user_id, "Section 1.1", parent_id=chapter1["_id"])
    chapter2 = make_folder(user_id, "Chapter 2")

    database["folders"].seed(chapter1, section1_1, chapter2)

    # Problems
    p1 = make_problem(user_id, text="P1", folder_id=str(chapter1["_id"]))
    p2 = make_problem(user_id, text="P2", folder_id=str(chapter1["_id"]))
    p3 = make_problem(user_id, text="P3", folder_id=str(section1_1["_id"]))
    p4 = make_problem(user_id, text="P4", folder_id=str(chapter2["_id"]))
    p5 = make_problem(user_id, text="P5", folder_id=None)
    p6 = make_problem(user_id, text="P6")  # no folderId field

    database["problems"].seed(p1, p2, p3, p4, p5, p6)

    response = await client.get("/api/v1/folders")
    assert response.status_code == 200
    body = response.json()

    assert body["allProblemsCount"] == 6
    assert body["unfiledCount"] == 2

    # Find Chapter 1 in items
    chapter1_item = next(
        (item for item in body["items"] if item["name"] == "Chapter 1"), None
    )
    assert chapter1_item is not None
    assert chapter1_item["problemCount"] == 3  # 2 in chapter + 1 in section

    # Check Section 1.1 is child
    assert len(chapter1_item["children"]) == 1
    section1_1_item = chapter1_item["children"][0]
    assert section1_1_item["name"] == "Section 1.1"
    assert section1_1_item["problemCount"] == 1

    # Find Chapter 2
    chapter2_item = next(
        (item for item in body["items"] if item["name"] == "Chapter 2"), None
    )
    assert chapter2_item is not None
    assert chapter2_item["problemCount"] == 1


@pytest.mark.asyncio
async def test_cross_user_folder_access_denied(
    folders_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    other_user_id = folders_app.state.secondary_user["_id"]

    other_folder = make_folder(other_user_id, "Other's Folder")
    database["folders"].seed(other_folder)

    # Try to access other user's folder
    response = await client.get(f"/api/v1/folders/{other_folder['_id']}")
    assert response.status_code == 403

    # Try to rename
    response = await client.patch(
        f"/api/v1/folders/{other_folder['_id']}",
        json={"name": "Hacked"},
    )
    assert response.status_code == 403

    # Try to delete
    response = await client.delete(f"/api/v1/folders/{other_folder['_id']}")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_folder_tree_alphabetical_order(
    folders_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    # Create folders in non-alphabetical order
    folder_c = make_folder(user_id, "Chapter C")
    folder_a = make_folder(user_id, "Chapter A")
    folder_b = make_folder(user_id, "Chapter B")

    child_c = make_folder(user_id, "Section C", parent_id=folder_c["_id"])
    child_a = make_folder(user_id, "Section A", parent_id=folder_c["_id"])
    child_b = make_folder(user_id, "Section B", parent_id=folder_c["_id"])

    database["folders"].seed(folder_c, folder_a, folder_b, child_c, child_a, child_b)

    response = await client.get("/api/v1/folders")
    assert response.status_code == 200
    body = response.json()

    # Root level should be alphabetically sorted
    root_names = [item["name"] for item in body["items"]]
    assert root_names == ["Chapter A", "Chapter B", "Chapter C"]

    # Children of Chapter C should be alphabetically sorted
    chapter_c = next(item for item in body["items"] if item["name"] == "Chapter C")
    child_names = [child["name"] for child in chapter_c["children"]]
    assert child_names == ["Section A", "Section B", "Section C"]


@pytest.mark.asyncio
async def test_deleted_problems_not_counted(
    folders_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = folders_app.state.fake_database
    user_id = folders_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    database["folders"].seed(folder)

    # Active problem
    p1 = make_problem(user_id, text="Active", folder_id=str(folder["_id"]))
    # Deleted problem
    p2 = make_problem(user_id, text="Deleted", folder_id=str(folder["_id"]), is_deleted=True)

    database["problems"].seed(p1, p2)

    response = await client.get("/api/v1/folders")
    assert response.status_code == 200
    body = response.json()

    assert body["allProblemsCount"] == 1  # only active

    folder_item = body["items"][0]
    assert folder_item["problemCount"] == 1  # only active problem


@pytest.mark.asyncio
async def test_non_existent_parent_returns_404(
    folders_app: FastAPI, client: AsyncClient
) -> None:
    fake_id = str(ObjectId())

    response = await client.post(
        "/api/v1/folders",
        json={"name": "Test", "parentId": fake_id},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_non_existent_folder_returns_404(
    folders_app: FastAPI, client: AsyncClient
) -> None:
    fake_id = str(ObjectId())

    response = await client.get(f"/api/v1/folders/{fake_id}")
    assert response.status_code == 404

    response = await client.patch(f"/api/v1/folders/{fake_id}", json={"name": "Test"})
    assert response.status_code == 404

    response = await client.delete(f"/api/v1/folders/{fake_id}")
    assert response.status_code == 404
