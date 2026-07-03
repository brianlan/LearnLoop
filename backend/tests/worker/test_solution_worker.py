import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from bson import ObjectId

from app.domain.models import SolutionGenerationStatus
from app.infrastructure.vlm.solution_coaching_client import SolutionCoachingVLMError, SolutionVLMResult
from app.infrastructure.worker.solution_worker import run_solution_worker, process_task
from tests.conftest import FakeCollection, FakeDatabase, FakeStorage


# VLM-specific test double; kept local because it models SolutionVLMClient behavior,
# not Mongo/S3 storage shapes.
class FakeSolutionVLMClient:
    def __init__(self):
        self.error_to_raise = None
        self.result_to_return = SolutionVLMResult(
            model="test",
            steps_markdown="steps",
            final_answer="answer",
            level_classification="basic",
            raw_provider_response={}
        )
        self.calls = []
        self.closed = False

    async def generate_solution(self, request):
        self.calls.append(request)
        if self.error_to_raise:
            raise self.error_to_raise
        return self.result_to_return

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_process_task_success():
    client = FakeSolutionVLMClient()
    storage = FakeStorage()
    tasks_col = FakeCollection()
    solutions_col = FakeCollection()
    problems_col = FakeCollection()

    problem_id = str(ObjectId())
    user_id = str(ObjectId())
    task_id = ObjectId()

    problems_col.seed({"_id": ObjectId(problem_id), "text": "prob", "correctAnswer": {"display": "ans"}, "sourceImage": {"bucket": "b", "objectKey": "k"}})
    storage.seed("b", "k", b"image")
    task = {"_id": task_id, "problem_id": problem_id, "user_id": user_id, "status": "pending"}
    tasks_col.seed(task)

    await process_task(task, client, storage, tasks_col, solutions_col, problems_col, 3)

    updated_task = await tasks_col.find_one({"_id": task_id})
    assert updated_task["status"] == "ready"
    assert len(solutions_col._documents) == 1
    assert len(client.calls) == 1
    assert not client.closed  # injected client must not be closed


@pytest.mark.asyncio
async def test_process_task_retry_and_fail():
    client = FakeSolutionVLMClient()
    client.error_to_raise = SolutionCoachingVLMError("err", code="err", retryable=True)
    storage = FakeStorage()
    tasks_col = FakeCollection()
    solutions_col = FakeCollection()
    problems_col = FakeCollection()

    problem_id = str(ObjectId())
    task_id = ObjectId()

    problems_col.seed({"_id": ObjectId(problem_id), "text": "prob", "correctAnswer": {"display": "ans"}, "sourceImage": {"bucket": "b", "objectKey": "k"}})
    storage.seed("b", "k", b"image")

    # 1. First failure -> pending
    task = {"_id": task_id, "problem_id": problem_id, "user_id": "u", "status": "pending", "retry_count": 0}
    tasks_col.seed(task)
    await process_task(task, client, storage, tasks_col, solutions_col, problems_col, 3)
    updated = await tasks_col.find_one({"_id": task_id})
    assert updated["status"] == "pending"
    assert updated["retry_count"] == 1
    assert updated["updated_at"] <= updated["process_after"]
    assert updated["process_after"] - updated["updated_at"] == timedelta(seconds=30)

    # 2. Fourth failure -> failed
    task["retry_count"] = 3
    await process_task(task, client, storage, tasks_col, solutions_col, problems_col, 3)
    updated = await tasks_col.find_one({"_id": task_id})
    assert updated["status"] == "failed"
    assert updated["retry_count"] == 4
    assert not client.closed  # injected client must not be closed


@pytest.mark.asyncio
async def test_run_worker_stuck_task_recovery(monkeypatch):
    class FakeSettings:
        solution_worker_poll_interval_seconds = 0.01
        solution_task_timeout_minutes = 10
        solution_max_retries = 3
        math_solution_vlm_endpoint = "http"
        math_solution_vlm_model = "m"
        math_solution_vlm_api_key = "k"
        math_solution_vlm_timeout_seconds = 10
        english_solution_vlm_endpoint = "http-en"
        english_solution_vlm_model = "m-en"
        english_solution_vlm_api_key = "k-en"
        english_solution_vlm_timeout_seconds = 10
        s3_bucket = "b"
        s3_region = "r"
        s3_endpoint_url = "u"
        s3_access_key = "a"
        s3_secret_key = "s"

    from app.infrastructure.config import settings
    monkeypatch.setattr(settings, "get_settings", lambda: FakeSettings())

    db = FakeDatabase()
    now = datetime.now(UTC)

    stuck_task = {"_id": ObjectId(), "problem_id": str(ObjectId()), "user_id": str(ObjectId()), "status": "generating", "updated_at": now - timedelta(minutes=15)}
    db["solution_generation_tasks"].seed(stuck_task)

    stop_event = asyncio.Event()

    # Let it run one loop and stop
    async def stop_soon():
        await asyncio.sleep(0.05)
        stop_event.set()

    await asyncio.gather(run_solution_worker(db, stop_event), stop_soon())

    # After one loop, stuck task should be recovered to generating and then maybe processed if problem exists.
    # But since problem doesn't exist, it will be marked failed.
    updated = await db["solution_generation_tasks"].find_one({"_id": stuck_task["_id"]})
    assert updated["status"] == "failed"  # because it found the task, couldn't find problem


