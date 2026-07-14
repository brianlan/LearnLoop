from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bson import ObjectId
from httpx import ASGITransport, AsyncClient
import pytest
from fastapi import FastAPI

from app.infrastructure.vlm.client import VLMError
from app.presentation.deps import get_app_settings

from .conftest import FakeGradingResult, FakeMongoAdapter


@pytest.mark.asyncio
async def test_wf_exam_1_generate_exam_selection_conflict_and_zero_eligible_rejection(
    app: FastAPI,
    client: AsyncClient,
    register_and_login,
    create_problem_via_api,
) -> None:
    user = await register_and_login(client, username="wf-exam-1-owner")

    problem_one = await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="2 + 2 = ?",
        problem_type="fill-in-the-blank",
        correct_answer="4",
        tags=["math"],
    )
    problem_two = await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="Capital of France",
        problem_type="short-answer",
        correct_answer="Paris",
        tags=["geography"],
    )

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 2})
    assert create_response.status_code == 201
    exam = create_response.json()["exam"]
    assert exam["state"] == "in-progress"
    assert {item["problemId"] for item in exam["items"]} == {problem_one["id"], problem_two["id"]}
    assert all(item["problem"]["correctAnswer"] is None for item in exam["items"])

    conflict_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    assert conflict_response.status_code == 409
    assert conflict_response.json() == {
        "error": {
            "code": "ACTIVE_EXAM_EXISTS",
            "message": "An active exam already exists",
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as second_client:
        await register_and_login(second_client, username="wf-exam-1-empty")
        zero_eligible_response = await second_client.post(
            "/api/v1/exams",
            json={"maxProblemCount": 1},
        )

    assert zero_eligible_response.status_code == 422
    assert zero_eligible_response.json() == {
        "error": {
            "code": "NO_ELIGIBLE_PROBLEMS",
            "message": "No eligible problems available",
        }
    }


@pytest.mark.asyncio
async def test_wf_exam_2_take_resume_and_persist_answers(
    client: AsyncClient,
    register_and_login,
    create_problem_via_api,
    find_exam_item,
) -> None:
    user = await register_and_login(client, username="wf-exam-2")

    problem = await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="5 + 6 = ?",
        problem_type="fill-in-the-blank",
        correct_answer="11",
        tags=["math"],
    )

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    assert create_response.status_code == 201
    exam = create_response.json()["exam"]
    item = find_exam_item(exam, problem_id=problem["id"])

    first_active_response = await client.get("/api/v1/exams/active")
    assert first_active_response.status_code == 200
    started_at = first_active_response.json()["exam"]["startedAt"]
    assert started_at is not None

    save_response = await client.patch(
        f"/api/v1/exams/{exam['id']}/items/{item['itemId']}/answer",
        json={"answer": "11"},
    )
    assert save_response.status_code == 200
    assert save_response.json()["item"]["answer"]["raw"] == "11"
    assert save_response.json()["item"]["answer"]["savedAt"] is not None

    resume_response = await client.get("/api/v1/exams/active")
    assert resume_response.status_code == 200
    resumed_exam = resume_response.json()["exam"]
    resumed_item = find_exam_item(resumed_exam, problem_id=problem["id"])
    assert resumed_exam["startedAt"] == started_at
    assert resumed_item["answer"]["raw"] == "11"
    assert resumed_item["answer"]["savedAt"] is not None


@pytest.mark.asyncio
async def test_wf_exam_3_submit_and_grade_updates_score_and_tracking(
    app: FastAPI,
    client: AsyncClient,
    register_and_login,
    create_problem_via_api,
    get_problem_document,
    find_exam_item,
) -> None:
    user = await register_and_login(client, username="wf-exam-3")
    app.state.fake_grading_vlm.responses = [FakeGradingResult(is_correct=True, feedback="good explanation")]

    objective_problem = await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="6 x 7 = ?",
        problem_type="fill-in-the-blank",
        correct_answer="42",
        tags=["math"],
    )
    short_problem = await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="Why is the sky blue?",
        problem_type="short-answer",
        correct_answer="Rayleigh scattering",
        tags=["science"],
        image_bytes=b"short-answer-image",
    )

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 2})
    assert create_response.status_code == 201
    exam = create_response.json()["exam"]
    objective_item = find_exam_item(exam, problem_id=objective_problem["id"])
    short_item = find_exam_item(exam, problem_id=short_problem["id"])

    await client.patch(
        f"/api/v1/exams/{exam['id']}/items/{objective_item['itemId']}/answer",
        json={"answer": "42"},
    )
    await client.patch(
        f"/api/v1/exams/{exam['id']}/items/{short_item['itemId']}/answer",
        json={"answer": "Because of scattering"},
    )

    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert submit_response.status_code == 200
    grading_exam = submit_response.json()["exam"]
    assert grading_exam["state"] == "grading"

    # Run the worker to grade items and finalize.
    from app.infrastructure.worker.exam_grading_worker import process_exam_grading_task
    from copy import deepcopy as _deepcopy
    database = app.state.fake_database
    storage = app.state.fake_storage
    tasks_col = database["exam_grading_tasks"]
    task = _deepcopy(tasks_col._documents[0])
    task["claimToken"] = "test-token"
    task["status"] = "processing"
    await tasks_col.update_one(
        {"_id": task["_id"]},
        {"$set": {"status": "processing", "claimToken": "test-token"}},
    )
    await process_exam_grading_task(
        task, database, app.state.fake_grading_vlm, storage, app.dependency_overrides[get_app_settings](), tasks_col,
        adapter=FakeMongoAdapter(),
    )

    detail_response = await client.get(f"/api/v1/exams/{exam['id']}")
    assert detail_response.status_code == 200
    submitted_exam = detail_response.json()["exam"]
    assert submitted_exam["state"] == "submitted"
    assert submitted_exam["summary"] == {
        "totalProblems": 2,
        "answeredProblems": 2,
        "gradedProblems": 2,
        "pendingProblems": 0,
        "correctProblems": 2,
        "failedProblems": 0,
        "score": 1.0,
    }

    submitted_short_item = find_exam_item(submitted_exam, problem_id=short_problem["id"])
    assert submitted_short_item["grading"]["status"] == "correct"
    assert submitted_short_item["grading"]["method"] == "vlm"
    assert submitted_short_item["problem"]["correctAnswer"]["display"] == "Rayleigh scattering"

    objective_document = await get_problem_document(objective_problem["id"])
    short_document = await get_problem_document(short_problem["id"])
    assert objective_document is not None
    assert short_document is not None
    assert objective_document["tracking"]["exposureCount"] == 1
    assert objective_document["tracking"]["correctCount"] == 1
    assert objective_document["tracking"]["failedCount"] == 0
    assert short_document["tracking"]["exposureCount"] == 1
    assert short_document["tracking"]["correctCount"] == 1
    assert short_document["tracking"]["failedCount"] == 0

    assert detail_response.json()["exam"]["summary"]["score"] == 1.0


