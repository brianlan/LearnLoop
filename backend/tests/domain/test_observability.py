import logging
from datetime import datetime, UTC
import pytest
from bson import ObjectId

from app.domain.models import SolutionGenerationStatus
from app.observability import (
    log_solution_generation_event,
    log_coaching_event,
    get_solution_task_counts,
)


def test_log_solution_generation_event(caplog):
    with caplog.at_level(logging.INFO):
        log_solution_generation_event("enqueued", "prob-123", extra_field="test")

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.name == "learnloop.solution_generation"
    assert record.levelname == "INFO"
    assert record.message == "solution_generation:enqueued"
    assert getattr(record, "solution_generation_event") == "enqueued"
    assert getattr(record, "problem_id") == "prob-123"
    assert getattr(record, "extra_field") == "test"


def test_log_coaching_event(caplog):
    with caplog.at_level(logging.INFO):
        log_coaching_event("request", "conv-456", 2, 120.5, extra_field="test")

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.name == "learnloop.coaching"
    assert record.levelname == "INFO"
    assert record.message == "coaching:request"
    assert getattr(record, "coaching_event") == "request"
    assert getattr(record, "conversation_id") == "conv-456"
    assert getattr(record, "message_count") == 2
    assert getattr(record, "response_time_ms") == 120.5
    assert getattr(record, "extra_field") == "test"


@pytest.mark.asyncio
async def test_get_solution_task_counts():
    class FakeCursor:
        def __init__(self, items):
            self.items = items
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.index >= len(self.items):
                raise StopAsyncIteration
            item = self.items[self.index]
            self.index += 1
            return item

    class FakeTasksCollection:
        def __init__(self, aggregate_result):
            self.result = aggregate_result
            self.calls = []

        def aggregate(self, pipeline):
            self.calls.append(pipeline)
            return FakeCursor(self.result)

    class FakeDb:
        def __init__(self, collection):
            self.collection = collection

        def __getitem__(self, name):
            return self.collection

    aggregate_data = [
        {"_id": "pending", "count": 5},
        {"_id": "generating", "count": 2},
        {"_id": "ready", "count": 10},
        {"_id": "failed", "count": 1},
    ]

    col = FakeTasksCollection(aggregate_data)
    db = FakeDb(col)

    counts = await get_solution_task_counts(db)
    assert counts == {
        "pending": 5,
        "generating": 2,
        "ready": 10,
        "failed": 1
    }
    assert len(col.calls) == 1
    assert col.calls[0] == [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]

    # Test case-insensitivity and status casing normalization
    aggregate_data_caps = [
        {"_id": "PENDING", "count": 3},
        {"_id": "Generating", "count": 1},
        {"_id": "ready", "count": 4},
        {"_id": "FAILED", "count": 2},
    ]
    col = FakeTasksCollection(aggregate_data_caps)
    db = FakeDb(col)

    counts = await get_solution_task_counts(db)
    assert counts == {
        "pending": 3,
        "generating": 1,
        "ready": 4,
        "failed": 2
    }


@pytest.mark.asyncio
async def test_send_message_logs_observability(caplog):
    from app.domain.coaching.service import CoachingService
    from tests.domain.test_coaching_service import FakeDatabase, FakeCoachingVLMClient

    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db.cols["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False, "text": "prob text"})

    with caplog.at_level(logging.INFO):
        conv = await service.send_message(str(prob_id), str(user_id), "help me")

    coaching_logs = [r for r in caplog.records if r.name == "learnloop.coaching"]
    assert len(coaching_logs) == 1
    record = coaching_logs[0]
    assert record.message == "coaching:request"
    assert getattr(record, "coaching_event") == "request"
    assert getattr(record, "conversation_id") == conv.id
    assert getattr(record, "message_count") == len(conv.messages)
    assert isinstance(getattr(record, "response_time_ms"), float)
    assert getattr(record, "response_time_ms") >= 0


@pytest.mark.asyncio
async def test_enqueue_solution_logs_observability(caplog):
    from app.presentation.solution_generation import enqueue_solution_generation_task_for_problem

    class FakeCol:
        def __init__(self):
            self.docs = []

        async def find_one(self, query):
            return None

        async def insert_one(self, doc):
            self.docs.append(doc)

    class FakeDb:
        def __init__(self):
            self.cols = {
                "solution_generation_tasks": FakeCol(),
                "canonical_solutions": FakeCol(),
            }
        def __getitem__(self, name):
            return self.cols[name]

    db = FakeDb()
    problem = {"_id": ObjectId(), "userId": ObjectId()}

    with caplog.at_level(logging.INFO):
        success = await enqueue_solution_generation_task_for_problem(db, problem)

    assert success is True
    sg_logs = [r for r in caplog.records if r.name == "learnloop.solution_generation"]
    assert len(sg_logs) == 1
    record = sg_logs[0]
    assert record.message == "solution_generation:enqueued"
    assert getattr(record, "solution_generation_event") == "enqueued"
    assert getattr(record, "problem_id") == str(problem["_id"])


@pytest.mark.asyncio
async def test_worker_process_task_logs_observability(caplog):
    from app.infrastructure.worker.solution_worker import process_task

    class FakeSolutionsCol:
        def __init__(self):
            self.docs = []
        async def insert_one(self, doc):
            self.docs.append(doc)

    class FakeTasksCol:
        def __init__(self):
            self.updates = []
        async def update_one(self, query, update):
            self.updates.append((query, update))

    class FakeProblemsCol:
        def __init__(self, prob):
            self.prob = prob
        async def find_one(self, query):
            return self.prob

    class FakeSolutionVLMResult:
        def __init__(self):
            self.steps_markdown = "steps"
            self.final_answer = "ans"
            self.math_level_classification = "level"

    class FakeSolutionVLMClient:
        async def generate_solution(self, req):
            return FakeSolutionVLMResult()

    class FakeStorage:
        pass

    prob_id = ObjectId()
    user_id = ObjectId()
    task = {"_id": ObjectId(), "problem_id": str(prob_id), "user_id": str(user_id)}
    prob = {"_id": prob_id, "text": "text", "correctAnswer": {"display": "ans"}}

    tasks_col = FakeTasksCol()
    solutions_col = FakeSolutionsCol()
    problems_col = FakeProblemsCol(prob)
    client = FakeSolutionVLMClient()
    storage = FakeStorage()

    import app.infrastructure.worker.solution_worker as sw
    original_load = sw.load_source_image_base64
    sw.load_source_image_base64 = lambda img, store: None

    try:
        with caplog.at_level(logging.INFO):
            await process_task(task, client, storage, tasks_col, solutions_col, problems_col, max_retries=3)
    finally:
        sw.load_source_image_base64 = original_load

    sg_logs = [r for r in caplog.records if r.name == "learnloop.solution_generation"]
    assert len(sg_logs) >= 2

    started_log = sg_logs[0]
    assert started_log.message == "solution_generation:started"
    assert getattr(started_log, "solution_generation_event") == "started"
    assert getattr(started_log, "problem_id") == str(prob_id)

    succeeded_log = sg_logs[1]
    assert succeeded_log.message == "solution_generation:succeeded"
    assert getattr(succeeded_log, "solution_generation_event") == "succeeded"
    assert getattr(succeeded_log, "problem_id") == str(prob_id)
