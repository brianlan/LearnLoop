import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from copy import deepcopy

import pytest
from bson import ObjectId

from app.domain.models import SolutionGenerationStatus
from app.infrastructure.llm.client import SolutionVLMResult, LLMClientError
from app.infrastructure.worker.solution_worker import run_solution_worker, process_task

class FakeCollection:
    def __init__(self):
        self._documents = []

    def seed(self, document):
        self._documents.append(document)

    async def find_one(self, query):
        for doc in self._documents:
            if _matches(doc, query):
                return deepcopy(doc)
        return None

    async def update_one(self, query, update):
        for doc in self._documents:
            if _matches(doc, query):
                for k, v in update.get("$set", {}).items():
                    doc[k] = v
                return

    async def update_many(self, query, update):
        for doc in self._documents:
            if _matches(doc, query):
                for k, v in update.get("$set", {}).items():
                    doc[k] = v

    async def find_one_and_update(self, query, update, sort=None, return_document=None):
        docs = [d for d in self._documents if _matches(d, query)]
        if not docs:
            return None
        if sort:
            docs.sort(key=lambda x: x.get(sort[0][0], 0))
        
        doc = docs[0]
        for k, v in update.get("$set", {}).items():
            doc[k] = v
        return deepcopy(doc)

    async def insert_one(self, doc):
        d = deepcopy(doc)
        if "_id" not in d: d["_id"] = ObjectId()
        self._documents.append(d)


def _matches(document, query):
    for key, value in query.items():
        if key == "$or":
            if not any(_matches(document, clause) for clause in value):
                return False
            continue

        actual = document.get(key)
        if isinstance(value, dict):
            if "$in" in value and actual not in value["$in"]:
                return False
            if "$lt" in value and (actual is None or not (actual < value["$lt"])):
                return False
            if "$lte" in value and (actual is None or not (actual <= value["$lte"])):
                return False
            if "$exists" in value and (key in document) != value["$exists"]:
                return False
            continue

        if actual != value:
            return False
    return True

class FakeLLMClient:
    def __init__(self):
        self.error_to_raise = None
        self.result_to_return = SolutionVLMResult(
            prompt_version="1",
            model="test",
            steps_markdown="steps",
            final_answer="answer",
            math_level_classification="basic",
            raw_provider_response={}
        )
        self.calls = []

    async def generate_solution(self, request):
        self.calls.append(request)
        if self.error_to_raise:
            raise self.error_to_raise
        return self.result_to_return
        
    async def aclose(self):
        pass

class FakeStorage:
    def get_object(self, bucket, key):
        return b"image"

@pytest.mark.asyncio
async def test_process_task_success():
    client = FakeLLMClient()
    storage = FakeStorage()
    tasks_col = FakeCollection()
    solutions_col = FakeCollection()
    problems_col = FakeCollection()
    
    problem_id = str(ObjectId())
    user_id = str(ObjectId())
    task_id = ObjectId()
    
    problems_col.seed({"_id": ObjectId(problem_id), "text": "prob", "correctAnswer": {"display": "ans"}, "sourceImage": {"bucket": "b", "objectKey": "k"}})
    task = {"_id": task_id, "problem_id": problem_id, "user_id": user_id, "status": "pending"}
    tasks_col.seed(task)
    
    await process_task(task, client, storage, tasks_col, solutions_col, problems_col, 3)
    
    updated_task = await tasks_col.find_one({"_id": task_id})
    assert updated_task["status"] == "ready"
    assert len(solutions_col._documents) == 1
    assert len(client.calls) == 1

@pytest.mark.asyncio
async def test_process_task_retry_and_fail():
    client = FakeLLMClient()
    client.error_to_raise = LLMClientError("err", code="err", retryable=True)
    storage = FakeStorage()
    tasks_col = FakeCollection()
    solutions_col = FakeCollection()
    problems_col = FakeCollection()
    
    problem_id = str(ObjectId())
    task_id = ObjectId()
    
    problems_col.seed({"_id": ObjectId(problem_id), "text": "prob", "correctAnswer": {"display": "ans"}, "sourceImage": {"bucket": "b", "objectKey": "k"}})
    
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

@pytest.mark.asyncio
async def test_run_worker_stuck_task_recovery():
    class FakeSettings:
        solution_worker_poll_interval_seconds = 0.01
        solution_task_timeout_minutes = 10
        solution_max_retries = 3
        solution_vlm_endpoint = "http"
        solution_vlm_model = "m"
        solution_vlm_api_key = "k"
        solution_vlm_timeout_seconds = 10
        s3_bucket = "b"
        s3_region = "r"
        s3_endpoint_url = "u"
        s3_access_key = "a"
        s3_secret_key = "s"
    
    from app.infrastructure.config import settings
    settings.get_settings = lambda: FakeSettings()
    
    class FakeDatabase:
        def __init__(self):
            self.cols = {"solution_generation_tasks": FakeCollection(), "canonical_solutions": FakeCollection(), "problems": FakeCollection()}
        def __getitem__(self, k):
            return self.cols[k]
        def get_collection(self, k):
            return self.cols[k]
            
    db = FakeDatabase()
    now = datetime.now(UTC)
    
    stuck_task = {"_id": ObjectId(), "problem_id": str(ObjectId()), "user_id": str(ObjectId()), "status": "generating", "updated_at": now - timedelta(minutes=15)}
    db.cols["solution_generation_tasks"].seed(stuck_task)
    
    stop_event = asyncio.Event()
    
    # Let it run one loop and stop
    async def stop_soon():
        await asyncio.sleep(0.05)
        stop_event.set()
        
    await asyncio.gather(run_solution_worker(db, stop_event), stop_soon())
    
    # After one loop, stuck task should be recovered to generating and then maybe processed if problem exists.
    # But since problem doesn't exist, it will be marked failed.
    updated = await db.cols["solution_generation_tasks"].find_one({"_id": stuck_task["_id"]})
    assert updated["status"] == "failed" # because it found the task, couldn't find problem


@pytest.mark.asyncio
async def test_run_worker_skips_pending_task_until_process_after() -> None:
    class FakeSettings:
        solution_worker_poll_interval_seconds = 0.01
        solution_task_timeout_minutes = 10
        solution_max_retries = 3
        solution_vlm_endpoint = "http"
        solution_vlm_model = "m"
        solution_vlm_api_key = "k"
        solution_vlm_timeout_seconds = 10
        s3_bucket = "b"
        s3_region = "r"
        s3_endpoint_url = "u"
        s3_access_key = "a"
        s3_secret_key = "s"

    from app.infrastructure.config import settings
    settings.get_settings = lambda: FakeSettings()

    class FakeDatabase:
        def __init__(self):
            self.cols = {"solution_generation_tasks": FakeCollection(), "canonical_solutions": FakeCollection(), "problems": FakeCollection()}
        def __getitem__(self, k):
            return self.cols[k]
        def get_collection(self, k):
            return self.cols[k]

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
    db.cols["solution_generation_tasks"].seed(pending_task)

    stop_event = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.05)
        stop_event.set()

    await asyncio.gather(run_solution_worker(db, stop_event), stop_soon())

    updated = await db.cols["solution_generation_tasks"].find_one({"_id": pending_task["_id"]})
    assert updated["status"] == "pending"

@pytest.mark.asyncio
async def test_process_task_no_image():
    client = FakeLLMClient()
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
