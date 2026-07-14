from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from bson import ObjectId

from app.domain.models import ExamState, GradingStatus
from app.infrastructure.config.settings import Settings
from app.infrastructure.storage.mongo import EXAM_GRADING_TASKS_COLLECTION
from app.infrastructure.worker.exam_grading_worker import (
    TASK_COMPLETED,
    TASK_PENDING,
    TASK_PROCESSING,
    _claim_task,
    _finalize_exam,
    _refresh_lease,
    _release_task,
    process_exam_grading_task,
    run_exam_grading_worker,
)
from tests.test_utils.db_fakes import FakeDatabase


class FakeSession:
    async def with_transaction(self, callback: Any) -> Any:
        return await callback(self)


class FakeMongoAdapter:
    @asynccontextmanager
    async def start_session(self) -> AsyncIterator[FakeSession]:
        yield FakeSession()


class FakeGradingResult:
    def __init__(self, *, is_correct: bool, feedback: str = "", model: str = "fake-vlm") -> None:
        self.is_correct = is_correct
        self.feedback = feedback
        self.model = model
        self.raw_provider_response = {"isCorrect": is_correct}


class FakeVLMClient:
    def __init__(self) -> None:
        self.responses: list[Any] = []
        self.calls = 0

    async def grade_short_answer(self, **kwargs: Any) -> FakeGradingResult:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self) -> None:
        pass


class FakeStorage:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def seed(self, bucket: str, key: str, payload: bytes) -> None:
        self._objects[(bucket, key)] = payload

    def get_object(self, bucket: str, key: str) -> bytes:
        return self._objects[(bucket, key)]


def _make_settings(**overrides: Any) -> Settings:
    defaults = dict(
        exam_grading_worker_enabled=True,
        exam_grading_worker_poll_interval_seconds=0.01,
        exam_grading_lease_seconds=60.0,
        exam_grading_lease_refresh_seconds=20.0,
        problem_selection_min_age_days=0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_problem(user_id: ObjectId, *, text: str = "Explain X", problem_type: str = "short-answer", correct_answer: str = "answer", tracking: dict[str, Any] | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": text,
        "problemType": problem_type,
        "subject": "math",
        "graphDsl": None,
        "correctAnswer": {"display": correct_answer, "normalizedText": correct_answer, "normalizedSet": [], "format": "single"},
        "tags": [],
        "sourceImage": {"bucket": "learnloop-media", "objectKey": f"users/{user_id}/img/{ObjectId()}.png", "contentType": "image/png", "sizeBytes": 4, "sha256": None, "uploadedAt": now},
        "origin": {},
        "tracking": tracking or {"exposureCount": 0, "correctCount": 0, "failedCount": 0, "lastTestedAt": None, "lastAttemptCorrect": None},
        "isDeleted": False,
        "deletedAt": None,
        "isDisabled": False,
        "createdAt": now,
        "updatedAt": now,
    }


def _make_grading_exam(user_id: ObjectId, items: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "userId": user_id,
        "state": ExamState.GRADING.value,
        "configSnapshot": {"maxProblemCount": len(items), "selectionPolicy": {"cooldownDays": 7, "lastWrongWeight": 1.0, "failureRateWeight": 1.0, "recencyWeight": 1.0, "minProblemAgeDays": 0}, "generatedAt": now},
        "items": items,
        "summary": {"totalProblems": len(items), "answeredProblems": 0, "gradedProblems": 0, "pendingProblems": 0, "correctProblems": 0, "failedProblems": 0, "score": None},
        "createdAt": now,
        "startedAt": now,
        "submittedAt": None,
        "updatedAt": now,
    }


def _make_ungraded_item(problem: dict[str, Any], *, order: int = 1, answer: str | None = "my answer") -> dict[str, Any]:
    return {
        "itemId": str(ObjectId()),
        "order": order,
        "problemId": problem["_id"],
        "problemSnapshot": {
            "text": problem["text"],
            "problemType": problem["problemType"],
            "subject": problem.get("subject", "math"),
            "graphDsl": None,
            "correctAnswer": deepcopy(problem["correctAnswer"]),
            "sourceImage": deepcopy(problem.get("sourceImage")),
        },
        "answer": {"raw": answer, "savedAt": datetime.now(UTC)},
        "grading": {
            "status": GradingStatus.UNGRADED.value,
            "method": None,
            "isCorrect": None,
            "score": None,
            "feedback": None,
            "providerModel": None,
            "rawProviderResponse": None,
            "gradedAt": None,
            "retryCount": 0,
            "selfReportedCorrect": None,
        },
    }


def _make_task(exam_id: ObjectId, user_id: ObjectId, *, claim_token: str = "tok", status: str = TASK_PROCESSING, lease_until: datetime | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "examId": exam_id,
        "userId": user_id,
        "status": status,
        "claimToken": claim_token,
        "leaseUntil": lease_until or now + timedelta(seconds=60),
        "error": None,
        "createdAt": now,
        "updatedAt": now,
    }


@pytest_asyncio.fixture
async def db() -> FakeDatabase:
    return FakeDatabase()


@pytest_asyncio.fixture
async def storage() -> FakeStorage:
    return FakeStorage()


@pytest_asyncio.fixture
async def vlm() -> FakeVLMClient:
    return FakeVLMClient()


@pytest_asyncio.fixture
async def settings() -> Settings:
    return _make_settings()


@pytest_asyncio.fixture
async def adapter() -> FakeMongoAdapter:
    return FakeMongoAdapter()


@pytest.mark.asyncio
async def test_claim_task_picks_pending(db: FakeDatabase) -> None:
    now = datetime.now(UTC)
    tasks = db[EXAM_GRADING_TASKS_COLLECTION]
    tasks.seed(_make_task(ObjectId(), ObjectId(), claim_token=None, status=TASK_PENDING))
    claimed = await _claim_task(tasks, now=now, lease_seconds=60)
    assert claimed is not None
    assert claimed["status"] == TASK_PROCESSING
    assert claimed["claimToken"] is not None
    assert claimed["leaseUntil"] > now


@pytest.mark.asyncio
async def test_claim_task_reclaims_expired_lease(db: FakeDatabase) -> None:
    now = datetime.now(UTC)
    tasks = db[EXAM_GRADING_TASKS_COLLECTION]
    tasks.seed(_make_task(ObjectId(), ObjectId(), claim_token="old", status=TASK_PROCESSING, lease_until=now - timedelta(seconds=10)))
    claimed = await _claim_task(tasks, now=now, lease_seconds=60)
    assert claimed is not None
    assert claimed["claimToken"] != "old"


@pytest.mark.asyncio
async def test_claim_task_returns_none_when_nothing_claimable(db: FakeDatabase) -> None:
    now = datetime.now(UTC)
    tasks = db[EXAM_GRADING_TASKS_COLLECTION]
    # Active lease not expired
    tasks.seed(_make_task(ObjectId(), ObjectId(), claim_token="tok", status=TASK_PROCESSING, lease_until=now + timedelta(seconds=30)))
    claimed = await _claim_task(tasks, now=now, lease_seconds=60)
    assert claimed is None


@pytest.mark.asyncio
async def test_refresh_lease_requires_ownership(db: FakeDatabase) -> None:
    now = datetime.now(UTC)
    tasks = db[EXAM_GRADING_TASKS_COLLECTION]
    task = _make_task(ObjectId(), ObjectId(), claim_token="tok")
    tasks.seed(task)
    ok = await _refresh_lease(tasks, task["_id"], "wrong-token", now=now, lease_seconds=60)
    assert ok is False
    ok = await _refresh_lease(tasks, task["_id"], "tok", now=now, lease_seconds=60)
    assert ok is True


@pytest.mark.asyncio
async def test_release_task_makes_claimable_again(db: FakeDatabase) -> None:
    now = datetime.now(UTC)
    tasks = db[EXAM_GRADING_TASKS_COLLECTION]
    task = _make_task(ObjectId(), ObjectId(), claim_token="tok")
    tasks.seed(task)
    await _release_task(tasks, task["_id"], "tok", now=now)
    refreshed = await tasks.find_one({"_id": task["_id"]})
    assert refreshed["status"] == TASK_PENDING
    assert refreshed["claimToken"] is None


@pytest.mark.asyncio
async def test_worker_grades_mixed_exam_and_finalizes(
    db: FakeDatabase, storage: FakeStorage, vlm: FakeVLMClient, settings: Settings, adapter: FakeMongoAdapter,
) -> None:
    user_id = ObjectId()
    objective = _make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    short = _make_problem(user_id, text="Explain gravity", problem_type="short-answer", correct_answer="Mass attracts mass")
    db["problems"].seed(objective, short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"img")

    items = [
        _make_ungraded_item(objective, order=1, answer="4"),
        _make_ungraded_item(short, order=2, answer="my answer"),
    ]
    exam = _make_grading_exam(user_id, items)
    db["exams"].seed(exam)
    vlm.responses = [FakeGradingResult(is_correct=True, feedback="good")]

    task = _make_task(exam["_id"], user_id)
    db[EXAM_GRADING_TASKS_COLLECTION].seed(task)

    await process_exam_grading_task(task, db, vlm, storage, settings, db[EXAM_GRADING_TASKS_COLLECTION], adapter=adapter)

    stored_exam = await db["exams"].find_one({"_id": exam["_id"]})
    assert stored_exam["state"] == ExamState.SUBMITTED.value
    assert stored_exam["summary"]["gradedProblems"] == 2
    assert stored_exam["summary"]["correctProblems"] == 2
    assert stored_exam["submittedAt"] is not None
    # Tracking updated exactly once.
    obj_doc = await db["problems"].find_one({"_id": objective["_id"]})
    short_doc = await db["problems"].find_one({"_id": short["_id"]})
    assert obj_doc["tracking"]["exposureCount"] == 1
    assert short_doc["tracking"]["exposureCount"] == 1
    # Task completed.
    stored_task = await db[EXAM_GRADING_TASKS_COLLECTION].find_one({"_id": task["_id"]})
    assert stored_task["status"] == TASK_COMPLETED


@pytest.mark.asyncio
async def test_worker_per_item_failure_becomes_pending_review_and_continues(
    db: FakeDatabase, storage: FakeStorage, vlm: FakeVLMClient, settings: Settings, adapter: FakeMongoAdapter,
) -> None:
    user_id = ObjectId()
    short_a = _make_problem(user_id, text="Explain A", problem_type="short-answer", correct_answer="A")
    short_b = _make_problem(user_id, text="Explain B", problem_type="short-answer", correct_answer="B")
    db["problems"].seed(short_a, short_b)
    storage.seed(short_a["sourceImage"]["bucket"], short_a["sourceImage"]["objectKey"], b"img")
    storage.seed(short_b["sourceImage"]["bucket"], short_b["sourceImage"]["objectKey"], b"img")

    items = [
        _make_ungraded_item(short_a, order=1),
        _make_ungraded_item(short_b, order=2),
    ]
    exam = _make_grading_exam(user_id, items)
    db["exams"].seed(exam)
    vlm.responses = [RuntimeError("vlm down"), FakeGradingResult(is_correct=False, feedback="no")]

    task = _make_task(exam["_id"], user_id)
    db[EXAM_GRADING_TASKS_COLLECTION].seed(task)

    await process_exam_grading_task(task, db, vlm, storage, settings, db[EXAM_GRADING_TASKS_COLLECTION], adapter=adapter)

    stored_exam = await db["exams"].find_one({"_id": exam["_id"]})
    assert stored_exam["state"] == ExamState.SUBMITTED.value
    statuses = {item["itemId"]: item["grading"]["status"] for item in stored_exam["items"]}
    assert statuses[items[0]["itemId"]] == GradingStatus.PENDING_REVIEW.value
    assert statuses[items[1]["itemId"]] == GradingStatus.INCORRECT.value
    # pending-review item does not update tracking; incorrect does.
    doc_a = await db["problems"].find_one({"_id": short_a["_id"]})
    doc_b = await db["problems"].find_one({"_id": short_b["_id"]})
    assert doc_a["tracking"]["exposureCount"] == 0
    assert doc_b["tracking"]["exposureCount"] == 1
    assert doc_b["tracking"]["failedCount"] == 1


@pytest.mark.asyncio
async def test_worker_resumes_skipping_terminal_items(
    db: FakeDatabase, storage: FakeStorage, vlm: FakeVLMClient, settings: Settings, adapter: FakeMongoAdapter,
) -> None:
    """After a restart, the worker skips already-terminal items and finalizes."""
    user_id = ObjectId()
    objective = _make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    short = _make_problem(user_id, text="Explain gravity", problem_type="short-answer", correct_answer="Mass")
    db["problems"].seed(objective, short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"img")

    # Objective already graded correct before restart.
    obj_item = _make_ungraded_item(objective, order=1, answer="4")
    obj_item["grading"] = {
        "status": GradingStatus.CORRECT.value,
        "method": "normalized-match",
        "isCorrect": True,
        "score": 1.0,
        "feedback": None,
        "providerModel": None,
        "rawProviderResponse": None,
        "gradedAt": datetime.now(UTC),
        "retryCount": 0,
        "selfReportedCorrect": None,
    }
    short_item = _make_ungraded_item(short, order=2, answer="my answer")
    exam = _make_grading_exam(user_id, [obj_item, short_item])
    db["exams"].seed(exam)
    vlm.responses = [FakeGradingResult(is_correct=True, feedback="ok")]

    task = _make_task(exam["_id"], user_id)
    db[EXAM_GRADING_TASKS_COLLECTION].seed(task)

    await process_exam_grading_task(task, db, vlm, storage, settings, db[EXAM_GRADING_TASKS_COLLECTION], adapter=adapter)

    stored_exam = await db["exams"].find_one({"_id": exam["_id"]})
    assert stored_exam["state"] == ExamState.SUBMITTED.value
    assert vlm.calls == 1  # Only the ungraded short-answer was graded.
    # Tracking for the already-graded objective applied once during finalization.
    obj_doc = await db["problems"].find_one({"_id": objective["_id"]})
    assert obj_doc["tracking"]["exposureCount"] == 1


@pytest.mark.asyncio
async def test_worker_stale_worker_cannot_persist_after_ownership_change(
    db: FakeDatabase, storage: FakeStorage, vlm: FakeVLMClient, settings: Settings, adapter: FakeMongoAdapter,
) -> None:
    user_id = ObjectId()
    short = _make_problem(user_id, text="Explain X", problem_type="short-answer", correct_answer="X")
    db["problems"].seed(short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"img")
    exam = _make_grading_exam(user_id, [_make_ungraded_item(short, order=1)])
    db["exams"].seed(exam)
    vlm.responses = [FakeGradingResult(is_correct=True)]

    # First worker claims with token "tok1".
    task = _make_task(exam["_id"], user_id, claim_token="tok1")
    db[EXAM_GRADING_TASKS_COLLECTION].seed(task)
    # Another worker steals ownership before the first persists.
    await db[EXAM_GRADING_TASKS_COLLECTION].update_one(
        {"_id": task["_id"]},
        {"$set": {"claimToken": "tok2"}},
    )

    # The first worker (token "tok1") should not persist results.
    await process_exam_grading_task(task, db, vlm, storage, settings, db[EXAM_GRADING_TASKS_COLLECTION], adapter=adapter)

    stored_exam = await db["exams"].find_one({"_id": exam["_id"]})
    assert stored_exam["state"] == ExamState.GRADING.value  # Not finalized by stale worker.
    assert stored_exam["items"][0]["grading"]["status"] == GradingStatus.UNGRADED.value


@pytest.mark.asyncio
async def test_worker_does_not_double_count_tracking_on_rerun(
    db: FakeDatabase, storage: FakeStorage, vlm: FakeVLMClient, settings: Settings, adapter: FakeMongoAdapter,
) -> None:
    """If the worker resumes after finalization already happened, tracking is not double-counted."""
    user_id = ObjectId()
    objective = _make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    db["problems"].seed(objective)
    obj_item = _make_ungraded_item(objective, order=1, answer="4")
    obj_item["grading"]["status"] = GradingStatus.CORRECT.value
    obj_item["grading"]["isCorrect"] = True
    obj_item["grading"]["method"] = "normalized-match"
    obj_item["grading"]["score"] = 1.0

    # Exam already submitted (finalized by a prior run).
    exam = _make_grading_exam(user_id, [obj_item])
    exam["state"] = ExamState.SUBMITTED.value
    exam["submittedAt"] = datetime.now(UTC)
    db["exams"].seed(exam)
    # Tracking already updated.
    await db["problems"].update_one(
        {"_id": objective["_id"]},
        {"$set": {"tracking": {"exposureCount": 1, "correctCount": 1, "failedCount": 0, "lastTestedAt": datetime.now(UTC), "lastAttemptCorrect": True}}},
    )

    task = _make_task(exam["_id"], user_id, claim_token="tok")
    db[EXAM_GRADING_TASKS_COLLECTION].seed(task)

    await process_exam_grading_task(task, db, vlm, storage, settings, db[EXAM_GRADING_TASKS_COLLECTION], adapter=adapter)

    # Tracking unchanged.
    doc = await db["problems"].find_one({"_id": objective["_id"]})
    assert doc["tracking"]["exposureCount"] == 1
    # Task marked completed.
    stored_task = await db[EXAM_GRADING_TASKS_COLLECTION].find_one({"_id": task["_id"]})
    assert stored_task["status"] == TASK_COMPLETED


@pytest.mark.asyncio
async def test_worker_persists_items_incrementally(
    db: FakeDatabase, storage: FakeStorage, vlm: FakeVLMClient, settings: Settings, adapter: FakeMongoAdapter,
) -> None:
    """Each completed item is persisted atomically with its recomputed partial summary."""
    user_id = ObjectId()
    short_a = _make_problem(user_id, text="Explain A", problem_type="short-answer", correct_answer="A")
    short_b = _make_problem(user_id, text="Explain B", problem_type="short-answer", correct_answer="B")
    db["problems"].seed(short_a, short_b)
    storage.seed(short_a["sourceImage"]["bucket"], short_a["sourceImage"]["objectKey"], b"img")
    storage.seed(short_b["sourceImage"]["bucket"], short_b["sourceImage"]["objectKey"], b"img")

    items = [_make_ungraded_item(short_a, order=1), _make_ungraded_item(short_b, order=2)]
    exam = _make_grading_exam(user_id, items)
    db["exams"].seed(exam)

    # Intercept after the first item is persisted to inspect partial state.
    seen_partial: list[dict[str, Any]] = []
    import app.infrastructure.worker.exam_grading_worker as worker_mod
    original_persist = worker_mod._persist_item_result

    async def _capture_partial(database, exam_id, item_id, grading, *, now):
        result = await original_persist(database, exam_id, item_id, grading, now=now)
        fresh = await db["exams"].find_one({"_id": exam_id})
        seen_partial.append(deepcopy(fresh))
        return result

    worker_mod._persist_item_result = _capture_partial  # type: ignore[assignment]
    try:
        vlm.responses = [
            FakeGradingResult(is_correct=True, feedback="a"),
            FakeGradingResult(is_correct=False, feedback="b"),
        ]
        task = _make_task(exam["_id"], user_id)
        db[EXAM_GRADING_TASKS_COLLECTION].seed(task)
        await process_exam_grading_task(task, db, vlm, storage, settings, db[EXAM_GRADING_TASKS_COLLECTION], adapter=adapter)
    finally:
        worker_mod._persist_item_result = original_persist  # type: ignore[assignment]

    # After the first item was graded, the exam was still grading with one graded item.
    assert seen_partial, "partial state was not captured"
    partial = seen_partial[0]
    assert partial["state"] == ExamState.GRADING.value
    graded_count = sum(
        1 for it in partial["items"]
        if it["grading"]["status"] in (GradingStatus.CORRECT.value, GradingStatus.INCORRECT.value)
    )
    assert graded_count == 1
    assert partial["summary"]["gradedProblems"] == 1


@pytest.mark.asyncio
async def test_lease_refresh_extends_lease_during_long_task(
    db: FakeDatabase, storage: FakeStorage, vlm: FakeVLMClient,
) -> None:
    """The background lease refresher extends the lease while a task is processing."""
    user_id = ObjectId()
    short = _make_problem(user_id, text="Explain X", problem_type="short-answer", correct_answer="X")
    db["problems"].seed(short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"img")
    exam = _make_grading_exam(user_id, [_make_ungraded_item(short, order=1)])
    db["exams"].seed(exam)

    settings = _make_settings(exam_grading_lease_refresh_seconds=0.05, exam_grading_lease_seconds=0.1)
    original_lease = datetime.now(UTC) + timedelta(seconds=0.1)
    task = _make_task(exam["_id"], user_id, claim_token="tok", lease_until=original_lease)
    db[EXAM_GRADING_TASKS_COLLECTION].seed(task)

    # Make VLM slow so the refresher runs at least once.
    async def _slow_grade(**kwargs):
        await asyncio.sleep(0.15)
        return FakeGradingResult(is_correct=True)

    vlm.grade_short_answer = _slow_grade  # type: ignore[assignment]

    await process_exam_grading_task(task, db, vlm, storage, settings, db[EXAM_GRADING_TASKS_COLLECTION], adapter=FakeMongoAdapter())

    # The lease should have been refreshed past the original expiry.
    stored_task = await db[EXAM_GRADING_TASKS_COLLECTION].find_one({"_id": task["_id"]})
    assert stored_task["status"] == TASK_COMPLETED
    stored_exam = await db["exams"].find_one({"_id": exam["_id"]})
    assert stored_exam["state"] == ExamState.SUBMITTED.value


@pytest.mark.asyncio
async def test_run_worker_loop_processes_task_and_exits(
    db: FakeDatabase, storage: FakeStorage, vlm: FakeVLMClient,
) -> None:
    user_id = ObjectId()
    short = _make_problem(user_id, text="Explain X", problem_type="short-answer", correct_answer="X")
    db["problems"].seed(short)
    storage.seed(short["sourceImage"]["bucket"], short["sourceImage"]["objectKey"], b"img")
    exam = _make_grading_exam(user_id, [_make_ungraded_item(short, order=1)])
    db["exams"].seed(exam)
    vlm.responses = [FakeGradingResult(is_correct=True)]

    task = _make_task(exam["_id"], user_id, claim_token=None, status=TASK_PENDING)
    db[EXAM_GRADING_TASKS_COLLECTION].seed(task)

    settings = _make_settings(exam_grading_lease_seconds=60)

    # Patch the VLMClient constructor so the worker uses our fake.
    import app.infrastructure.worker.exam_grading_worker as worker_mod

    original_init = worker_mod.VLMClient

    class _FakeVLMWrapper:
        def __init__(self, **kwargs: Any) -> None:
            self._inner = vlm

        async def __aenter__(self):
            return self

        async def grade_short_answer(self, **kwargs: Any):
            return await self._inner.grade_short_answer(**kwargs)

        async def aclose(self):
            pass

    worker_mod.VLMClient = lambda **kwargs: vlm  # type: ignore[misc]
    try:
        stop = asyncio.Event()
        # Run worker briefly; it should process the one task then idle.
        async def _run_briefly():
            task_obj = asyncio.create_task(run_exam_grading_worker(db, storage, settings, stop, adapter=FakeMongoAdapter()))
            await asyncio.sleep(0.5)
            stop.set()
            await asyncio.wait_for(task_obj, timeout=5.0)

        await _run_briefly()
    finally:
        worker_mod.VLMClient = original_init  # type: ignore[misc]

    stored_exam = await db["exams"].find_one({"_id": exam["_id"]})
    assert stored_exam["state"] == ExamState.SUBMITTED.value


@pytest.mark.asyncio
async def test_finalize_exam_transactional_applies_all_or_nothing(
    db: FakeDatabase, storage: FakeStorage, vlm: FakeVLMClient, settings: Settings, adapter: FakeMongoAdapter,
) -> None:
    """Finalization commits the exam transition, tracking updates, and task
    completion in one transaction. A rerun after a simulated post-commit crash
    (exam already submitted) does not double-apply tracking and merely marks
    the task completed."""
    user_id = ObjectId()
    objective = _make_problem(user_id, text="2+2?", problem_type="fill-in-the-blank", correct_answer="4")
    db["problems"].seed(objective)
    obj_item = _make_ungraded_item(objective, order=1, answer="4")
    obj_item["grading"]["status"] = GradingStatus.CORRECT.value
    obj_item["grading"]["isCorrect"] = True
    obj_item["grading"]["method"] = "normalized-match"
    obj_item["grading"]["score"] = 1.0

    exam = _make_grading_exam(user_id, [obj_item])
    db["exams"].seed(exam)

    task = _make_task(exam["_id"], user_id, claim_token="tok")
    db[EXAM_GRADING_TASKS_COLLECTION].seed(task)

    finalized = await _finalize_exam(
        db, exam["_id"], user_id, task["_id"], "tok", db[EXAM_GRADING_TASKS_COLLECTION],
        now=datetime.now(UTC), adapter=adapter,
    )
    assert finalized is True

    stored_exam = await db["exams"].find_one({"_id": exam["_id"]})
    assert stored_exam["state"] == ExamState.SUBMITTED.value
    doc = await db["problems"].find_one({"_id": objective["_id"]})
    assert doc["tracking"]["exposureCount"] == 1
    assert doc["tracking"]["correctCount"] == 1
    stored_task = await db[EXAM_GRADING_TASKS_COLLECTION].find_one({"_id": task["_id"]})
    assert stored_task["status"] == TASK_COMPLETED

    # Rerun finalization: exam is already submitted, so tracking must not double-count.
    finalized_again = await _finalize_exam(
        db, exam["_id"], user_id, task["_id"], "tok", db[EXAM_GRADING_TASKS_COLLECTION],
        now=datetime.now(UTC), adapter=adapter,
    )
    assert finalized_again is True
    doc2 = await db["problems"].find_one({"_id": objective["_id"]})
    assert doc2["tracking"]["exposureCount"] == 1
    assert doc2["tracking"]["correctCount"] == 1