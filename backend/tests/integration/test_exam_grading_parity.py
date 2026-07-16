from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from bson import ObjectId
from fastapi import FastAPI
from httpx import AsyncClient

from app.domain.models import ExamState, GradingStatus
from app.infrastructure.storage.mongo import EXAM_GRADING_TASKS_COLLECTION
from app.infrastructure.worker.exam_grading_worker import process_exam_grading_task
from app.presentation.deps import get_app_settings

from .conftest import FakeGradingResult, FakeMongoAdapter, FakeVLMClient


# ---------------------------------------------------------------------------
# Shared corpus helpers
# ---------------------------------------------------------------------------

def _make_problem(
    user_id: ObjectId,
    *,
    text: str,
    problem_type: str,
    correct_answer: str,
    subject: str = "math",
    tracking: dict[str, Any] | None = None,
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
            "display": correct_answer,
            "normalizedText": correct_answer.lower(),
            "normalizedSet": [],
            "format": "single",
        },
        "tags": ["parity"],
        "sourceImage": {
            "bucket": "learnloop-media",
            "objectKey": f"users/{user_id}/images/{ObjectId()}.png",
            "contentType": "image/png",
            "sizeBytes": 4,
            "sha256": None,
            "uploadedAt": now,
        },
        "origin": {},
        "tracking": tracking
        or {
            "exposureCount": 0,
            "correctCount": 0,
            "failedCount": 0,
            "lastTestedAt": None,
            "lastAttemptCorrect": None,
        },
        "isDeleted": False,
        "deletedAt": None,
        "isDisabled": False,
        "createdAt": now,
        "updatedAt": now,
    }


def _make_item(
    problem: dict[str, Any],
    *,
    order: int,
    answer: str | None,
    status: GradingStatus = GradingStatus.UNGRADED,
    method: str | None = None,
    is_correct: bool | None = None,
    score: float | None = None,
) -> dict[str, Any]:
    return {
        "itemId": str(ObjectId()),
        "order": order,
        "problemId": problem["_id"],
        "problemSnapshot": {
            "text": problem["text"],
            "problemType": problem["problemType"],
            "subject": problem["subject"],
            "graphDsl": None,
            "correctAnswer": deepcopy(problem["correctAnswer"]),
            "sourceImage": deepcopy(problem.get("sourceImage")),
        },
        "answer": {"raw": answer, "savedAt": datetime.now(UTC)},
        "grading": {
            "status": status.value,
            "method": method,
            "isCorrect": is_correct,
            "score": score,
            "feedback": None,
            "providerModel": None,
            "rawProviderResponse": None,
            "gradedAt": None,
            "retryCount": 0,
            "selfReportedCorrect": None,
        },
    }


def _make_grading_exam(user_id: ObjectId, items: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "state": ExamState.GRADING.value,
        "configSnapshot": {
            "maxProblemCount": len(items),
            "selectionPolicy": {
                "cooldownDays": 7,
                "lastWrongWeight": 1.0,
                "failureRateWeight": 1.0,
                "recencyWeight": 1.0,
                "minProblemAgeDays": 0,
            },
            "generatedAt": now,
        },
        "items": items,
        "summary": {
            "totalProblems": len(items),
            "answeredProblems": len(items),
            "gradedProblems": 0,
            "pendingProblems": 0,
            "correctProblems": 0,
            "failedProblems": 0,
            "score": None,
        },
        "createdAt": now,
        "startedAt": now,
        "submittedAt": None,
        "updatedAt": now,
    }


def _make_task(
    exam_id: ObjectId,
    user_id: ObjectId,
    *,
    claim_token: str | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "examId": exam_id,
        "userId": user_id,
        "status": status,
        "claimToken": claim_token,
        "leaseUntil": None,
        "error": None,
        "createdAt": now,
        "updatedAt": now,
    }


async def _run_worker(
    app: FastAPI,
    exam_id: ObjectId,
    user_id: ObjectId,
) -> None:
    """Claim the durable task for ``exam_id`` and process it through the worker."""
    database = app.state.fake_database
    storage = app.state.fake_storage
    vlm: FakeVLMClient = app.state.fake_grading_vlm
    settings = app.dependency_overrides[get_app_settings]()
    tasks_col = database[EXAM_GRADING_TASKS_COLLECTION]

    task = await tasks_col.find_one({"examId": exam_id})
    assert task is not None, "expected a durable exam grading task"
    task = deepcopy(task)
    task["claimToken"] = "parity-test-token"
    task["status"] = "processing"
    await tasks_col.update_one(
        {"_id": task["_id"]},
        {"$set": {"status": "processing", "claimToken": "parity-test-token"}},
    )
    await process_exam_grading_task(
        task,
        database,
        vlm,
        storage,
        settings,
        tasks_col,
        adapter=FakeMongoAdapter(),
    )


def _final_grading_snapshot(exam: dict[str, Any]) -> dict[str, Any]:
    """Return a stable, comparable view of item gradings and summary.

    Problem identifiers and item order are replaced with ``problemType`` so that
    two independent problem documents producing the same grading behavior compare
    equal. Order is an exam-selection concern, not a grading outcome.
    """
    return {
        "state": exam["state"],
        "summary": exam["summary"],
        "items": sorted(
            [
                {
                    "problemType": item["problemSnapshot"]["problemType"],
                    "status": item["grading"]["status"],
                    "method": item["grading"]["method"],
                    "isCorrect": item["grading"]["isCorrect"],
                    "score": item["grading"]["score"],
                    "retryCount": item["grading"]["retryCount"],
                    "selfReportedCorrect": item["grading"]["selfReportedCorrect"],
                }
                for item in exam["items"]
            ],
            key=lambda it: it["problemType"],
        ),
    }


def _tracking_snapshot(problem: dict[str, Any]) -> dict[str, Any]:
    tracking = problem.get("tracking", {})
    return {
        "exposureCount": tracking.get("exposureCount"),
        "correctCount": tracking.get("correctCount"),
        "failedCount": tracking.get("failedCount"),
        "lastAttemptCorrect": tracking.get("lastAttemptCorrect"),
    }


# ---------------------------------------------------------------------------
# Characterization tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parity_objective_only_sync_matches_worker(
    exams_app: FastAPI,
    exams_client: AsyncClient,
) -> None:
    """The same objective-only exam produces identical final state via synchronous
    submit and via the durable worker path.

    Two independent problem sets are used so the sync-path tracking updates do not
    leak into the worker-path baseline.
    """
    database = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    sync_a = _make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    sync_b = _make_problem(
        user_id,
        text="Capital of France?",
        problem_type="single-choice",
        correct_answer="Paris",
        tracking={"exposureCount": 2, "correctCount": 1, "failedCount": 1, "lastTestedAt": None, "lastAttemptCorrect": False},
    )
    database["problems"].seed(sync_a, sync_b)

    # Sync path: create exam, answer both items, submit.
    create_response = await exams_client.post("/api/v1/exams", json={"maxProblemCount": 2})
    assert create_response.status_code == 201
    sync_exam_id = create_response.json()["exam"]["id"]
    sync_items = create_response.json()["exam"]["items"]
    sync_by_pid = {item["problemId"]: item["itemId"] for item in sync_items}

    await exams_client.patch(
        f"/api/v1/exams/{sync_exam_id}/items/{sync_by_pid[str(sync_a['_id'])]}/answer",
        json={"answer": "4"},
    )
    await exams_client.patch(
        f"/api/v1/exams/{sync_exam_id}/items/{sync_by_pid[str(sync_b['_id'])]}/answer",
        json={"answer": "Paris"},
    )

    submit_response = await exams_client.post(f"/api/v1/exams/{sync_exam_id}/submit")
    assert submit_response.status_code == 200
    assert submit_response.json()["exam"]["state"] == "submitted"

    # Worker path: independent problem documents with the same initial tracking.
    worker_a = _make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    worker_b = _make_problem(
        user_id,
        text="Capital of France?",
        problem_type="single-choice",
        correct_answer="Paris",
        tracking={"exposureCount": 2, "correctCount": 1, "failedCount": 1, "lastTestedAt": None, "lastAttemptCorrect": False},
    )
    database["problems"].seed(worker_a, worker_b)

    worker_items = [
        _make_item(worker_a, order=0, answer="4"),
        _make_item(worker_b, order=1, answer="Paris"),
    ]
    worker_exam = _make_grading_exam(user_id, worker_items)
    worker_exam_id = worker_exam["_id"]
    database["exams"].seed(worker_exam)
    database[EXAM_GRADING_TASKS_COLLECTION].seed(_make_task(worker_exam_id, user_id))

    await _run_worker(exams_app, worker_exam_id, user_id)

    sync_exam = await database["exams"].find_one({"_id": ObjectId(sync_exam_id)})
    worker_exam_doc = await database["exams"].find_one({"_id": worker_exam_id})

    assert _final_grading_snapshot(sync_exam) == _final_grading_snapshot(worker_exam_doc)

    # Sync-path tracking: only the grading transaction increments it.
    sync_a_doc = await database["problems"].find_one({"_id": sync_a["_id"]})
    sync_b_doc = await database["problems"].find_one({"_id": sync_b["_id"]})
    assert _tracking_snapshot(sync_a_doc) == {"exposureCount": 1, "correctCount": 1, "failedCount": 0, "lastAttemptCorrect": True}
    assert _tracking_snapshot(sync_b_doc) == {"exposureCount": 3, "correctCount": 2, "failedCount": 1, "lastAttemptCorrect": True}

    # Worker-path tracking matches the sync-path end state.
    worker_a_doc = await database["problems"].find_one({"_id": worker_a["_id"]})
    worker_b_doc = await database["problems"].find_one({"_id": worker_b["_id"]})
    assert _tracking_snapshot(worker_a_doc) == _tracking_snapshot(sync_a_doc)
    assert _tracking_snapshot(worker_b_doc) == _tracking_snapshot(sync_b_doc)