@pytest.mark.asyncio
async def test_run_worker_skips_pending_task_until_process_after(monkeypatch) -> None:
    class FakeSettings:
        solution_worker_poll_interval_seconds = 0.01
        solution_task_timeout_minutes = 10
        solution_max_retries = 3
        math_solution_vlm_endpoint = "http"
        math_solution_vlm_model = "m"
        math_solution_vlm_api_key = "k"
        math_solution_vlm_timeout_seconds = 10
        english_solution_vlm_endpoint = "http-en"
        english_solution_vlm_model = "m-en"
        english_solution_vlm_api_key = "k-en"
        english_solution_vlm_timeout_seconds = 10
        s3_bucket = "b"
        s3_region = "r"
        s3_endpoint_url = "u"
        s3_access_key = "a"
        s3_secret_key = "s"

    from app.infrastructure.config import settings
    monkeypatch.setattr(settings, "get_settings", lambda: FakeSettings())

    db = FakeDatabase()
    now = datetime.now(UTC)
    pending_task = {
        "_id": ObjectId(),
        "problem_id": str(ObjectId()),
        "user_id": str(ObjectId()),
        "status": "pending",
        "updated_at": now,
        "process_after": now + timedelta(minutes=1),
    }
    db["solution_generation_tasks"].seed(pending_task)

    stop_event = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.05)
        stop_event.set()

    await asyncio.gather(run_solution_worker(db, stop_event), stop_soon())

    updated = await db["solution_generation_tasks"].find_one({"_id": pending_task["_id"]})
    assert updated["status"] == "pending"


@pytest.mark.asyncio
async def test_process_task_no_image():
    client = FakeSolutionVLMClient()
    storage = FakeStorage()
    tasks_col = FakeCollection()
    solutions_col = FakeCollection()
    problems_col = FakeCollection()

    problem_id = str(ObjectId())
    user_id = str(ObjectId())
    task_id = ObjectId()

    problems_col.seed({"_id": ObjectId(problem_id), "text": "prob no image", "correctAnswer": {"display": "ans"}})
    task = {"_id": task_id, "problem_id": problem_id, "user_id": user_id, "status": "pending"}
    tasks_col.seed(task)

    await process_task(task, client, storage, tasks_col, solutions_col, problems_col, 3)

    updated_task = await tasks_col.find_one({"_id": task_id})
    assert updated_task["status"] == "ready"
    assert len(solutions_col._documents) == 1
    assert len(client.calls) == 1
    assert client.calls[0].image_base64 is None
    assert not client.closed  # injected client must not be closed


@pytest.mark.asyncio
async def test_process_task_closes_internal_client_on_success():
    """When process_task creates its own client, it must close it after success."""
    internal_client = FakeSolutionVLMClient()
    storage = FakeStorage()
    tasks_col = FakeCollection()
    solutions_col = FakeCollection()
    problems_col = FakeCollection()

    problem_id = str(ObjectId())
    user_id = str(ObjectId())
    task_id = ObjectId()

    problems_col.seed({"_id": ObjectId(problem_id), "text": "prob", "correctAnswer": {"display": "ans"}, "sourceImage": {"bucket": "b", "objectKey": "k"}})
    storage.seed("b", "k", b"image")
    task = {"_id": task_id, "problem_id": problem_id, "user_id": user_id, "status": "pending"}
    tasks_col.seed(task)

    with patch(
        "app.infrastructure.worker.solution_worker.SolutionVLMClient",
        return_value=internal_client,
    ):
        await process_task(task, None, storage, tasks_col, solutions_col, problems_col, 3)

    updated_task = await tasks_col.find_one({"_id": task_id})
    assert updated_task["status"] == "ready"
    assert internal_client.closed


@pytest.mark.asyncio
async def test_process_task_closes_internal_client_on_vlm_failure():
    """When process_task creates its own client, it must close it after VLM error."""
    internal_client = FakeSolutionVLMClient()
    internal_client.error_to_raise = SolutionCoachingVLMError("err", code="err", retryable=True)
    storage = FakeStorage()
    tasks_col = FakeCollection()
    solutions_col = FakeCollection()
    problems_col = FakeCollection()

    problem_id = str(ObjectId())
    task_id = ObjectId()

    problems_col.seed({"_id": ObjectId(problem_id), "text": "prob", "correctAnswer": {"display": "ans"}, "sourceImage": {"bucket": "b", "objectKey": "k"}})
    storage.seed("b", "k", b"image")
    task = {"_id": task_id, "problem_id": problem_id, "user_id": "u", "status": "pending", "retry_count": 3}
    tasks_col.seed(task)

    with patch(
        "app.infrastructure.worker.solution_worker.SolutionVLMClient",
        return_value=internal_client,
    ):
        await process_task(task, None, storage, tasks_col, solutions_col, problems_col, 3)

    updated = await tasks_col.find_one({"_id": task_id})
    assert updated["status"] == "failed"
    assert internal_client.closed


@pytest.mark.asyncio
async def test_process_task_closes_internal_client_on_unexpected_failure():
    """When process_task creates its own client, it must close it after unexpected error."""
    internal_client = FakeSolutionVLMClient()
    internal_client.error_to_raise = RuntimeError("boom")
    storage = FakeStorage()
    tasks_col = FakeCollection()
    solutions_col = FakeCollection()
    problems_col = FakeCollection()

    problem_id = str(ObjectId())
    task_id = ObjectId()

    problems_col.seed({"_id": ObjectId(problem_id), "text": "prob", "correctAnswer": {"display": "ans"}, "sourceImage": {"bucket": "b", "objectKey": "k"}})
    storage.seed("b", "k", b"image")
    task = {"_id": task_id, "problem_id": problem_id, "user_id": "u", "status": "pending"}
    tasks_col.seed(task)

    with patch(
        "app.infrastructure.worker.solution_worker.SolutionVLMClient",
        return_value=internal_client,
    ):
        await process_task(task, None, storage, tasks_col, solutions_col, problems_col, 3)

    updated = await tasks_col.find_one({"_id": task_id})
    assert updated["status"] == "failed"
    assert internal_client.closed
