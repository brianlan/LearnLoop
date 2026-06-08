from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, cast

from bson import ObjectId


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = [deepcopy(document) for document in documents]

    def sort(self, field: str, direction: int) -> FakeCursor:
        reverse = direction < 0
        self._documents.sort(
            key=lambda document: cast(Any, document.get(field)),
            reverse=reverse,
        )
        return self

    def skip(self, amount: int) -> FakeCursor:
        return self

    def limit(self, amount: int) -> FakeCursor:
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return deepcopy(self._documents)


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = deepcopy(documents)

    def seed(self, document: dict[str, Any]) -> None:
        self._documents.append(deepcopy(document))

    def find(self, query: dict[str, Any]) -> FakeCursor:
        matching = [
            doc for doc in self._documents
            if matches_query(doc, query)
        ]
        return FakeCursor(matching)

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for document in self._documents:
            if matches_query(document, query):
                return deepcopy(document)
        return None

    async def count_documents(self, query: dict[str, Any]) -> int:
        return len([
            doc for doc in self._documents
            if matches_query(doc, query)
        ])

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
    ) -> None:
        for document in self._documents:
            if matches_query(document, query):
                if "$set" in update:
                    for key, value in update["$set"].items():
                        document[key] = deepcopy(value)

    async def insert_one(self, document: dict[str, Any]) -> None:
        self._documents.append(deepcopy(document))

    async def delete_one(self, query: dict[str, Any]) -> None:
        self._documents = [
            doc for doc in self._documents
            if not matches_query(doc, query)
        ]


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


def matches_query(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        # Handle nested keys like "correctAnswer.display"
        if "." in key:
            parts = key.split(".")
            actual = document
            for part in parts:
                if not isinstance(actual, dict):
                    return False
                actual = actual.get(part)
        else:
            actual = document.get(key)

        if isinstance(value, dict):
            for op, op_value in value.items():
                if op == "$exists":
                    if op_value and actual is None:
                        return False
                    if not op_value and actual is not None:
                        return False
                elif op == "$in":
                    if actual not in op_value:
                        return False
                elif op == "$ne":
                    if actual == op_value:
                        return False
                else:
                    return False
        else:
            if actual is None or actual != value:
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


def make_problem(
    user_id: ObjectId,
    *,
    text: str = "What is 2+2?",
    problem_type: str = "short-answer",
    correct_answer_display: str = "4",
    subject: str = "math",
    is_deleted: bool = False,
    last_tested_at: datetime | None = None,
    exposure_count: int = 0,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": text,
        "problemType": problem_type,
        "subject": subject,
        "graphDsl": None,
        "correctAnswer": {
            "display": correct_answer_display,
            "normalizedText": correct_answer_display,
            "normalizedSet": [],
            "format": "single",
        },
        "tags": [],
        "sourceImage": None,
        "origin": {},
        "tracking": {
            "exposureCount": exposure_count,
            "correctCount": 0,
            "failedCount": 0,
            "lastTestedAt": last_tested_at,
            "lastAttemptCorrect": None,
        },
        "isDeleted": is_deleted,
        "deletedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }
