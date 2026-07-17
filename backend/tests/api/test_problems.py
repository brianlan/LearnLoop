from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.storage.s3 import StorageObjectNotFoundError
from app.main import create_app
from app.presentation.deps import get_current_user, get_database, get_s3_storage
from app.presentation.errors import ApiError
from tests.api.conftest import FakeDatabase, make_user


class FakeStorage:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def seed(self, bucket: str, key: str, payload: bytes) -> None:
        self._objects[(bucket, key)] = payload

    def get_object(self, bucket: str, key: str) -> bytes:
        payload = self._objects.get((bucket, key))
        if payload is None:
            raise StorageObjectNotFoundError(key)
        return payload


def make_preview(user_id: ObjectId, *, status: str = "ready") -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "status": status,
        "sourceImage": {
            "bucket": "learnloop-media",
            "objectKey": f"users/{user_id}/images/source.png",
            "contentType": "image/png",
            "sizeBytes": 4,
            "sha256": "abc123",
        },
        "extraction": {
            "requestModel": "gpt-4.1-mini",
            "rawText": "raw extracted text",
            "rawProblemType": "short-answer",
            "rawGraphDsl": "graph TD; A-->B",
        },
        "editableDraft": {
            "text": "draft text",
            "problemType": "short-answer",
            "graphDsl": None,
            "correctAnswer": "draft",
            "tags": ["draft"],
            "subject": "math",
        },
        "createdAt": now,
        "updatedAt": now,
        "expiresAt": now + timedelta(hours=24),
    }


DEFAULT_LAST_TESTED = object()


def make_problem(
    user_id: ObjectId,
    *,
    text: str = "What is 2+2?",
    problem_type: str = "short-answer",
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    last_tested_at: Any = DEFAULT_LAST_TESTED,
    tags: list[str] | None = None,
    is_deleted: bool = False,
    is_disabled: bool = False,
    folder_id: str | None = None,
) -> dict[str, Any]:
    now = updated_at or datetime.now(UTC)
    created = created_at or now
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": text,
        "problemType": problem_type,
        "subject": "math",
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
        "origin": {
            "previewId": ObjectId(),
            "vlmModel": "gpt-4.1-mini",
            "rawExtractedText": "raw",
            "rawExtractedProblemType": problem_type,
            "rawExtractedGraphDsl": None,
        },
        "tracking": {
            "exposureCount": 3,
            "correctCount": 2,
            "failedCount": 1,
            "lastTestedAt": now if last_tested_at is DEFAULT_LAST_TESTED else last_tested_at,
            "lastAttemptCorrect": True,
        },
        "isDeleted": is_deleted,
        "deletedAt": now if is_deleted else None,
        "isDisabled": is_disabled,
        "folderId": folder_id,
        "createdAt": created,
        "updatedAt": now,
    }


def make_solution_task(
    problem: dict[str, Any], user_id: ObjectId, *, status: str
) -> dict[str, Any]:
    return {
        "_id": ObjectId(),
        "problem_id": str(problem["_id"]),
        "user_id": str(user_id),
        "status": status,
        "created_at": datetime.now(UTC),
    }


def make_canonical_solution(
    problem: dict[str, Any], user_id: ObjectId
) -> dict[str, Any]:
    return {
        "_id": ObjectId(),
        "problem_id": str(problem["_id"]),
        "user_id": str(user_id),
        "steps_markdown": "step 1",
        "final_answer": "4",
        "math_level_classification": "basic",
    }


@pytest_asyncio.fixture
async def problems_app() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    storage = FakeStorage()
    primary_user = make_user(ObjectId(), "student1")
    secondary_user = make_user(ObjectId(), "student2")

    application.state.fake_database = database
    application.state.fake_storage = storage
    application.state.primary_user = primary_user
    application.state.secondary_user = secondary_user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_s3_storage] = lambda: storage
    application.dependency_overrides[get_current_user] = lambda: deepcopy(primary_user)
    return application


