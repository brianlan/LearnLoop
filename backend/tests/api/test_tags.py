from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.presentation.deps import get_current_user, get_database


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
        self._documents.sort(key=lambda document: document.get(field), reverse=reverse)
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        if length is None:
            return [deepcopy(document) for document in self._documents]
        return [deepcopy(document) for document in self._documents[:length]]


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        actual = document.get(key)
        if isinstance(value, dict) and "$in" in value:
            if actual not in value["$in"]:
                return False
            continue
        if isinstance(actual, list):
            if value not in actual:
                return False
            continue
        if actual != value:
            return False
    return True


def _eval_pipeline_expr(expr: Any, doc: dict[str, Any]) -> Any:
    if isinstance(expr, dict):
        if "$eq" in expr:
            args = expr["$eq"]
            return _eval_pipeline_expr(args[0], doc) == _eval_pipeline_expr(args[1], doc)
        if "$ne" in expr:
            args = expr["$ne"]
            return _eval_pipeline_expr(args[0], doc) != _eval_pipeline_expr(args[1], doc)
        if "$cond" in expr:
            cond_args = expr["$cond"]
            condition = _eval_pipeline_expr(cond_args[0], doc)
            return _eval_pipeline_expr(cond_args[1] if condition else cond_args[2], doc)
        if "$map" in expr:
            map_spec = expr["$map"]
            input_val = _eval_pipeline_expr(map_spec["input"], doc)
            var_name = map_spec["as"]
            in_expr = map_spec["in"]
            result = []
            for item in input_val:
                scoped = {**doc, f"$${var_name}": item}
                result.append(_eval_pipeline_expr(in_expr, scoped))
            return result
        if "$filter" in expr:
            filter_spec = expr["$filter"]
            input_val = _eval_pipeline_expr(filter_spec["input"], doc)
            var_name = filter_spec["as"]
            cond_expr = filter_spec["cond"]
            result = []
            for item in input_val:
                scoped = {**doc, f"$${var_name}": item}
                if _eval_pipeline_expr(cond_expr, scoped):
                    result.append(item)
            return result
    if isinstance(expr, str):
        if expr.startswith("$$"):
            return doc.get(expr)
        if expr.startswith("$"):
            return doc.get(expr[1:])
    return expr


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

    async def insert_many(
        self, documents: list[dict[str, Any]], ordered: bool = True
    ) -> None:
        for document in documents:
            stored = deepcopy(document)
            if "_id" not in stored:
                stored["_id"] = ObjectId()
            self._documents.append(stored)

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any] | list[dict[str, Any]]
    ) -> FakeUpdateResult:
        for document in self._documents:
            if _matches(document, query):
                if isinstance(update, list):
                    for stage in update:
                        for key, expr in stage.get("$set", {}).items():
                            document[key] = _eval_pipeline_expr(expr, document)
                else:
                    for key, value in update.get("$set", {}).items():
                        document[key] = deepcopy(value)
                return FakeUpdateResult(1)
        return FakeUpdateResult(0)

    async def update_many(
        self, query: dict[str, Any], update: dict[str, Any] | list[dict[str, Any]]
    ) -> FakeUpdateResult:
        count = 0
        for document in self._documents:
            if _matches(document, query):
                if isinstance(update, list):
                    for stage in update:
                        for key, expr in stage.get("$set", {}).items():
                            document[key] = _eval_pipeline_expr(expr, document)
                else:
                    for key, value in update.get("$set", {}).items():
                        document[key] = deepcopy(value)
                count += 1
        return FakeUpdateResult(count)

    async def delete_one(self, query: dict[str, Any]) -> FakeDeleteResult:
        for index, document in enumerate(self._documents):
            if _matches(document, query):
                del self._documents[index]
                return FakeDeleteResult(1)
        return FakeDeleteResult(0)

    def find(self, query: dict[str, Any]) -> FakeCursor:
        matching = [document for document in self._documents if _matches(document, query)]
        return FakeCursor(matching)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for document in self._documents if _matches(document, query))

    async def aggregate(self, pipeline: list[dict[str, Any]]) -> FakeCursor:
        docs = [deepcopy(d) for d in self._documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$unwind" in stage:
                field = stage["$unwind"].lstrip("$")
                unwound = []
                for d in docs:
                    values = d.get(field, [])
                    if isinstance(values, list):
                        for v in values:
                            copy = deepcopy(d)
                            copy[field] = v
                            unwound.append(copy)
                docs = unwound
            elif "$group" in stage:
                group_spec = stage["$group"]
                id_expr = group_spec["_id"]
                groups: dict[Any, list[dict[str, Any]]] = {}
                for d in docs:
                    key = d.get(id_expr.lstrip("$")) if isinstance(id_expr, str) else id_expr
                    groups.setdefault(key, []).append(d)
                result = []
                for key, group_docs in groups.items():
                    row: dict[str, Any] = {"_id": key}
                    for acc_name, acc_spec in group_spec.items():
                        if acc_name == "_id":
                            continue
                        if isinstance(acc_spec, dict) and "$sum" in acc_spec:
                            row[acc_name] = len(group_docs) if acc_spec["$sum"] == 1 else acc_spec["$sum"]
                    result.append(row)
                docs = result
        return FakeCursor(docs)


class FakeDatabase:
    def __init__(self) -> None:
        self._collections = {
            "problems": FakeCollection(),
            "tags": FakeCollection(),
            "ingestion_previews": FakeCollection(),
            "users": FakeCollection(),
            "sessions": FakeCollection(),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections[name]


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


def make_tag(user_id: ObjectId, name: str, *, created_at: datetime | None = None) -> dict[str, Any]:
    now = created_at or datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "name": name,
        "createdAt": now,
    }


def make_problem(
    user_id: ObjectId,
    *,
    text: str = "What is 2+2?",
    problem_type: str = "short-answer",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": text,
        "problemType": problem_type,
        "graphDsl": None,
        "correctAnswer": {
            "display": "4",
            "normalizedText": "4",
            "normalizedSet": [],
            "format": "single",
        },
        "tags": tags or ["math"],
        "sourceImage": {
            "bucket": "learnloop-media",
            "objectKey": f"users/{user_id}/images/{ObjectId()}.png",
            "contentType": "image/png",
            "sizeBytes": 7,
            "sha256": None,
        },
        "origin": None,
        "tracking": {
            "exposureCount": 0,
            "correctCount": 0,
            "failedCount": 0,
            "lastTestedAt": None,
            "lastAttemptCorrect": None,
        },
        "isDeleted": False,
        "deletedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }


@pytest_asyncio.fixture
async def tags_app() -> FastAPI:
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
async def client(tags_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=tags_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_list_tags_returns_sorted_by_name(tags_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    user_id = tags_app.state.primary_user["_id"]
    tag_z = make_tag(user_id, "zeta")
    tag_a = make_tag(user_id, "alpha")
    tag_m = make_tag(user_id, "mu")
    database["tags"].seed(tag_z, tag_a, tag_m)

    response = await client.get("/api/v1/tags")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 3
    assert items[0]["name"] == "alpha"
    assert items[1]["name"] == "mu"
    assert items[2]["name"] == "zeta"


@pytest.mark.asyncio
async def test_list_tags_includes_problem_count(tags_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    user_id = tags_app.state.primary_user["_id"]
    tag_geo = make_tag(user_id, "geometry")
    tag_alg = make_tag(user_id, "algebra")
    database["tags"].seed(tag_geo, tag_alg)

    problem1 = make_problem(user_id, tags=["geometry", "chapter-1"])
    problem2 = make_problem(user_id, tags=["geometry", "chapter-2"])
    problem3 = make_problem(user_id, tags=["algebra"])
    database["problems"].seed(problem1, problem2, problem3)

    response = await client.get("/api/v1/tags")

    assert response.status_code == 200
    items = response.json()["items"]
    geo_item = next(item for item in items if item["name"] == "geometry")
    alg_item = next(item for item in items if item["name"] == "algebra")
    assert geo_item["problemCount"] == 2
    assert alg_item["problemCount"] == 1


@pytest.mark.asyncio
async def test_list_tags_excludes_deleted_problems_from_count(
    tags_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    user_id = tags_app.state.primary_user["_id"]
    tag = make_tag(user_id, "geometry")
    database["tags"].seed(tag)

    active_problem = make_problem(user_id, tags=["geometry"])
    deleted_problem = make_problem(user_id, tags=["geometry"])
    deleted_problem["isDeleted"] = True
    database["problems"].seed(active_problem, deleted_problem)

    response = await client.get("/api/v1/tags")

    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["problemCount"] == 1


@pytest.mark.asyncio
async def test_create_tag_stores_new_tag(tags_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    user_id = tags_app.state.primary_user["_id"]

    response = await client.post("/api/v1/tags", json={"name": "  calculus  "})

    assert response.status_code == 201
    body = response.json()["tag"]
    assert body["name"] == "calculus"
    assert body["problemCount"] == 0

    stored = await database["tags"].find_one({"userId": user_id, "name": "calculus"})
    assert stored is not None


@pytest.mark.asyncio
async def test_create_tag_rejects_duplicate(tags_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    user_id = tags_app.state.primary_user["_id"]
    existing = make_tag(user_id, "geometry")
    database["tags"].seed(existing)

    response = await client.post("/api/v1/tags", json={"name": "geometry"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_TAG"


@pytest.mark.asyncio
async def test_create_tag_rejects_empty_name(tags_app: FastAPI, client: AsyncClient) -> None:
    response = await client.post("/api/v1/tags", json={"name": "   "})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_get_tag_returns_tag_with_count(tags_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    user_id = tags_app.state.primary_user["_id"]
    tag = make_tag(user_id, "geometry")
    database["tags"].seed(tag)
    problem = make_problem(user_id, tags=["geometry"])
    database["problems"].seed(problem)

    response = await client.get(f"/api/v1/tags/{tag['_id']}")

    assert response.status_code == 200
    body = response.json()["tag"]
    assert body["id"] == str(tag["_id"])
    assert body["name"] == "geometry"
    assert body["problemCount"] == 1


@pytest.mark.asyncio
async def test_get_tag_returns_not_found_for_nonexistent(tags_app: FastAPI, client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/tags/{ObjectId()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_rename_tag_updates_tag_and_propagates_to_problems(
    tags_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    user_id = tags_app.state.primary_user["_id"]
    tag = make_tag(user_id, "geometry")
    database["tags"].seed(tag)
    problem1 = make_problem(user_id, tags=["geometry", "chapter-1"])
    problem2 = make_problem(user_id, tags=["geometry", "algebra"])
    database["problems"].seed(problem1, problem2)

    response = await client.patch(f"/api/v1/tags/{tag['_id']}", json={"name": "geo"})

    assert response.status_code == 200
    body = response.json()["tag"]
    assert body["name"] == "geo"
    assert body["problemCount"] == 2

    updated_tag = await database["tags"].find_one({"_id": tag["_id"]})
    assert updated_tag["name"] == "geo"

    updated_p1 = await database["problems"].find_one({"_id": problem1["_id"]})
    updated_p2 = await database["problems"].find_one({"_id": problem2["_id"]})
    assert updated_p1["tags"] == ["geo", "chapter-1"]
    assert updated_p2["tags"] == ["geo", "algebra"]


@pytest.mark.asyncio
async def test_rename_tag_rejects_duplicate_name(tags_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    user_id = tags_app.state.primary_user["_id"]
    tag_geo = make_tag(user_id, "geometry")
    tag_alg = make_tag(user_id, "algebra")
    database["tags"].seed(tag_geo, tag_alg)

    response = await client.patch(f"/api/v1/tags/{tag_geo['_id']}", json={"name": "algebra"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_TAG"


@pytest.mark.asyncio
async def test_rename_tag_same_name_returns_ok(tags_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    user_id = tags_app.state.primary_user["_id"]
    tag = make_tag(user_id, "geometry")
    database["tags"].seed(tag)

    response = await client.patch(f"/api/v1/tags/{tag['_id']}", json={"name": "geometry"})

    assert response.status_code == 200
    assert response.json()["tag"]["name"] == "geometry"


@pytest.mark.asyncio
async def test_delete_tag_removes_tag_and_updates_problems(
    tags_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    user_id = tags_app.state.primary_user["_id"]
    tag = make_tag(user_id, "geometry")
    database["tags"].seed(tag)
    problem = make_problem(user_id, tags=["geometry", "chapter-1"])
    database["problems"].seed(problem)

    response = await client.delete(f"/api/v1/tags/{tag['_id']}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    deleted_tag = await database["tags"].find_one({"_id": tag["_id"]})
    assert deleted_tag is None

    updated_problem = await database["problems"].find_one({"_id": problem["_id"]})
    assert updated_problem["tags"] == ["chapter-1"]


@pytest.mark.asyncio
async def test_delete_tag_idempotent_for_nonexistent(tags_app: FastAPI, client: AsyncClient) -> None:
    response = await client.delete(f"/api/v1/tags/{ObjectId()}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_cross_user_access_denied(tags_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = tags_app.state.fake_database
    other_user_id = tags_app.state.secondary_user["_id"]
    other_tag = make_tag(other_user_id, "theirs")
    database["tags"].seed(other_tag)

    get_response = await client.get(f"/api/v1/tags/{other_tag['_id']}")
    assert get_response.status_code == 403

    rename_response = await client.patch(f"/api/v1/tags/{other_tag['_id']}", json={"name": "hijacked"})
    assert rename_response.status_code == 403

    delete_response = await client.delete(f"/api/v1/tags/{other_tag['_id']}")
    assert delete_response.status_code == 403
