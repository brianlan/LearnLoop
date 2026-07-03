from __future__ import annotations

import re
from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any, cast

import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.storage.s3 import StorageObjectNotFoundError
from app.main import create_app


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
        self._skip = 0
        self._limit: int | None = None

    def sort(self, field: str | list[tuple[str, int]], direction: int | None = None) -> FakeCursor:
        if isinstance(field, list):
            for key, dir_val in reversed(field):
                reverse = dir_val < 0
                self._documents.sort(
                    key=lambda document: cast(Any, document.get(key)),
                    reverse=reverse,
                )
        else:
            dir_val = direction if direction is not None else 1
            reverse = dir_val < 0
            self._documents.sort(
                key=lambda document: cast(Any, document.get(field)),
                reverse=reverse,
            )
        return self

    def skip(self, amount: int) -> FakeCursor:
        self._skip = amount
        return self

    def limit(self, amount: int) -> FakeCursor:
        self._limit = amount
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        documents = self._documents[self._skip :]
        effective_limit = self._limit if self._limit is not None else length
        if effective_limit is not None:
            documents = documents[:effective_limit]
        return [deepcopy(document) for document in documents]

    def __aiter__(self) -> FakeCursor:
        self._index = self._skip
        return self

    async def __anext__(self) -> dict[str, Any]:
        documents = self._documents[self._index :]
        if self._limit is not None and (self._index - self._skip) >= self._limit:
            raise StopAsyncIteration
        if not documents:
            raise StopAsyncIteration
        doc = documents[0]
        self._index += 1
        return deepcopy(doc)


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self._documents = deepcopy(documents) if documents is not None else []
        self._insert_one_error: Exception | None = None
        self._delete_one_error: Exception | None = None

    def seed(self, *documents: dict[str, Any]) -> None:
        for doc in documents:
            self._documents.append(deepcopy(doc))

    def find(
        self,
        query: dict[str, Any],
        session: Any | None = None,
        projection: dict[str, Any] | None = None,
    ) -> FakeCursor:
        matching = [
            doc for doc in self._documents
            if matches_query(doc, query)
        ]
        return FakeCursor(matching)

    async def find_one(
        self,
        query: dict[str, Any],
        sort: Any | None = None,
        session: Any | None = None,
    ) -> dict[str, Any] | None:
        matching = [
            doc for doc in self._documents
            if matches_query(doc, query)
        ]
        if not matching:
            return None
        if sort:
            for key, direction in reversed(sort):
                reverse = direction < 0
                matching.sort(key=lambda d: cast(Any, d.get(key)), reverse=reverse)
        return deepcopy(matching[0])

    async def count_documents(self, query: dict[str, Any], session: Any | None = None) -> int:
        return len([
            doc for doc in self._documents
            if matches_query(doc, query)
        ])

    async def insert_one(self, document: dict[str, Any], session: Any | None = None) -> FakeInsertOneResult:
        if self._insert_one_error is not None:
            raise self._insert_one_error
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
        self,
        query: dict[str, Any],
        update: dict[str, Any] | list[dict[str, Any]],
        upsert: bool = False,
        session: Any | None = None,
    ) -> FakeUpdateResult:
        target_doc = None
        for document in self._documents:
            if matches_query(document, query):
                target_doc = document
                break

        if target_doc is not None:
            if isinstance(update, list):
                for stage in update:
                    for key, expr in stage.get("$set", {}).items():
                        target_doc[key] = _eval_pipeline_expr(expr, target_doc)
            else:
                for key, value in update.get("$set", {}).items():
                    _set_nested(target_doc, key, deepcopy(value))
            return FakeUpdateResult(1)

        if upsert:
            new_doc = deepcopy(query)
            new_doc = {k: v for k, v in new_doc.items() if not k.startswith("$") and "." not in k}
            if isinstance(update, list):
                for stage in update:
                    for key, expr in stage.get("$set", {}).items():
                        new_doc[key] = _eval_pipeline_expr(expr, new_doc)
            else:
                for key, value in update.get("$set", {}).items():
                    _set_nested(new_doc, key, deepcopy(value))
            if "_id" not in new_doc:
                new_doc["_id"] = ObjectId()
            self._documents.append(new_doc)
            return FakeUpdateResult(1)

        return FakeUpdateResult(0)

    async def update_many(
        self,
        query: dict[str, Any],
        update: dict[str, Any] | list[dict[str, Any]],
        session: Any | None = None,
    ) -> FakeUpdateResult:
        count = 0
        for document in self._documents:
            if matches_query(document, query):
                if isinstance(update, list):
                    for stage in update:
                        for key, expr in stage.get("$set", {}).items():
                            document[key] = _eval_pipeline_expr(expr, document)
                else:
                    for key, value in update.get("$set", {}).items():
                        _set_nested(document, key, deepcopy(value))
                count += 1
        return FakeUpdateResult(count)

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any] | None:
        for document in self._documents:
            if matches_query(document, query):
                original_document = deepcopy(document)
                for key, value in update.get("$set", {}).items():
                    _set_nested(document, key, deepcopy(value))
                return original_document
        return None

    async def delete_one(self, query: dict[str, Any], session: Any | None = None) -> FakeDeleteResult:
        if self._delete_one_error is not None:
            raise self._delete_one_error
        for index, document in enumerate(self._documents):
            if matches_query(document, query):
                del self._documents[index]
                return FakeDeleteResult(1)
        return FakeDeleteResult(0)

    async def distinct(self, field: str, query: dict[str, Any]) -> list[Any]:
        values: list[Any] = []
        seen: set[Any] = set()
        for document in self._documents:
            if not matches_query(document, query):
                continue
            parts = field.split(".")
            resolved_values = get_nested_values(document, parts)
            for current in resolved_values:
                iterable = current if isinstance(current, list) else [current]
                for value in iterable:
                    if value in seen:
                        continue
                    seen.add(value)
                    values.append(value)
        return values

    async def aggregate(self, pipeline: list[dict[str, Any]]) -> FakeCursor:
        docs = [deepcopy(d) for d in self._documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if matches_query(d, stage["$match"])]
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

    async def create_index(self, keys: Any, **kwargs: Any) -> None:
        pass

    async def replace_one(
        self,
        query: dict[str, Any],
        replacement: dict[str, Any],
        **kwargs: Any,
    ) -> FakeUpdateResult:
        for i, document in enumerate(self._documents):
            if matches_query(document, query):
                stored = deepcopy(replacement)
                if "_id" not in stored and "_id" in document:
                    stored["_id"] = document["_id"]
                self._documents[i] = stored
                return FakeUpdateResult(1)
        return FakeUpdateResult(0)


class FakeDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection([])
        return self._collections[name]

    def seed(self, collection: str, documents: list[dict[str, Any]]) -> None:
        for document in documents:
            self[collection].seed(document)


class FakeStorage:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[tuple[str, str, str | None, bytes]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []
        self._counter = 0

    def build_object_key(self, user_id: str, extension: str) -> str:
        self._counter += 1
        return f"users/{user_id}/ingestion/preview-{self._counter}{extension}"

    def put_object(self, bucket: str, object_key: str, payload: bytes, content_type: str | None) -> None:
        self._objects[(bucket, object_key)] = payload
        self.put_calls.append((bucket, object_key, content_type, payload))

    def get_object(self, bucket: str, object_key: str) -> bytes:
        self.get_calls.append((bucket, object_key))
        payload = self._objects.get((bucket, object_key))
        if payload is None:
            raise StorageObjectNotFoundError(object_key)
        return payload

    def delete_object(self, bucket: str, object_key: str) -> None:
        self.delete_calls.append((bucket, object_key))
        self._objects.pop((bucket, object_key), None)

    def seed(self, bucket: str, object_key: str, payload: bytes) -> None:
        self._objects[(bucket, object_key)] = payload


def get_nested_values(document: Any, parts: list[str]) -> list[Any]:
    if not parts:
        return [document]
    current_part = parts[0]
    next_parts = parts[1:]

    if isinstance(document, list):
        results = []
        for item in document:
            results.extend(get_nested_values(item, parts))
        return results

    if isinstance(document, dict):
        val = document.get(current_part)
        return get_nested_values(val, next_parts)

    return [None]


def matches_query(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if key == "$or":
            if not any(matches_query(document, sub) for sub in value):
                return False
            continue

        parts = key.split(".")
        candidates = get_nested_values(document, parts)

        if isinstance(value, dict) and any(k.startswith("$") for k in value.keys()):
            for op, op_val in value.items():
                if op == "$options":
                    continue
                if op == "$exists":
                    has_non_none = any(c is not None for c in candidates)
                    if op_val and not has_non_none:
                        return False
                    if not op_val and has_non_none:
                        return False
                elif op == "$in":
                    matched = False
                    for c in candidates:
                        if isinstance(c, list):
                            if any(item in op_val for item in c):
                                matched = True
                                break
                        else:
                            if c in op_val:
                                matched = True
                                break
                    if not matched:
                        return False
                elif op == "$ne":
                    for c in candidates:
                        if isinstance(c, list):
                            if op_val in c:
                                return False
                        else:
                            if c == op_val:
                                return False
                elif op == "$regex":
                    pattern = op_val
                    options = value.get("$options", "")
                    flags = re.IGNORECASE if "i" in options else 0
                    matched = False
                    for c in candidates:
                        if isinstance(c, list):
                            if any(re.search(pattern, str(item), flags) for item in c):
                                matched = True
                                break
                        else:
                            if re.search(pattern, str(c or ""), flags):
                                matched = True
                                break
                    if not matched:
                        return False
                elif op == "$gt":
                    if not any(c is not None and c > op_val for c in candidates):
                        return False
                elif op == "$gte":
                    if not any(c is not None and c >= op_val for c in candidates):
                        return False
                elif op == "$lt":
                    if not any(c is not None and c < op_val for c in candidates):
                        return False
                elif op == "$lte":
                    if not any(c is not None and c <= op_val for c in candidates):
                        return False
                else:
                    return False
        else:
            matched = False
            for c in candidates:
                if isinstance(c, list):
                    if value in c:
                        matched = True
                        break
                else:
                    if c == value:
                        matched = True
                        break
            if not matched:
                return False
    return True


def _set_nested(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    target = document
    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            target[part] = {}
        target = target[part]
    target[parts[-1]] = value


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


@pytest_asyncio.fixture
async def app() -> FastAPI:
    return create_app()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