@pytest.mark.asyncio
async def test_parity_mixed_exam_worker_produces_expected_final_state(
    exams_app: FastAPI,
    exams_client: AsyncClient,
) -> None:
    """A mixed objective + short-answer exam is always routed through the worker.
    The final state matches the deterministic outcome expected from the unit-level
    grading helpers.
    """
    database = exams_app.state.fake_database
    storage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
    user_id = exams_app.state.primary_user["_id"]

    obj = _make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    short = _make_problem(user_id, text="Explain gravity", problem_type="short-answer", correct_answer="Mass attracts mass")
    database["problems"].seed(obj, short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"img")
    vlm.responses = [FakeGradingResult(is_correct=True, feedback="good")]

    create_response = await exams_client.post("/api/v1/exams", json={"maxProblemCount": 2})
    assert create_response.status_code == 201
    exam_id = create_response.json()["exam"]["id"]
    items = create_response.json()["exam"]["items"]
    by_pid = {item["problemId"]: item["itemId"] for item in items}

    await exams_client.patch(
        f"/api/v1/exams/{exam_id}/items/{by_pid[str(obj['_id'])]}/answer",
        json={"answer": "4"},
    )
    await exams_client.patch(
        f"/api/v1/exams/{exam_id}/items/{by_pid[str(short['_id'])]}/answer",
        json={"answer": "my answer"},
    )

    submit_response = await exams_client.post(f"/api/v1/exams/{exam_id}/submit")
    assert submit_response.status_code == 200
    assert submit_response.json()["exam"]["state"] == "grading"

    await _run_worker(exams_app, ObjectId(exam_id), user_id)

    exam_doc = await database["exams"].find_one({"_id": ObjectId(exam_id)})
    assert exam_doc["state"] == ExamState.SUBMITTED.value
    assert exam_doc["summary"] == {
        "totalProblems": 2,
        "answeredProblems": 2,
        "gradedProblems": 2,
        "pendingProblems": 0,
        "correctProblems": 2,
        "failedProblems": 0,
        "score": 1.0,
    }

    short_doc = await database["problems"].find_one({"_id": short["_id"]})
    assert short_doc["tracking"]["exposureCount"] == 1
    assert short_doc["tracking"]["correctCount"] == 1


