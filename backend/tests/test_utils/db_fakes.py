"""Fake database and query matcher utilities for backend tests.

Extracted from tests/conftest.py for reuse and direct testability.
Both tests/conftest.py and tests/api/conftest.py re-export these symbols
so existing imports continue to work.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, cast

from bson import ObjectId


class FakeInsertOneResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(self, modified_count: int, matched_count: int | None = None) -> None:
        self.modified_count = modified_count
        self.matched_count = modified_count if matched_count is None else matched_count


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
            stored = deepcopy(doc)
            if "_id" not in stored:
                stored["_id"] = ObjectId()
            self._documents.append(stored)

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
        array_filters: list[dict[str, Any]] | None = None,
    ) -> FakeUpdateResult:
        target_doc = None
        for document in self._documents:
            if matches_query(document, query):
                target_doc = document
                break

        if target_doc is not None:
            self._apply_update(target_doc, query, update, array_filters)
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
        sort: Any | None = None,
        return_document: Any | None = None,
        array_filters: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        for document in self._documents:
            if matches_query(document, query):
                original_document = deepcopy(document)
                self._apply_update(document, query, update, array_filters)
                if return_document:
                    return deepcopy(document)
                return original_document
        return None

    def _apply_update(
        self,
        document: dict[str, Any],
        query: dict[str, Any],
        update: dict[str, Any] | list[dict[str, Any]],
        array_filters: list[dict[str, Any]] | None,
    ) -> None:
        stages = update if isinstance(update, list) else [update]

        # MongoDB resolves positional "$" indexes once, against the document
        # state that matched the query, before applying any modification.
        # Resolve every positional path up front so later keys in the same
        # update do not observe earlier keys' mutations.
        positional: dict[str, int] = {}
        for stage in stages:
            for operator in ("$set", "$unset", "$push", "$inc"):
                for key in stage.get(operator, {}):
                    if ".$." in key:
                        array_path = key.split(".$.")[0]
                        if array_path not in positional:
                            positional[array_path] = self._resolve_array_index(
                                document, query, array_path
                            )

        def _resolve(key: str) -> list[str]:
            if ".$[" in key:
                return self._resolve_array_filter_keys(document, key, array_filters)
            if ".$." in key:
                array_path = key.split(".$.")[0]
                index = positional.get(array_path, 0)
                return [key.replace(".$.", f".{index}.", 1)]
            return [key]

        for stage in stages:
            for key, value in stage.get("$set", {}).items():
                for resolved_key in _resolve(key):
                    expr = _eval_pipeline_expr(value, document) if isinstance(update, list) else value
                    _set_nested(document, resolved_key, deepcopy(expr))
            for key, value in stage.get("$unset", {}).items():
                for resolved_key in _resolve(key):
                    _unset_nested(document, resolved_key)
            for key, value in stage.get("$push", {}).items():
                for resolved_key in _resolve(key):
                    _push_nested(document, resolved_key, value)
            for key, value in stage.get("$inc", {}).items():
                for resolved_key in _resolve(key):
                    _inc_nested(document, resolved_key, value)

    def _resolve_positional_key(
        self,
        document: dict[str, Any],
        query: dict[str, Any],
        key: str,
    ) -> str:
        if ".$." not in key:
            return key
        array_path = key.split(".$.")[0]
        index = self._resolve_array_index(document, query, array_path)
        return key.replace(".$.", f".{index}.", 1)

    def _resolve_array_filter_keys(
        self,
        document: dict[str, Any],
        key: str,
        array_filters: list[dict[str, Any]] | None,
    ) -> list[str]:
        """Expand an ``a.$[id].b`` path to one concrete path per matching element."""
        head, rest = key.split(".$[", 1)
        identifier, tail = rest.split("]", 1)
        identifier_filter: dict[str, Any] = {}
        for spec in array_filters or []:
            if not isinstance(spec, dict):
                continue
            # The identifier may appear bare (``{"item": {...}}``) or as a
            # dotted prefix on element fields (``{"item.imageId": ...}``);
            # both constrain the element the same way.
            element_query: dict[str, Any] = {}
            for spec_key, spec_value in spec.items():
                if spec_key == identifier and isinstance(spec_value, dict):
                    element_query.update(spec_value)
                elif spec_key.startswith(f"{identifier}."):
                    element_query[spec_key[len(identifier) + 1 :]] = spec_value
            if element_query or identifier in spec:
                identifier_filter = element_query
                break
        target = document.get(head)
        if not isinstance(target, list):
            return []
        return [
            f"{head}.{index}{tail}"
            for index, element in enumerate(target)
            if matches_query(element, identifier_filter)
        ]

    def _resolve_array_index(
        self,
        document: dict[str, Any],
        query: dict[str, Any],
        array_path: str,
    ) -> int:
        def _walk_to_array(doc: Any, path: str) -> Any:
            target = doc
            for part in path.split("."):
                if isinstance(target, dict):
                    target = target.get(part)
                elif isinstance(target, list) and part.isdigit():
                    target = target[int(part)]
                else:
                    return None
            return target

        elements: list[Any] = []
        spec = query.get(array_path)
        if isinstance(spec, dict) and "$elemMatch" in spec:
            elem_match = spec["$elemMatch"]
            array = _walk_to_array(document, array_path)
            if isinstance(array, list):
                elements = array
        else:
            elem_query: dict[str, Any] = {}
            prefix = f"{array_path}."
            for query_key, query_value in query.items():
                if query_key.startswith(prefix):
                    elem_query[query_key[len(prefix) :]] = query_value
            if not elem_query:
                return 0
            array = _walk_to_array(document, array_path)
            if isinstance(array, list):
                elements = array
        for index, item in enumerate(elements):
            if isinstance(item, dict) and matches_query(item, elem_match if "$elemMatch" in (spec or {}) else elem_query):
                return index
        return 0

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


def get_nested_values(document: Any, parts: list[str]) -> list[Any]:
    if not parts:
        return [document]
    current_part = parts[0]
    next_parts = parts[1:]

    if isinstance(document, list):
        # Numeric segments index into the array ("failures.0"); other
        # segments fan out across the elements ("items.itemId").
        if current_part.isdigit():
            index = int(current_part)
            if index < len(document):
                return get_nested_values(document[index], next_parts)
            return [None]
        results = []
        for item in document:
            results.extend(get_nested_values(item, parts))
        return results

    if isinstance(document, dict):
        val = document.get(current_part)
        return get_nested_values(val, next_parts)

    return [None]


def _operators_match(candidates: list[Any], value: dict[str, Any]) -> bool:
    """Evaluate a Mongo operator expression against resolved candidates."""
    if "$elemMatch" in value:
        for candidate in candidates:
            if isinstance(candidate, list) and any(
                matches_query(item, value["$elemMatch"]) for item in candidate
            ):
                return True
        return False

    for op, op_val in value.items():
        if op == "$options":
            continue
        if op == "$not":
            if _operators_match(candidates, op_val):
                return False
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
        elif op == "$nin":
            for c in candidates:
                if isinstance(c, list):
                    if any(item in op_val for item in c):
                        return False
                elif c in op_val:
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
    return True


def matches_query(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if key == "$or":
            if not any(matches_query(document, sub) for sub in value):
                return False
            continue

        parts = key.split(".")
        candidates = get_nested_values(document, parts)

        if isinstance(value, dict) and any(k.startswith("$") for k in value.keys()):
            if not _operators_match(candidates, value):
                return False
            continue
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
        if part.isdigit():
            target = target[int(part)]
        elif part in target and isinstance(target[part], list):
            target = target[part]
        elif part not in target or not isinstance(target[part], dict):
            target[part] = {}
            target = target[part]
        else:
            target = target[part]
    target[parts[-1]] = value


def _unset_nested(document: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    target: Any = document
    for part in parts[:-1]:
        if isinstance(target, dict):
            target = target.get(part)
        elif isinstance(target, list) and part.isdigit():
            target = target[int(part)]
        else:
            return
    if isinstance(target, dict):
        target.pop(parts[-1], None)


def _push_nested(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    target = document
    for part in parts[:-1]:
        if part.isdigit():
            target = target[int(part)]
        elif part not in target or not isinstance(target[part], dict):
            target[part] = {}
            target = target[part]
        else:
            target = target[part]
    field = parts[-1]
    if not isinstance(target.get(field), list):
        target[field] = []
    if isinstance(value, dict) and "$each" in value:
        target[field].extend(deepcopy(value["$each"]))
    else:
        target[field].append(deepcopy(value))


def _inc_nested(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    target = document
    for part in parts[:-1]:
        if part.isdigit():
            target = target[int(part)]
        else:
            target = target[part]
    current = target.get(parts[-1], 0)
    target[parts[-1]] = current + value


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