@pytest.mark.asyncio
async def test_wf_exam_4_pending_review_then_self_report_updates_score(
    app: FastAPI,
    client: AsyncClient,
    register_and_login,
    create_problem_via_api,
    get_problem_document,
    find_exam_item,
) -> None:
    user = await register_and_login(client, username="wf-exam-4")
    app.state.fake_grading_vlm.responses = [
        VLMError("temporary", code="vlm-timeout", retryable=True),
        VLMError("still broken", code="vlm-network-error", retryable=True),
    ]

    short_problem = await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="Explain osmosis",
        problem_type="short-answer",
        correct_answer="Water moves across a membrane",
        tags=["biology"],
        image_bytes=b"osmosis-image",
    )

    create_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    assert create_response.status_code == 201
    exam = create_response.json()["exam"]
    item = find_exam_item(exam, problem_id=short_problem["id"])

    await client.patch(
        f"/api/v1/exams/{exam['id']}/items/{item['itemId']}/answer",
        json={"answer": "My explanation"},
    )
    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert submit_response.status_code == 200
    grading_exam = submit_response.json()["exam"]
    assert grading_exam["state"] == "grading"

    # Run the worker to grade the item (VLM retry then pending-review).
    from app.infrastructure.worker.exam_grading_worker import process_exam_grading_task
    from copy import deepcopy as _deepcopy
    database = app.state.fake_database
    storage = app.state.fake_storage
    tasks_col = database["exam_grading_tasks"]
    task = _deepcopy(tasks_col._documents[0])
    task["claimToken"] = "test-token"
    task["status"] = "processing"
    await tasks_col.update_one(
        {"_id": task["_id"]},
        {"$set": {"status": "processing", "claimToken": "test-token"}},
    )
    await process_exam_grading_task(
        task, database, app.state.fake_grading_vlm, storage, app.dependency_overrides[get_app_settings](), tasks_col,
        adapter=FakeMongoAdapter(),
    )

    detail_response = await client.get(f"/api/v1/exams/{exam['id']}")
    submitted_exam = detail_response.json()["exam"]
    submitted_item = submitted_exam["items"][0]
    assert submitted_item["grading"]["status"] == "pending-review"
    assert submitted_item["grading"]["retryCount"] == 1
    assert submitted_exam["summary"] == {
        "totalProblems": 1,
        "answeredProblems": 1,
        "gradedProblems": 0,
        "pendingProblems": 1,
        "correctProblems": 0,
        "failedProblems": 0,
        "score": None,
    }

    before_resolve_document = await get_problem_document(short_problem["id"])
    assert before_resolve_document is not None
    assert before_resolve_document["tracking"]["exposureCount"] == 0

    self_report_response = await client.post(
        f"/api/v1/exams/{exam['id']}/items/{item['itemId']}/self-report",
        json={"isCorrect": True},
    )
    assert self_report_response.status_code == 200
    assert self_report_response.json()["item"]["grading"]["status"] == "correct"
    assert self_report_response.json()["item"]["grading"]["method"] == "self-report"
    assert self_report_response.json()["summary"] == {
        "totalProblems": 1,
        "answeredProblems": 1,
        "gradedProblems": 1,
        "pendingProblems": 0,
        "correctProblems": 1,
        "failedProblems": 0,
        "score": 1.0,
    }

    after_resolve_document = await get_problem_document(short_problem["id"])
    assert after_resolve_document is not None
    assert after_resolve_document["tracking"]["exposureCount"] == 1
    assert after_resolve_document["tracking"]["correctCount"] == 1
    assert after_resolve_document["tracking"]["failedCount"] == 0