@pytest_asyncio.fixture
async def client(problems_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=problems_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_confirm_preview_creates_problem_and_canonicalizes_answer(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    preview = make_preview(problems_app.state.primary_user["_id"])
    preview["editableDraft"] = {
        "text": "Choose all correct letters",
        "problemType": "multi-choice",
        "graphDsl": None,
        "correctAnswer": " C, A ,C ",
        "tags": ["geometry", " geometry ", "chapter-3"],
    }
    database["ingestion_previews"].seed(preview)

    response = await client.post(f"/api/v1/ingestion-previews/{preview['_id']}/confirm")

    assert response.status_code == 201
    body = response.json()["problem"]
    assert body["correctAnswer"] == {
        "display": "C, A ,C",
        "normalizedText": "a,c",
        "normalizedSet": ["a", "c"],
        "format": "set",
    }
    assert body["tags"] == ["geometry", "chapter-3"]
    stored_problem = database["problems"]._documents[0]
    assert stored_problem["origin"]["previewId"] == str(preview["_id"])
    assert stored_problem["correctAnswer"]["normalizedSet"] == ["a", "c"]
    assert database["ingestion_previews"]._documents[0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_problem_detail_defaults_missing_subject_to_math(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    old_problem = make_problem(
        user_id,
        text="Legacy problem without subject",
        problem_type="short-answer",
    )
    del old_problem["subject"]
    database["problems"].seed(old_problem)

    response = await client.get(f"/api/v1/problems/{old_problem['_id']}")
    assert response.status_code == 200
    body = response.json()["problem"]
    assert body["subject"] == "math"


@pytest.mark.asyncio
async def test_problem_response_serializes_naive_utc_datetime_with_timezone(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    # Mongo/PyMongo returns UTC datetimes as naive by default. Simulate that
    # storage shape so the regression is exercised through a real endpoint.
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    naive_utc = datetime(2026, 5, 25, 13, 51, 4)
    problem = make_problem(user_id, created_at=naive_utc, updated_at=naive_utc)
    database["problems"].seed(problem)

    response = await client.get(f"/api/v1/problems/{problem['_id']}")

    assert response.status_code == 200
    created_at = response.json()["problem"]["createdAt"]
    assert created_at == "2026-05-25T13:51:04Z"


@pytest.mark.asyncio
async def test_list_detail_update_delete_tags_and_tracking(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    other_problem = make_problem(
        user_id,
        text="Older problem",
        updated_at=datetime.now(UTC) - timedelta(days=2),
        tags=["algebra"],
    )
    newest_problem = make_problem(
        user_id,
        text="Newest problem",
        problem_type="fill-in-the-blank",
        updated_at=datetime.now(UTC) - timedelta(hours=1),
        tags=["geometry", "chapter-3"],
    )
    deleted_problem = make_problem(
        user_id,
        text="Deleted problem",
        updated_at=datetime.now(UTC),
        tags=["deleted"],
        is_deleted=True,
    )
    database["problems"].seed(other_problem, newest_problem, deleted_problem)

    list_response = await client.get("/api/v1/problems")
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert [item["id"] for item in list_body["items"]] == [
        str(newest_problem["_id"]),
        str(other_problem["_id"]),
    ]
    assert list_body["total"] == 2

    filtered_response = await client.get(
        "/api/v1/problems",
        params={"tag": "geometry", "type": "fill-in-the-blank", "page": 1, "pageSize": 1},
    )
    assert filtered_response.status_code == 200
    filtered_body = filtered_response.json()
    assert filtered_body["total"] == 1
    assert filtered_body["items"][0]["id"] == str(newest_problem["_id"])

    detail_response = await client.get(f"/api/v1/problems/{newest_problem['_id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["problem"]["text"] == "Newest problem"

    tracking_response = await client.get(f"/api/v1/problems/{newest_problem['_id']}/tracking")
    assert tracking_response.status_code == 200
    tracking_body = tracking_response.json()
    assert tracking_body["problemId"] == str(newest_problem["_id"])
    assert tracking_body["tracking"] == {
        "exposureCount": 3,
        "correctCount": 2,
        "failedCount": 1,
        "lastTestedAt": newest_problem["updatedAt"].isoformat().replace("+00:00", "Z"),
        "lastAttemptCorrect": True,
    }
    assert "practiceWeight" in tracking_body
    weight = tracking_body["practiceWeight"]
    assert set(weight.keys()) == {"lastWrong", "failure", "recency", "total"}
    # newest_problem is tested today (lastTestedAt ~now, lastAttemptCorrect True),
    # so recency base is 0.0 and 0 elapsed days -> recency 0.0. With more correct
    # than failed (2 vs 1) the failure component is negative, so the raw component
    # sum is negative and the returned total is the zero-clamped value.
    raw_total = weight["lastWrong"] + weight["failure"] + weight["recency"]
    assert weight["total"] == max(0.0, raw_total)

    tags_response = await client.get("/api/v1/problems/tags")
    assert tags_response.status_code == 200
    assert tags_response.json() == {"items": ["algebra", "chapter-3", "geometry"]}

    update_response = await client.patch(
        f"/api/v1/problems/{newest_problem['_id']}",
        json={
            "text": "Updated text",
            "problemType": "multi-choice",
            "graphDsl": "graph LR; X-->Y",
            "correctAnswer": " B, A ,B ",
            "tags": ["logic", " logic ", "sets"],
        },
    )
    assert update_response.status_code == 200
    updated_body = update_response.json()["problem"]
    assert updated_body["text"] == "Updated text"
    assert updated_body["problemType"] == "multi-choice"
    assert updated_body["graphDsl"] == "graph LR; X-->Y"
    assert updated_body["correctAnswer"] == {
        "display": " B, A ,B ",
        "normalizedText": "a,b",
        "normalizedSet": ["a", "b"],
        "format": "set",
    }
    assert updated_body["tags"] == ["logic", "sets"]

    soft_delete_response = await client.delete(f"/api/v1/problems/{other_problem['_id']}")
    assert soft_delete_response.status_code == 200
    assert soft_delete_response.json() == {"ok": True}

    relist_response = await client.get("/api/v1/problems")
    assert relist_response.status_code == 200
    assert relist_response.json()["total"] == 1
    assert relist_response.json()["items"][0]["id"] == str(newest_problem["_id"])

    deleted_detail_response = await client.get(f"/api/v1/problems/{other_problem['_id']}")
    assert deleted_detail_response.status_code == 404
    assert deleted_detail_response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Problem not found"}
    }


@pytest.mark.asyncio
async def test_problem_tracking_includes_practice_weight_with_exact_values(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    from app.infrastructure.config.settings import Settings, get_settings

    explicit_settings = Settings(
        problem_selection_last_wrong_weight=1.5,
        problem_selection_failure_rate_weight=2.0,
        problem_selection_recency_weight=1.0,
    )
    problems_app.dependency_overrides[get_settings] = lambda: explicit_settings

    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="Weighted problem")
    problem["tracking"] = {
        "exposureCount": 0,
        "correctCount": 0,
        "failedCount": 0,
        "lastTestedAt": None,
        "lastAttemptCorrect": None,
    }
    database["problems"].seed(problem)

    response = await client.get(f"/api/v1/problems/{problem['_id']}/tracking")
    assert response.status_code == 200
    body = response.json()
    assert body["problemId"] == str(problem["_id"])
    assert body["practiceWeight"] == {
        "lastWrong": 1.5,
        "failure": 0.0,
        "recency": 1.0,
        "total": 2.5,
    }


@pytest.mark.asyncio
async def test_problem_detail_handles_none_origin(problems_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    problem = make_problem(problems_app.state.primary_user["_id"])
    problem["origin"] = None
    database["problems"].seed(problem)

    response = await client.get(f"/api/v1/problems/{problem['_id']}")

    assert response.status_code == 200
    assert response.json()["problem"]["origin"] == {
        "previewId": None,
        "vlmModel": None,
        "rawExtractedText": None,
        "rawExtractedProblemType": None,
        "rawExtractedGraphDsl": None,
    }


@pytest.mark.asyncio
async def test_problem_image_streams_for_owner_and_handles_missing_object(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    storage: FakeStorage = problems_app.state.fake_storage
    problem = make_problem(problems_app.state.primary_user["_id"])
    database["problems"].seed(problem)
    storage.seed(problem["sourceImage"]["bucket"], problem["sourceImage"]["objectKey"], b"pngdata")

    response = await client.get(f"/api/v1/problems/{problem['_id']}/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"pngdata"

    missing_problem = make_problem(problems_app.state.primary_user["_id"])
    database["problems"].seed(missing_problem)
    missing_response = await client.get(f"/api/v1/problems/{missing_problem['_id']}/image")
    assert missing_response.status_code == 404
    assert missing_response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Problem image not found"}
    }


@pytest.mark.asyncio
async def test_cross_user_access_is_denied_for_problem_and_media_routes(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    other_user_problem = make_problem(problems_app.state.secondary_user["_id"])
    other_user_preview = make_preview(problems_app.state.secondary_user["_id"])
    database["problems"].seed(other_user_problem)
    database["ingestion_previews"].seed(other_user_preview)

    detail_response = await client.get(f"/api/v1/problems/{other_user_problem['_id']}")
    assert detail_response.status_code == 403
    assert detail_response.json() == {
        "error": {"code": "FORBIDDEN", "message": "Forbidden"}
    }

    image_response = await client.get(f"/api/v1/problems/{other_user_problem['_id']}/image")
    assert image_response.status_code == 403
    assert image_response.json() == {
        "error": {"code": "FORBIDDEN", "message": "Forbidden"}
    }

    confirm_response = await client.post(
        f"/api/v1/ingestion-previews/{other_user_preview['_id']}/confirm"
    )
    assert confirm_response.status_code == 404
    assert confirm_response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Preview not found"}
    }


@pytest.mark.asyncio
async def test_problem_routes_require_authentication(problems_app: FastAPI) -> None:
    async def unauthenticated_user() -> dict[str, Any]:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")

    problems_app.dependency_overrides[get_current_user] = unauthenticated_user
    transport = ASGITransport(app=problems_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as unauthenticated_client:
        response = await unauthenticated_client.get("/api/v1/problems")

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHENTICATED", "message": "Authentication required"}
    }


@pytest.mark.asyncio
async def test_problem_payloads_include_is_disabled(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    enabled = make_problem(user_id, text="Enabled")
    disabled = make_problem(user_id, text="Disabled", is_disabled=True)
    database["problems"].seed(enabled, disabled)

    list_response = await client.get("/api/v1/problems")
    assert list_response.status_code == 200
    by_text = {item["text"]: item for item in list_response.json()["items"]}
    assert by_text["Enabled"]["isDisabled"] is False
    assert by_text["Disabled"]["isDisabled"] is True

    detail_response = await client.get(f"/api/v1/problems/{disabled['_id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["problem"]["isDisabled"] is True


@pytest.mark.asyncio
async def test_toggle_disables_problem_with_correct_teacher_password(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="To disable")
    database["problems"].seed(problem)

    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/disabled",
        json={"isDisabled": True, "teacherPassword": "default-teacher-password"},
    )

    assert response.status_code == 200
    body = response.json()["problem"]
    assert body["isDisabled"] is True

    stored = database["problems"]._documents[0]
    assert stored["isDisabled"] is True


@pytest.mark.asyncio
async def test_toggle_enables_disabled_problem_with_correct_teacher_password(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="To enable", is_disabled=True)
    database["problems"].seed(problem)

    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/disabled",
        json={"isDisabled": False, "teacherPassword": "default-teacher-password"},
    )

    assert response.status_code == 200
    assert response.json()["problem"]["isDisabled"] is False

    stored = database["problems"]._documents[0]
    assert stored["isDisabled"] is False


@pytest.mark.asyncio
async def test_toggle_rejects_incorrect_teacher_password_and_leaves_state_unchanged(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="Protected")
    database["problems"].seed(problem)

    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/disabled",
        json={"isDisabled": True, "teacherPassword": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHENTICATED", "message": "Incorrect teacher password"}
    }

    stored = database["problems"]._documents[0]
    assert stored.get("isDisabled") is False


@pytest.mark.asyncio
async def test_toggle_rejects_missing_deleted_and_other_user_problems(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    other_user_id = problems_app.state.secondary_user["_id"]
    deleted = make_problem(user_id, text="Deleted", is_deleted=True)
    other_user = make_problem(other_user_id, text="Other user")
    database["problems"].seed(deleted, other_user)
    body = {"isDisabled": True, "teacherPassword": "default-teacher-password"}

    missing_response = await client.patch(
        f"/api/v1/problems/{ObjectId()}/disabled",
        json=body,
    )
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "NOT_FOUND"

    deleted_response = await client.patch(
        f"/api/v1/problems/{deleted['_id']}/disabled",
        json=body,
    )
    assert deleted_response.status_code == 404
    assert deleted_response.json()["error"]["code"] == "NOT_FOUND"

    forbidden_response = await client.patch(
        f"/api/v1/problems/{other_user['_id']}/disabled",
        json=body,
    )
    assert forbidden_response.status_code == 403
    assert forbidden_response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_solution_status(problems_app: FastAPI, client: AsyncClient) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id)
    problem_id_str = str(problem["_id"])
    database["problems"].seed(problem)

    # 1. returns 'none' when no task or solution exists
    response = await client.get(f"/api/v1/problems/{problem_id_str}/solution-status")
    assert response.status_code == 200
    assert response.json() == {"status": "none"}

    # 2. returns 'pending' when task is pending
    task_pending = {
        "_id": ObjectId(),
        "problem_id": problem_id_str,
        "user_id": str(user_id),
        "status": "pending",
        "created_at": datetime.now(UTC)
    }
    database["solution_generation_tasks"].seed(task_pending)
    response = await client.get(f"/api/v1/problems/{problem_id_str}/solution-status")
    assert response.status_code == 200
    assert response.json() == {"status": "pending"}

    # 3. returns 'generating' when task is generating
    database["solution_generation_tasks"]._documents.clear()
    task_generating = {**task_pending, "_id": ObjectId(), "status": "generating"}
    database["solution_generation_tasks"].seed(task_generating)
    response = await client.get(f"/api/v1/problems/{problem_id_str}/solution-status")
    assert response.status_code == 200
    assert response.json() == {"status": "generating"}

    # 4. returns 'failed' when task is failed
    database["solution_generation_tasks"]._documents.clear()
    task_failed = {**task_pending, "_id": ObjectId(), "status": "failed"}
    database["solution_generation_tasks"].seed(task_failed)
    response = await client.get(f"/api/v1/problems/{problem_id_str}/solution-status")
    assert response.status_code == 200
    assert response.json() == {"status": "failed"}

    # 5. returns 'ready' when solution exists
    solution = {
        "_id": ObjectId(),
        "problem_id": problem_id_str,
        "user_id": str(user_id),
        "steps_markdown": "step 1",
        "final_answer": "4",
        "math_level_classification": "basic",
    }
    database["canonical_solutions"].seed(solution)
    response = await client.get(f"/api/v1/problems/{problem_id_str}/solution-status")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}

    # 6. returns 403 for other user problem
    other_problem = make_problem(problems_app.state.secondary_user["_id"])
    database["problems"].seed(other_problem)
    response = await client.get(f"/api/v1/problems/{str(other_problem['_id'])}/solution-status")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_regenerate_solution_when_ready_deletes_solution_and_enqueues_task(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id)
    problem_id_str = str(problem["_id"])
    database["problems"].seed(problem)

    solution = make_canonical_solution(problem, user_id)
    database["canonical_solutions"].seed(solution)

    response = await client.post(f"/api/v1/problems/{problem_id_str}/solution-regeneration")

    assert response.status_code == 200
    assert response.json() == {"status": "pending"}

    # canonical solution deleted
    remaining_solutions = await database["canonical_solutions"].find(
        {"problem_id": problem_id_str}
    ).to_list(length=None)
    assert remaining_solutions == []

    # pending task created
    tasks = await database["solution_generation_tasks"].find(
        {"problem_id": problem_id_str}
    ).to_list(length=None)
    assert len(tasks) == 1
    assert tasks[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_regenerate_solution_when_failed_resets_task_to_pending(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id)
    problem_id_str = str(problem["_id"])
    database["problems"].seed(problem)

    failed_task = {
        **make_solution_task(problem, user_id, status="failed"),
        "retry_count": 3,
        "failure_reason": "VLM timeout",
        "started_at": datetime.now(UTC) - timedelta(hours=1),
    }
    database["solution_generation_tasks"].seed(failed_task)

    response = await client.post(f"/api/v1/problems/{problem_id_str}/solution-regeneration")

    assert response.status_code == 200
    assert response.json() == {"status": "pending"}

    tasks = await database["solution_generation_tasks"].find(
        {"problem_id": problem_id_str}
    ).to_list(length=None)
    assert len(tasks) == 1
    assert tasks[0]["status"] == "pending"
    assert tasks[0]["retry_count"] == 0
    assert tasks[0]["failure_reason"] is None
    assert tasks[0]["started_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "generating"])
async def test_regenerate_solution_rejects_ineligible_active_states(
    problems_app: FastAPI, client: AsyncClient, status: str
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id)
    problem_id_str = str(problem["_id"])
    database["problems"].seed(problem)

    database["solution_generation_tasks"].seed(
        make_solution_task(problem, user_id, status=status)
    )

    response = await client.post(f"/api/v1/problems/{problem_id_str}/solution-regeneration")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOLUTION_REGENERATION_CONFLICT"

    # task unchanged
    tasks = await database["solution_generation_tasks"].find(
        {"problem_id": problem_id_str}
    ).to_list(length=None)
    assert len(tasks) == 1
    assert tasks[0]["status"] == status


@pytest.mark.asyncio
async def test_regenerate_solution_rejects_none_state(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id)
    problem_id_str = str(problem["_id"])
    database["problems"].seed(problem)

    response = await client.post(f"/api/v1/problems/{problem_id_str}/solution-regeneration")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOLUTION_REGENERATION_CONFLICT"


@pytest.mark.asyncio
async def test_regenerate_solution_forbidden_for_other_user(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    other_user_id = problems_app.state.secondary_user["_id"]
    other_problem = make_problem(other_user_id)
    database["problems"].seed(other_problem)
    database["canonical_solutions"].seed(make_canonical_solution(other_problem, other_user_id))

    response = await client.post(
        f"/api/v1/problems/{str(other_problem['_id'])}/solution-regeneration"
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_regenerate_solution_when_ready_with_stale_task_resets_task(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    """When both a canonical solution and a stale task exist (effective 'ready'),
    regeneration deletes the solution and resets the existing task to pending
    rather than creating a duplicate."""
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id)
    problem_id_str = str(problem["_id"])
    database["problems"].seed(problem)

    stale_task = {
        **make_solution_task(problem, user_id, status="ready"),
        "retry_count": 2,
        "failure_reason": "old failure",
    }
    database["solution_generation_tasks"].seed(stale_task)
    database["canonical_solutions"].seed(make_canonical_solution(problem, user_id))

    response = await client.post(f"/api/v1/problems/{problem_id_str}/solution-regeneration")

    assert response.status_code == 200
    assert response.json() == {"status": "pending"}

    # solution deleted
    remaining = await database["canonical_solutions"].find(
        {"problem_id": problem_id_str}
    ).to_list(length=None)
    assert remaining == []

    # exactly one task, reset to pending
    tasks = await database["solution_generation_tasks"].find(
        {"problem_id": problem_id_str}
    ).to_list(length=None)
    assert len(tasks) == 1
    assert tasks[0]["status"] == "pending"
    assert tasks[0]["retry_count"] == 0
    assert tasks[0]["failure_reason"] is None


@pytest.mark.asyncio
async def test_solution_state_filter_failed(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    failed_problem = make_problem(user_id, text="Failed problem")
    ready_problem = make_problem(user_id, text="Ready problem")
    none_problem = make_problem(user_id, text="None problem")
    database["problems"].seed(failed_problem, ready_problem, none_problem)
    database["solution_generation_tasks"].seed(
        make_solution_task(failed_problem, user_id, status="failed"),
    )
    database["canonical_solutions"].seed(
        make_canonical_solution(ready_problem, user_id),
    )

    response = await client.get("/api/v1/problems", params={"solutionState": "failed"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(failed_problem["_id"])


@pytest.mark.asyncio
async def test_solution_state_filter_each_state(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    pending_problem = make_problem(user_id, text="Pending")
    generating_problem = make_problem(user_id, text="Generating")
    ready_problem = make_problem(user_id, text="Ready")
    none_problem = make_problem(user_id, text="None")
    database["problems"].seed(
        pending_problem, generating_problem, ready_problem, none_problem
    )
    database["solution_generation_tasks"].seed(
        make_solution_task(pending_problem, user_id, status="pending"),
        make_solution_task(generating_problem, user_id, status="generating"),
    )
    database["canonical_solutions"].seed(
        make_canonical_solution(ready_problem, user_id),
    )

    for state, expected in [
        ("pending", pending_problem),
        ("generating", generating_problem),
        ("ready", ready_problem),
        ("none", none_problem),
    ]:
        response = await client.get("/api/v1/problems", params={"solutionState": state})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1, state
        assert body["items"][0]["id"] == str(expected["_id"]), state


@pytest.mark.asyncio
async def test_solution_state_ready_precedence_over_stale_failed_task(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="Has solution and stale failed task")
    database["problems"].seed(problem)
    # Stale failed task that should be shadowed by the canonical solution.
    database["solution_generation_tasks"].seed(
        make_solution_task(problem, user_id, status="failed"),
    )
    database["canonical_solutions"].seed(make_canonical_solution(problem, user_id))

    ready_response = await client.get("/api/v1/problems", params={"solutionState": "ready"})
    assert ready_response.status_code == 200
    ready_body = ready_response.json()
    assert ready_body["total"] == 1
    assert ready_body["items"][0]["id"] == str(problem["_id"])

    failed_response = await client.get("/api/v1/problems", params={"solutionState": "failed"})
    assert failed_response.status_code == 200
    assert failed_response.json()["total"] == 0


@pytest.mark.asyncio
async def test_solution_state_composes_with_other_filters(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    matching = make_problem(
        user_id, text="Matching failed problem", tags=["algebra"], problem_type="short-answer"
    )
    other_failed = make_problem(
        user_id, text="Other failed", tags=["geometry"], problem_type="single-choice"
    )
    database["problems"].seed(matching, other_failed)
    database["solution_generation_tasks"].seed(
        make_solution_task(matching, user_id, status="failed"),
        make_solution_task(other_failed, user_id, status="failed"),
    )

    response = await client.get(
        "/api/v1/problems",
        params={
            "solutionState": "failed",
            "tag": "algebra",
            "type": "short-answer",
            "q": "matching",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(matching["_id"])


@pytest.mark.asyncio
async def test_solution_state_invalid_returns_422(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    response = await client.get("/api/v1/problems", params={"solutionState": "bogus"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_solution_state_isolates_other_users(
    problems_app: FastAPI, client: AsyncClient
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    other_user_id = problems_app.state.secondary_user["_id"]

    own_problem = make_problem(user_id, text="Own none problem")
    database["problems"].seed(own_problem)

    # Another user has a failed task and a ready solution, but for their own problems.
    other_failed_problem = make_problem(other_user_id, text="Other user failed")
    other_ready_problem = make_problem(other_user_id, text="Other user ready")
    database["problems"].seed(other_failed_problem, other_ready_problem)
    database["solution_generation_tasks"].seed(
        make_solution_task(other_failed_problem, other_user_id, status="failed"),
    )
    database["canonical_solutions"].seed(
        make_canonical_solution(other_ready_problem, other_user_id),
    )

    none_response = await client.get("/api/v1/problems", params={"solutionState": "none"})
    assert none_response.status_code == 200
    none_body = none_response.json()
    assert none_body["total"] == 1
    assert none_body["items"][0]["id"] == str(own_problem["_id"])

    failed_response = await client.get("/api/v1/problems", params={"solutionState": "failed"})
    assert failed_response.status_code == 200
    assert failed_response.json()["total"] == 0

    ready_response = await client.get("/api/v1/problems", params={"solutionState": "ready"})
    assert ready_response.status_code == 200
    assert ready_response.json()["total"] == 0


async def test_search_by_text(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem1 = make_problem(user_id, text="Solve for x in equation", tags=["algebra"])
    problem2 = make_problem(user_id, text="Find the area of triangle", tags=["geometry"])
    problem3 = make_problem(user_id, text="What is 2+2?", tags=["arithmetic"])
    database["problems"].seed(problem1, problem2, problem3)

    response = await client.get("/api/v1/problems?q=equation")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["text"] == "Solve for x in equation"


async def test_search_by_tag(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem1 = make_problem(user_id, text="Problem A", tags=["algebra"])
    problem2 = make_problem(user_id, text="Problem B", tags=["geometry"])
    database["problems"].seed(problem1, problem2)

    response = await client.get("/api/v1/problems?q=alge")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["tags"] == ["algebra"]


async def test_search_case_insensitive(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="QUADRATIC equation", tags=["Algebra"])
    database["problems"].seed(problem)

    response = await client.get("/api/v1/problems?q=quadratic")
    assert response.status_code == 200
    assert response.json()["total"] == 1

    response = await client.get("/api/v1/problems?q=ALGEBRA")
    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_search_no_match(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="Hello world", tags=["greeting"])
    database["problems"].seed(problem)

    response = await client.get("/api/v1/problems?q=nonexistent")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_search_whitespace_ignored(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="Test problem", tags=["test"])
    database["problems"].seed(problem)

    response = await client.get("/api/v1/problems?q=%20%20%20")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1


async def test_search_regex_special_chars_treated_literally(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="Price is $10.00 (USD)", tags=["money"])
    database["problems"].seed(problem)

    response = await client.get("/api/v1/problems?q=$10.00")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["text"] == "Price is $10.00 (USD)"


async def test_search_composes_with_tag_filter(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem1 = make_problem(user_id, text="Solve for x", tags=["algebra"])
    problem2 = make_problem(user_id, text="Solve for y", tags=["geometry"])
    problem3 = make_problem(user_id, text="Find area", tags=["algebra"])
    database["problems"].seed(problem1, problem2, problem3)

    response = await client.get("/api/v1/problems?q=Solve&tag=algebra")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["text"] == "Solve for x"


async def test_search_composes_with_type_filter(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem1 = make_problem(user_id, text="Solve equation", tags=["algebra"], problem_type="short-answer")
    problem2 = make_problem(user_id, text="Solve equation", tags=["algebra"], problem_type="single-choice")
    database["problems"].seed(problem1, problem2)

    response = await client.get("/api/v1/problems?q=Solve&type=short-answer")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["problemType"] == "short-answer"


async def test_search_pagination_total_reflects_filtered(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    for i in range(5):
        database["problems"].seed(
            make_problem(user_id, text=f"Problem {i} about algebra", tags=["algebra"])
        )
    database["problems"].seed(
        make_problem(user_id, text="Problem about geometry", tags=["geometry"])
    )

    response = await client.get("/api/v1/problems?q=algebra&pageSize=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_list_problems_sorts_by_add_date_before_pagination(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    base = datetime(2026, 1, 1, tzinfo=UTC)
    newest = make_problem(user_id, text="Newest", created_at=base + timedelta(days=2))
    oldest = make_problem(user_id, text="Oldest", created_at=base)
    middle = make_problem(user_id, text="Middle", created_at=base + timedelta(days=1))
    database["problems"].seed(newest, oldest, middle)

    asc_response = await client.get("/api/v1/problems?sortBy=addDate&sortOrder=asc&pageSize=2&page=2")
    desc_response = await client.get("/api/v1/problems?sortBy=addDate&sortOrder=desc")

    assert asc_response.status_code == 200
    assert [item["text"] for item in asc_response.json()["items"]] == ["Newest"]
    assert desc_response.status_code == 200
    assert [item["text"] for item in desc_response.json()["items"]] == ["Newest", "Middle", "Oldest"]


@pytest.mark.asyncio
async def test_list_problems_sorts_by_last_test_date_with_never_tested_last_and_ties(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    base = datetime(2026, 1, 1, tzinfo=UTC)
    older = make_problem(user_id, text="Older tested", last_tested_at=base)
    never = make_problem(user_id, text="Never tested", last_tested_at=None)
    same_a = make_problem(user_id, text="Same A", last_tested_at=base + timedelta(days=1))
    same_b = make_problem(user_id, text="Same B", last_tested_at=base + timedelta(days=1))
    same_a["_id"] = ObjectId("000000000000000000000001")
    same_b["_id"] = ObjectId("000000000000000000000002")
    database["problems"].seed(never, same_b, older, same_a)

    asc_response = await client.get("/api/v1/problems?sortBy=lastTestDate&sortOrder=asc")
    desc_response = await client.get("/api/v1/problems?sortBy=lastTestDate&sortOrder=desc")

    assert asc_response.status_code == 200
    assert [item["text"] for item in asc_response.json()["items"]] == [
        "Older tested",
        "Same A",
        "Same B",
        "Never tested",
    ]
    assert desc_response.status_code == 200
    assert [item["text"] for item in desc_response.json()["items"]] == [
        "Same A",
        "Same B",
        "Older tested",
        "Never tested",
    ]


@pytest.mark.asyncio
async def test_list_problems_sorts_by_selection_score_using_configured_weights(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    from app.infrastructure.config.settings import Settings, get_settings

    problems_app.dependency_overrides[get_settings] = lambda: Settings(
        problem_selection_last_wrong_weight=10.0,
        problem_selection_failure_rate_weight=0.0,
        problem_selection_recency_weight=0.0,
    )
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    now = datetime.now(UTC) - timedelta(days=30)
    low = make_problem(user_id, text="Last correct", created_at=now, last_tested_at=now)
    low["tracking"]["lastAttemptCorrect"] = True
    high = make_problem(user_id, text="Last wrong", created_at=now, last_tested_at=now)
    high["tracking"]["lastAttemptCorrect"] = False
    database["problems"].seed(low, high)

    asc_response = await client.get("/api/v1/problems?sortBy=selectionScore&sortOrder=asc")
    desc_response = await client.get("/api/v1/problems?sortBy=selectionScore&sortOrder=desc")

    assert asc_response.status_code == 200
    assert [item["text"] for item in asc_response.json()["items"]] == ["Last correct", "Last wrong"]
    assert desc_response.status_code == 200
    assert [item["text"] for item in desc_response.json()["items"]] == ["Last wrong", "Last correct"]


@pytest.mark.asyncio
async def test_list_problems_sorts_negative_selection_scores_before_pagination(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    from app.infrastructure.config.settings import Settings, get_settings

    problems_app.dependency_overrides[get_settings] = lambda: Settings(
        problem_selection_last_wrong_weight=0.0,
        problem_selection_failure_rate_weight=1.0,
        problem_selection_recency_weight=0.0,
    )
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    now = datetime.now(UTC)
    lowest = make_problem(user_id, text="Lowest raw score", last_tested_at=now)
    tie_a = make_problem(user_id, text="Tied raw score A", last_tested_at=now)
    tie_b = make_problem(user_id, text="Tied raw score B", last_tested_at=now)
    lowest["_id"] = ObjectId("000000000000000000000002")
    tie_a["_id"] = ObjectId("000000000000000000000001")
    tie_b["_id"] = ObjectId("000000000000000000000003")
    lowest["tracking"].update(correctCount=9, failedCount=0, lastAttemptCorrect=True)
    tie_a["tracking"].update(correctCount=4, failedCount=0, lastAttemptCorrect=True)
    tie_b["tracking"].update(correctCount=4, failedCount=0, lastAttemptCorrect=True)
    database["problems"].seed(tie_b, lowest, tie_a)

    asc_response = await client.get(
        "/api/v1/problems?sortBy=selectionScore&sortOrder=asc&pageSize=2"
    )
    asc_page_two_response = await client.get(
        "/api/v1/problems?sortBy=selectionScore&sortOrder=asc&pageSize=2&page=2"
    )
    desc_response = await client.get("/api/v1/problems?sortBy=selectionScore&sortOrder=desc")

    assert asc_response.status_code == 200
    assert [item["text"] for item in asc_response.json()["items"]] == [
        "Lowest raw score",
        "Tied raw score A",
    ]
    assert asc_page_two_response.status_code == 200
    assert [item["text"] for item in asc_page_two_response.json()["items"]] == ["Tied raw score B"]
    assert desc_response.status_code == 200
    assert [item["text"] for item in desc_response.json()["items"]] == [
        "Tied raw score A",
        "Tied raw score B",
        "Lowest raw score",
    ]


@pytest.mark.asyncio
async def test_list_problems_sorting_composes_with_filters_and_rejects_invalid_values(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    folder = make_folder(user_id, "Chapter")
    database["folders"].seed(folder)
    old_matching = make_problem(
        user_id,
        text="Alpha matching",
        problem_type="short-answer",
        tags=["algebra"],
        folder_id=str(folder["_id"]),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    new_matching = make_problem(
        user_id,
        text="Alpha matching newer",
        problem_type="short-answer",
        tags=["algebra"],
        folder_id=str(folder["_id"]),
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    non_matching = make_problem(
        user_id,
        text="Alpha other",
        problem_type="single-choice",
        tags=["algebra"],
        folder_id=str(folder["_id"]),
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    database["problems"].seed(non_matching, new_matching, old_matching)

    response = await client.get(
        "/api/v1/problems",
        params={
            "sortBy": "addDate",
            "sortOrder": "asc",
            "folderId": str(folder["_id"]),
            "tag": "algebra",
            "type": "short-answer",
            "q": "Alpha",
        },
    )
    bad_sort_response = await client.get("/api/v1/problems?sortBy=updatedAt")
    bad_order_response = await client.get("/api/v1/problems?sortBy=addDate&sortOrder=sideways")

    assert response.status_code == 200
    assert [item["text"] for item in response.json()["items"]] == ["Alpha matching", "Alpha matching newer"]
    assert bad_sort_response.status_code == 422
    assert bad_order_response.status_code == 422


# Folder assignment and filtering tests


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


async def test_assign_single_problem_to_folder(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    problem = make_problem(user_id, text="Problem to move")
    database["folders"].seed(folder)
    database["problems"].seed(problem)

    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/folder",
        json={"folderId": str(folder["_id"])},
    )
    assert response.status_code == 200
    body = response.json()["problem"]
    assert body["folderId"] == str(folder["_id"])

    # Verify stored
    stored = database["problems"]._documents[0]
    assert stored["folderId"] == str(folder["_id"])


async def test_assign_problem_to_unfiled(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    problem = make_problem(user_id, text="Problem in folder", folder_id=str(folder["_id"]))
    database["folders"].seed(folder)
    database["problems"].seed(problem)

    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/folder",
        json={"folderId": None},
    )
    assert response.status_code == 200
    body = response.json()["problem"]
    assert body["folderId"] is None


async def test_bulk_assign_problems_to_folder(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    p1 = make_problem(user_id, text="P1")
    p2 = make_problem(user_id, text="P2")
    p3 = make_problem(user_id, text="P3")
    database["folders"].seed(folder)
    database["problems"].seed(p1, p2, p3)

    response = await client.patch(
        "/api/v1/problems/bulk-folder",
        json={
            "problemIds": [str(p1["_id"]), str(p2["_id"])],
            "folderId": str(folder["_id"]),
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # Verify only p1 and p2 were updated
    stored_p1 = next(p for p in database["problems"]._documents if p["_id"] == p1["_id"])
    stored_p2 = next(p for p in database["problems"]._documents if p["_id"] == p2["_id"])
    stored_p3 = next(p for p in database["problems"]._documents if p["_id"] == p3["_id"])
    assert stored_p1["folderId"] == str(folder["_id"])
    assert stored_p2["folderId"] == str(folder["_id"])
    assert stored_p3.get("folderId") is None


async def test_bulk_assign_to_unfiled(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    p1 = make_problem(user_id, text="P1", folder_id=str(folder["_id"]))
    p2 = make_problem(user_id, text="P2", folder_id=str(folder["_id"]))
    database["folders"].seed(folder)
    database["problems"].seed(p1, p2)

    response = await client.patch(
        "/api/v1/problems/bulk-folder",
        json={
            "problemIds": [str(p1["_id"]), str(p2["_id"])],
            "folderId": None,
        },
    )
    assert response.status_code == 200

    stored_p1 = next(p for p in database["problems"]._documents if p["_id"] == p1["_id"])
    stored_p2 = next(p for p in database["problems"]._documents if p["_id"] == p2["_id"])
    assert stored_p1["folderId"] is None
    assert stored_p2["folderId"] is None


async def test_reject_assign_to_nonexistent_folder(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    problem = make_problem(user_id, text="P1")
    database["problems"].seed(problem)

    fake_folder_id = str(ObjectId())
    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/folder",
        json={"folderId": fake_folder_id},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_reject_assign_to_other_user_folder(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    other_user_id = problems_app.state.secondary_user["_id"]

    other_folder = make_folder(other_user_id, "Other's folder")
    problem = make_problem(user_id, text="P1")
    database["folders"].seed(other_folder)
    database["problems"].seed(problem)

    response = await client.patch(
        f"/api/v1/problems/{problem['_id']}/folder",
        json={"folderId": str(other_folder["_id"])},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_reject_bulk_move_other_user_problems(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    other_user_id = problems_app.state.secondary_user["_id"]

    folder = make_folder(user_id, "My folder")
    other_problem = make_problem(other_user_id, text="Other's problem")
    database["folders"].seed(folder)
    database["problems"].seed(other_problem)

    response = await client.patch(
        "/api/v1/problems/bulk-folder",
        json={
            "problemIds": [str(other_problem["_id"])],
            "folderId": str(folder["_id"]),
        },
    )
    assert response.status_code == 403
    assert "FORBIDDEN" in response.json()["error"]["code"]


async def test_filter_by_folder_includes_descendants(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    # Create folder hierarchy: Chapter 1 -> Section 1.1
    chapter = make_folder(user_id, "Chapter 1")
    section = make_folder(user_id, "Section 1.1", parent_id=chapter["_id"])
    database["folders"].seed(chapter, section)

    # Problems in different folders
    p_chapter = make_problem(user_id, text="In Chapter", folder_id=str(chapter["_id"]))
    p_section = make_problem(user_id, text="In Section", folder_id=str(section["_id"]))
    p_unfiled = make_problem(user_id, text="Unfiled")
    database["problems"].seed(p_chapter, p_section, p_unfiled)

    # Filter by Chapter 1 should include both chapter and section problems
    response = await client.get(f"/api/v1/problems?folderId={chapter['_id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    texts = {item["text"] for item in data["items"]}
    assert texts == {"In Chapter", "In Section"}


async def test_filter_by_unfiled(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    p_in_folder = make_problem(user_id, text="In folder", folder_id=str(folder["_id"]))
    p_unfiled_null = make_problem(user_id, text="Unfiled null", folder_id=None)
    p_unfiled_missing = make_problem(user_id, text="Unfiled missing")
    del p_unfiled_missing["folderId"]  # Remove the field entirely

    database["folders"].seed(folder)
    database["problems"].seed(p_in_folder, p_unfiled_null, p_unfiled_missing)

    response = await client.get("/api/v1/problems?folderId=unfiled")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    texts = {item["text"] for item in data["items"]}
    assert texts == {"Unfiled null", "Unfiled missing"}


async def test_omit_folder_id_returns_all_problems(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    p_in_folder = make_problem(user_id, text="In folder", folder_id=str(folder["_id"]))
    p_unfiled = make_problem(user_id, text="Unfiled")

    database["folders"].seed(folder)
    database["problems"].seed(p_in_folder, p_unfiled)

    response = await client.get("/api/v1/problems")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


async def test_folder_filter_composes_with_tag_and_search(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    p1 = make_problem(user_id, text="Algebra problem", tags=["algebra"], folder_id=str(folder["_id"]))
    p2 = make_problem(user_id, text="Geometry problem", tags=["geometry"], folder_id=str(folder["_id"]))
    p3 = make_problem(user_id, text="Another algebra", tags=["algebra"], folder_id=str(folder["_id"]))

    database["folders"].seed(folder)
    database["problems"].seed(p1, p2, p3)

    # Filter by folder + tag
    response = await client.get(f"/api/v1/problems?folderId={folder['_id']}&tag=algebra")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2

    # Filter by folder + search
    response = await client.get(f"/api/v1/problems?folderId={folder['_id']}&q=Algebra")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


async def test_problem_payload_includes_folder_id(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]

    folder = make_folder(user_id, "Chapter 1")
    problem = make_problem(user_id, text="Problem", folder_id=str(folder["_id"]))
    database["folders"].seed(folder)
    database["problems"].seed(problem)

    # List endpoint
    list_response = await client.get("/api/v1/problems")
    assert list_response.status_code == 200
    item = list_response.json()["items"][0]
    assert item["folderId"] == str(folder["_id"])

    # Detail endpoint
    detail_response = await client.get(f"/api/v1/problems/{problem['_id']}")
    assert detail_response.status_code == 200
    body = detail_response.json()["problem"]
    assert body["folderId"] == str(folder["_id"])


def make_practice_attempt(
    user_id: ObjectId,
    problem_id: ObjectId,
    *,
    grading_status: str = "correct",
    created_at: datetime | None = None,
) -> dict[str, Any]:
    now = created_at or datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "problemId": problem_id,
        "submittedAnswer": "answer",
        "gradingStatus": grading_status,
        "gradingMethod": "normalized-match",
        "createdAt": now,
    }


def make_exam_item(
    problem_id: ObjectId,
    *,
    grading_status: str = "correct",
) -> dict[str, Any]:
    return {
        "itemId": str(ObjectId()),
        "order": 1,
        "problemId": problem_id,
        "problemSnapshot": {"text": "t", "problemType": "short-answer", "subject": "math"},
        "answer": {"raw": "a", "savedAt": datetime.now(UTC)},
        "grading": {
            "status": grading_status,
            "method": None,
            "isCorrect": grading_status == "correct",
            "score": None,
            "feedback": None,
        },
    }


def make_submitted_exam(
    user_id: ObjectId,
    problem_id: ObjectId,
    *,
    submitted_at: datetime,
    grading_status: str = "correct",
    state: str = "submitted",
) -> dict[str, Any]:
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "state": state,
        "items": [make_exam_item(problem_id, grading_status=grading_status)],
        "summary": {},
        "createdAt": submitted_at - timedelta(hours=1),
        "startedAt": submitted_at - timedelta(minutes=30),
        "submittedAt": submitted_at,
        "updatedAt": submitted_at,
    }


@pytest.mark.asyncio
async def test_attempt_history_merges_practice_and_exam_newest_first(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(
        user_id,
        text="History problem",
        created_at=datetime(2026, 7, 16, 6, 0, 0, tzinfo=UTC),
    )
    database["problems"].seed(problem)

    t1 = datetime(2026, 7, 16, 8, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 7, 16, 10, 0, 0, tzinfo=UTC)
    t3 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

    database["practice_attempts"].seed(
        make_practice_attempt(user_id, problem["_id"], grading_status="correct", created_at=t1),
        make_practice_attempt(user_id, problem["_id"], grading_status="incorrect", created_at=t3),
    )
    database["exams"].seed(
        make_submitted_exam(user_id, problem["_id"], submitted_at=t2, grading_status="correct"),
    )

    response = await client.get(f"/api/v1/problems/{problem['_id']}/attempts")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["hasMore"] is False
    items = body["items"]
    # Newest first: t3 practice incorrect, t2 exam correct, t1 practice correct,
    # then the derived created activity (oldest) with a null result.
    assert items[0]["testedAt"].startswith("2026-07-16T12")
    assert items[0]["source"] == "practice"
    assert items[0]["result"] == "incorrect"
    assert items[1]["source"] == "exam"
    assert items[1]["result"] == "correct"
    assert items[2]["testedAt"].startswith("2026-07-16T08")
    assert items[2]["source"] == "practice"
    assert items[2]["result"] == "correct"
    assert items[3]["testedAt"].startswith("2026-07-16T06")
    assert items[3]["source"] == "created"
    assert items[3]["result"] is None
    assert items[3]["id"] == f"created:{problem['_id']}"
    assert all(item["id"].count(":") == 1 for item in items)


@pytest.mark.asyncio
async def test_attempt_history_preserves_pending_and_ungraded_states(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id)
    database["problems"].seed(problem)

    now = datetime.now(UTC)
    database["practice_attempts"].seed(
        make_practice_attempt(user_id, problem["_id"], grading_status="pending-review", created_at=now),
        make_practice_attempt(user_id, problem["_id"], grading_status="ungraded", created_at=now - timedelta(hours=1)),
    )
    database["exams"].seed(
        make_submitted_exam(user_id, problem["_id"], submitted_at=now - timedelta(hours=2), grading_status="pending-review"),
    )

    response = await client.get(f"/api/v1/problems/{problem['_id']}/attempts")
    assert response.status_code == 200
    results = {item["result"] for item in response.json()["items"]}
    assert results == {"pending-review", "ungraded", None}


@pytest.mark.asyncio
async def test_attempt_history_pagination_appends_without_duplication(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id)
    database["problems"].seed(problem)

    base = datetime(2026, 7, 16, 0, 0, 0, tzinfo=UTC)
    # 22 distinct timestamps plus the derived created row (23 total) to
    # exercise two pages of 20.
    for i in range(22):
        database["practice_attempts"].seed(
            make_practice_attempt(user_id, problem["_id"], grading_status="correct", created_at=base + timedelta(minutes=i))
        )

    first = await client.get(f"/api/v1/problems/{problem['_id']}/attempts", params={"limit": 20, "offset": 0})
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["total"] == 23
    assert len(first_body["items"]) == 20
    assert first_body["hasMore"] is True

    second = await client.get(f"/api/v1/problems/{problem['_id']}/attempts", params={"limit": 20, "offset": 20})
    assert second.status_code == 200
    second_body = second.json()
    assert len(second_body["items"]) == 3
    assert second_body["hasMore"] is False

    all_ids = [item["id"] for item in first_body["items"]] + [item["id"] for item in second_body["items"]]
    assert len(all_ids) == 23
    assert len(set(all_ids)) == 23
    # The derived created activity participates in pagination exactly once.
    assert all_ids.count(f"created:{problem['_id']}") == 1
    # Page 1 timestamps are strictly newer than page 2 timestamps
    assert first_body["items"][-1]["testedAt"] >= second_body["items"][0]["testedAt"]


@pytest.mark.asyncio
async def test_attempt_history_excludes_non_final_and_other_user_exams(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    other_user_id = problems_app.state.secondary_user["_id"]
    problem = make_problem(user_id)
    database["problems"].seed(problem)

    now = datetime.now(UTC)
    # Submitted exam for this user contributes
    database["exams"].seed(
        make_submitted_exam(user_id, problem["_id"], submitted_at=now, grading_status="correct", state="submitted"),
    )
    # Active exam for this user is excluded
    database["exams"].seed(
        make_submitted_exam(user_id, problem["_id"], submitted_at=now, grading_status="correct", state="in-progress"),
    )
    # Grading exam excluded
    database["exams"].seed(
        make_submitted_exam(user_id, problem["_id"], submitted_at=now, grading_status="correct", state="grading"),
    )
    # Discarded exam excluded
    database["exams"].seed(
        make_submitted_exam(user_id, problem["_id"], submitted_at=now, grading_status="correct", state="discarded"),
    )
    # Other user's submitted exam excluded
    database["exams"].seed(
        make_submitted_exam(other_user_id, problem["_id"], submitted_at=now, grading_status="correct", state="submitted"),
    )
    # Other user's practice attempt excluded
    database["practice_attempts"].seed(
        make_practice_attempt(other_user_id, problem["_id"], grading_status="correct", created_at=now),
    )

    response = await client.get(f"/api/v1/problems/{problem['_id']}/attempts")
    assert response.status_code == 200
    body = response.json()
    # Only the valid submitted exam and the derived created activity remain.
    assert body["total"] == 2
    assert {item["source"] for item in body["items"]} == {"exam", "created"}


@pytest.mark.asyncio
async def test_attempt_history_does_not_fabricate_rows_from_aggregate_counters(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    # Tracking counters claim 2 correct + 1 failed but no persisted records exist.
    problem = make_problem(user_id, text="Counters only")
    problem["tracking"] = {
        "exposureCount": 3,
        "correctCount": 2,
        "failedCount": 1,
        "lastTestedAt": datetime.now(UTC),
        "lastAttemptCorrect": False,
    }
    database["problems"].seed(problem)

    response = await client.get(f"/api/v1/problems/{problem['_id']}/attempts")
    assert response.status_code == 200
    body = response.json()
    # Aggregate counters never fabricate practice/exam rows; only the derived
    # created activity (from the problem's createdAt) is present.
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["source"] == "created"
    assert body["items"][0]["result"] is None
    assert body["hasMore"] is False


@pytest.mark.asyncio
async def test_attempt_history_missing_problem_returns_404(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    response = await client.get(f"/api/v1/problems/{ObjectId()}/attempts")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Problem not found"}}


@pytest.mark.asyncio
async def test_attempt_history_creation_only_returns_single_created_row(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    created_at = datetime(2026, 7, 16, 9, 0, 0, tzinfo=UTC)
    problem = make_problem(user_id, created_at=created_at)
    database["problems"].seed(problem)

    response = await client.get(f"/api/v1/problems/{problem['_id']}/attempts")
    assert response.status_code == 200
    body = response.json()
    # A problem with no test attempts returns exactly one derived created row.
    assert body["total"] == 1
    assert body["hasMore"] is False
    item = body["items"][0]
    assert item["source"] == "created"
    assert item["result"] is None
    assert item["id"] == f"created:{problem['_id']}"
    assert item["testedAt"] == "2026-07-16T09:00:00Z"


@pytest.mark.asyncio
async def test_attempt_history_excludes_other_problem_records(
    problems_app: FastAPI,
    client: AsyncClient,
) -> None:
    database: FakeDatabase = problems_app.state.fake_database
    user_id = problems_app.state.primary_user["_id"]
    problem = make_problem(user_id, text="Target")
    other_problem = make_problem(user_id, text="Other")
    database["problems"].seed(problem, other_problem)

    now = datetime.now(UTC)
    database["practice_attempts"].seed(
        make_practice_attempt(user_id, problem["_id"], grading_status="correct", created_at=now),
        make_practice_attempt(user_id, other_problem["_id"], grading_status="incorrect", created_at=now),
    )
    database["exams"].seed(
        make_submitted_exam(user_id, problem["_id"], submitted_at=now, grading_status="correct"),
        make_submitted_exam(user_id, other_problem["_id"], submitted_at=now, grading_status="correct"),
    )

    response = await client.get(f"/api/v1/problems/{problem['_id']}/attempts")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