@pytest.mark.asyncio
async def test_parity_short_answer_vlm_retry_then_success(
    exams_app: FastAPI,
    exams_client: AsyncClient,
) -> None:
    """A retryable VLM failure followed by success ends with the same grading as an
    immediate success and updates tracking exactly once.
    """
    from app.infrastructure.vlm.client import VLMError

    database = exams_app.state.fake_database
    storage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
    user_id = exams_app.state.primary_user["_id"]

    short = _make_problem(user_id, text="Explain gravity", problem_type="short-answer", correct_answer="Mass attracts mass")
    database["problems"].seed(short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"img")
    vlm.responses = [
        VLMError("temporary", code="vlm-timeout", retryable=True),
        FakeGradingResult(is_correct=True, feedback="good"),
    ]

    create_response = await exams_client.post("/api/v1/exams", json={"maxProblemCount": 1})
    assert create_response.status_code == 201
    exam_id = create_response.json()["exam"]["id"]
    item_id = create_response.json()["exam"]["items"][0]["itemId"]

    await exams_client.patch(
        f"/api/v1/exams/{exam_id}/items/{item_id}/answer",
        json={"answer": "my answer"},
    )
    await exams_client.post(f"/api/v1/exams/{exam_id}/submit")

    await _run_worker(exams_app, ObjectId(exam_id), user_id)

    exam_doc = await database["exams"].find_one({"_id": ObjectId(exam_id)})
    item = exam_doc["items"][0]
    assert item["grading"]["status"] == "correct"
    assert item["grading"]["method"] == "vlm"
    assert item["grading"]["retryCount"] == 1

    short_doc = await database["problems"].find_one({"_id": short["_id"]})
    assert short_doc["tracking"]["exposureCount"] == 1
    assert short_doc["tracking"]["correctCount"] == 1


@pytest.mark.asyncio
async def test_parity_short_answer_retry_exhaustion_then_self_report_updates_tracking_once(
    exams_app: FastAPI,
    exams_client: AsyncClient,
) -> None:
    """Retry-exhausted short-answer items become pending-review. Self-report then
    updates the item, summary, and tracking exactly once, regardless of prior retries.
    """
    from app.infrastructure.vlm.client import VLMError

    database = exams_app.state.fake_database
    storage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
    user_id = exams_app.state.primary_user["_id"]

    short = _make_problem(user_id, text="Explain gravity", problem_type="short-answer", correct_answer="Mass attracts mass")
    database["problems"].seed(short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"img")
    vlm.responses = [
        VLMError("temporary", code="vlm-timeout", retryable=True),
        VLMError("still broken", code="vlm-network-error", retryable=True),
    ]

    create_response = await exams_client.post("/api/v1/exams", json={"maxProblemCount": 1})
    assert create_response.status_code == 201
    exam_id = create_response.json()["exam"]["id"]
    item_id = create_response.json()["exam"]["items"][0]["itemId"]

    await exams_client.patch(
        f"/api/v1/exams/{exam_id}/items/{item_id}/answer",
        json={"answer": "my answer"},
    )
    await exams_client.post(f"/api/v1/exams/{exam_id}/submit")
    await _run_worker(exams_app, ObjectId(exam_id), user_id)

    before_tracking_doc = await database["problems"].find_one({"_id": short["_id"]})
    assert before_tracking_doc["tracking"]["exposureCount"] == 0

    self_report_response = await exams_client.post(
        f"/api/v1/exams/{exam_id}/items/{item_id}/self-report",
        json={"isCorrect": True},
    )
    assert self_report_response.status_code == 200

    exam_doc = await database["exams"].find_one({"_id": ObjectId(exam_id)})
    item = next(it for it in exam_doc["items"] if it["itemId"] == item_id)
    assert item["grading"]["status"] == "correct"
    assert item["grading"]["method"] == "self-report"
    assert item["grading"]["selfReportedCorrect"] is True
    assert exam_doc["summary"] == {
        "totalProblems": 1,
        "answeredProblems": 1,
        "gradedProblems": 1,
        "pendingProblems": 0,
        "correctProblems": 1,
        "failedProblems": 0,
        "score": 1.0,
    }

    after_tracking_doc = await database["problems"].find_one({"_id": short["_id"]})
    assert after_tracking_doc["tracking"]["exposureCount"] == 1
    assert after_tracking_doc["tracking"]["correctCount"] == 1
    assert after_tracking_doc["tracking"]["lastAttemptCorrect"] is True