@pytest.mark.asyncio
async def test_list_exam_history_excludes_discarded_by_default(
    exams_app: FastAPI,
    exams_client: AsyncClient,
) -> None:
    database = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    database["exams"].seed(
        {
            "_id": ObjectId(),
            "userId": user_id,
            "state": "submitted",
            "items": [],
            "summary": {"totalProblems": 1, "answeredProblems": 1, "gradedProblems": 1, "pendingProblems": 0, "correctProblems": 1, "failedProblems": 0, "score": 1.0},
            "createdAt": datetime.now(UTC) - timedelta(days=2),
            "submittedAt": datetime.now(UTC) - timedelta(days=2),
            "updatedAt": datetime.now(UTC) - timedelta(days=2),
        },
        {
            "_id": ObjectId(),
            "userId": user_id,
            "state": "discarded",
            "items": [],
            "summary": {"totalProblems": 1, "answeredProblems": 0, "gradedProblems": 0, "pendingProblems": 0, "correctProblems": 0, "failedProblems": 0, "score": None},
            "createdAt": datetime.now(UTC) - timedelta(days=1),
            "discardedAt": datetime.now(UTC) - timedelta(days=1),
            "updatedAt": datetime.now(UTC) - timedelta(days=1),
        },
    )

    response = await exams_client.get("/api/v1/exams")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert all(item["state"] != "discarded" for item in body["items"])


@pytest.mark.asyncio
async def test_list_exam_history_includes_discarded_when_requested(
    exams_app: FastAPI,
    exams_client: AsyncClient,
) -> None:
    database = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    database["exams"].seed(
        {
            "_id": ObjectId(),
            "userId": user_id,
            "state": "submitted",
            "items": [],
            "summary": {"totalProblems": 1, "answeredProblems": 1, "gradedProblems": 1, "pendingProblems": 0, "correctProblems": 1, "failedProblems": 0, "score": 1.0},
            "createdAt": datetime.now(UTC) - timedelta(days=2),
            "submittedAt": datetime.now(UTC) - timedelta(days=2),
            "updatedAt": datetime.now(UTC) - timedelta(days=2),
        },
        {
            "_id": ObjectId(),
            "userId": user_id,
            "state": "discarded",
            "items": [],
            "summary": {"totalProblems": 1, "answeredProblems": 0, "gradedProblems": 0, "pendingProblems": 0, "correctProblems": 0, "failedProblems": 0, "score": None},
            "createdAt": datetime.now(UTC) - timedelta(days=1),
            "discardedAt": datetime.now(UTC) - timedelta(days=1),
            "updatedAt": datetime.now(UTC) - timedelta(days=1),
        },
    )

    response = await exams_client.get("/api/v1/exams", params={"includeDiscarded": "true"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    states = {item["state"] for item in body["items"]}
    assert "submitted" in states
    assert "discarded" in states


@pytest.mark.asyncio
async def test_list_exam_history_pagination_matches_filter(
    exams_app: FastAPI,
    exams_client: AsyncClient,
) -> None:
    database = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    for i in range(5):
        database["exams"].seed(
            {
                "_id": ObjectId(),
                "userId": user_id,
                "state": "submitted",
                "items": [],
                "summary": {"totalProblems": 1, "answeredProblems": 1, "gradedProblems": 1, "pendingProblems": 0, "correctProblems": 1, "failedProblems": 0, "score": 1.0},
                "createdAt": datetime.now(UTC) - timedelta(days=i),
                "submittedAt": datetime.now(UTC) - timedelta(days=i),
                "updatedAt": datetime.now(UTC) - timedelta(days=i),
            },
        )
    for i in range(3):
        database["exams"].seed(
            {
                "_id": ObjectId(),
                "userId": user_id,
                "state": "discarded",
                "items": [],
                "summary": {"totalProblems": 1, "answeredProblems": 0, "gradedProblems": 0, "pendingProblems": 0, "correctProblems": 0, "failedProblems": 0, "score": None},
                "createdAt": datetime.now(UTC) - timedelta(days=i),
                "discardedAt": datetime.now(UTC) - timedelta(days=i),
                "updatedAt": datetime.now(UTC) - timedelta(days=i),
            },
        )

    response = await exams_client.get("/api/v1/exams", params={"page": 1, "pageSize": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 3
    assert all(item["state"] != "discarded" for item in body["items"])