@pytest.mark.asyncio
async def test_parity_worker_restart_preserves_terminal_items_and_tracking(
    exams_app: FastAPI,
    exams_client: AsyncClient,
) -> None:
    """After a restart, terminal items are not re-graded and their tracking is not
    double-counted when the worker finalizes.
    """
    database = exams_app.state.fake_database
    storage = exams_app.state.fake_storage
    vlm: FakeVLMClient = exams_app.state.fake_grading_vlm
    user_id = exams_app.state.primary_user["_id"]

    obj = _make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    short = _make_problem(user_id, text="Explain gravity", problem_type="short-answer", correct_answer="Mass attracts mass")
    database["problems"].seed(obj, short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"img")

    # Seed a grading exam where the objective is already terminal.
    obj_item = _make_item(obj, order=0, answer="4", status=GradingStatus.CORRECT, method="normalized-match", is_correct=True, score=1.0)
    short_item = _make_item(short, order=1, answer="my answer")
    exam = _make_grading_exam(user_id, [obj_item, short_item])
    exam_id = exam["_id"]
    database["exams"].seed(exam)
    database[EXAM_GRADING_TASKS_COLLECTION].seed(_make_task(exam_id, user_id))

    vlm.responses = [FakeGradingResult(is_correct=False, feedback="no")]

    await _run_worker(exams_app, exam_id, user_id)

    exam_doc = await database["exams"].find_one({"_id": exam_id})
    assert exam_doc["state"] == ExamState.SUBMITTED.value
    assert exam_doc["summary"]["gradedProblems"] == 2
    assert exam_doc["summary"]["score"] == 0.5

    # Objective tracked exactly once despite being present before the restart.
    obj_doc = await database["problems"].find_one({"_id": obj["_id"]})
    assert obj_doc["tracking"]["exposureCount"] == 1
    assert obj_doc["tracking"]["correctCount"] == 1
    assert len(vlm.calls) == 1  # only the short-answer was graded


@pytest.mark.asyncio
async def test_parity_worker_idempotent_rerun_does_not_double_apply_tracking(
    exams_app: FastAPI,
    exams_client: AsyncClient,
) -> None:
    """If the exam is already submitted, a rerun of the worker only marks the task
    completed and leaves tracking unchanged.
    """
    database = exams_app.state.fake_database
    user_id = exams_app.state.primary_user["_id"]

    obj = _make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    database["problems"].seed(obj)

    obj_item = _make_item(obj, order=0, answer="4", status=GradingStatus.CORRECT, method="normalized-match", is_correct=True, score=1.0)
    exam = _make_grading_exam(user_id, [obj_item])
    exam["state"] = ExamState.SUBMITTED.value
    exam["submittedAt"] = datetime.now(UTC)
    exam_id = exam["_id"]
    database["exams"].seed(exam)
    await database["problems"].update_one(
        {"_id": obj["_id"]},
        {"$set": {"tracking": {"exposureCount": 1, "correctCount": 1, "failedCount": 0, "lastTestedAt": datetime.now(UTC), "lastAttemptCorrect": True}}},
    )
    database[EXAM_GRADING_TASKS_COLLECTION].seed(_make_task(exam_id, user_id))

    await _run_worker(exams_app, exam_id, user_id)

    obj_doc = await database["problems"].find_one({"_id": obj["_id"]})
    assert obj_doc["tracking"]["exposureCount"] == 1
    assert obj_doc["tracking"]["correctCount"] == 1

    task = await database[EXAM_GRADING_TASKS_COLLECTION].find_one({"examId": exam_id})
    assert task["status"] == "completed"
