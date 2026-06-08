import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument

from app.domain.models import SolutionGenerationStatus
from app.infrastructure.config.settings import get_settings
from app.infrastructure.vlm.solution_coaching_client import (
    SolutionCoachingVLMError,
    SolutionVLMClient,
    SolutionVLMRequest,
)
from app.infrastructure.storage.mongo import (
    CANONICAL_SOLUTIONS_COLLECTION,
    SOLUTION_GENERATION_TASKS_COLLECTION,
)
from app.infrastructure.storage.s3 import S3StorageAdapter
from app.presentation.helpers import load_source_image_base64
from app.presentation.solution_generation import _safe_get_collection

logger = logging.getLogger(__name__)
from app.observability import log_solution_generation_event


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def process_task(
    task: dict[str, Any],
    client: SolutionVLMClient,
    storage: S3StorageAdapter,
    tasks_col: Any,
    solutions_col: Any,
    problems_col: Any,
    max_retries: int,
) -> None:
    problem_id = task["problem_id"]
    user_id = task["user_id"]
    log_solution_generation_event("started", problem_id)
    
    problem = await problems_col.find_one({"_id": ObjectId(problem_id)})
    if not problem:
        now = _utc_now()
        logger.warning(f"Problem {problem_id} not found for task {task['_id']}")
        await tasks_col.update_one(
            {"_id": task["_id"]},
            {
                "$set": {
                    "status": SolutionGenerationStatus.FAILED.value,
                    "failure_reason": "Problem not found",
                    "updated_at": now,
                    "process_after": now,
                }
            }
        )
        log_solution_generation_event("failed", problem_id, failure_reason="Problem not found")
        return
        
    try:
        source_image = problem.get("sourceImage")
        image_base64 = load_source_image_base64(source_image, storage)
        
        request = SolutionVLMRequest(
            problem_text=problem["text"],
            correct_answer=problem["correctAnswer"]["display"],
            graph_dsl=problem.get("graphDsl"),
            image_base64=image_base64,
            image_url=None
        )

        
        result = await client.generate_solution(request)
        
        # Save solution
        solution = {
            "_id": ObjectId(),
            "problem_id": problem_id,
            "user_id": user_id,
            "steps_markdown": result.steps_markdown,
            "final_answer": result.final_answer,
            "math_level_classification": result.math_level_classification,
            "created_at": datetime.now(UTC)
        }
        await solutions_col.insert_one(solution)
        
        # Update task status to ready
        now = _utc_now()
        await tasks_col.update_one(
            {"_id": task["_id"]},
            {
                "$set": {
                    "status": SolutionGenerationStatus.READY.value,
                    "updated_at": now,
                    "process_after": now,
                }
            }
        )
        log_solution_generation_event("succeeded", problem_id)
        
    except SolutionCoachingVLMError as exc:
        retry_count = int(task.get("retry_count", 0)) + 1
        logger.warning(f"VLM error for task {task['_id']}: {exc}")
        if retry_count > max_retries:
            now = _utc_now()
            await tasks_col.update_one(
                {"_id": task["_id"]},
                {
                    "$set": {
                        "status": SolutionGenerationStatus.FAILED.value,
                        "failure_reason": str(exc),
                        "retry_count": retry_count,
                        "updated_at": now,
                        "process_after": now,
                    }
                }
            )
            log_solution_generation_event("failed", problem_id, failure_reason=str(exc))
        else:
            # Exponential backoff: 30s, 60s, 120s
            now = _utc_now()
            backoff_seconds = 30 * (2 ** (retry_count - 1))
            await tasks_col.update_one(
                {"_id": task["_id"]},
                {
                    "$set": {
                        "status": SolutionGenerationStatus.PENDING.value,
                        "retry_count": retry_count,
                        "updated_at": now,
                        "process_after": now + timedelta(seconds=backoff_seconds),
                    }
                }
            )
            log_solution_generation_event("retry", problem_id, retry_count=retry_count)
    except Exception as exc:
        now = _utc_now()
        logger.exception(f"Unexpected error processing task {task['_id']}")
        await tasks_col.update_one(
            {"_id": task["_id"]},
            {
                "$set": {
                    "status": SolutionGenerationStatus.FAILED.value,
                    "failure_reason": str(exc),
                    "updated_at": now,
                    "process_after": now,
                }
            }
        )
        log_solution_generation_event("failed", problem_id, failure_reason=str(exc))

async def run_solution_worker(database: Any, stop_event: asyncio.Event | None = None) -> None:
    settings = get_settings()
    client = SolutionVLMClient(settings)
    storage = S3StorageAdapter(settings)
    poll_interval = settings.solution_worker_poll_interval_seconds
    timeout_minutes = settings.solution_task_timeout_minutes
    max_retries = settings.solution_max_retries
    
    tasks_col = _safe_get_collection(database, SOLUTION_GENERATION_TASKS_COLLECTION)
    solutions_col = _safe_get_collection(database, CANONICAL_SOLUTIONS_COLLECTION)
    problems_col = _safe_get_collection(database, "problems")
    
    if tasks_col is None or solutions_col is None or problems_col is None:
        logger.error("Database collections missing for solution worker")
        return

    logger.info("Solution worker started")

    try:
        while True:
            if stop_event and stop_event.is_set():
                break

            now = _utc_now()
            
            # 1. Stuck task recovery
            stuck_threshold = now - timedelta(minutes=timeout_minutes)
            await tasks_col.update_many(
                {
                    "status": {"$in": [SolutionGenerationStatus.PENDING.value, SolutionGenerationStatus.GENERATING.value]},
                    "updated_at": {"$lt": stuck_threshold}
                },
                {
                    "$set": {
                        "status": SolutionGenerationStatus.PENDING.value,
                        "updated_at": now,
                        "process_after": now,
                    }
                }
            )

            # 2. Find and claim a pending task
            task = await tasks_col.find_one_and_update(
                {
                    "status": SolutionGenerationStatus.PENDING.value,
                    "$or": [
                        {"process_after": {"$lte": now}},
                        {"process_after": {"$exists": False}, "updated_at": {"$lte": now}},
                    ],
                },
                {
                    "$set": {
                        "status": SolutionGenerationStatus.GENERATING.value,
                        "started_at": now,
                        "updated_at": now,
                        "process_after": now,
                    }
                },
                sort=[("created_at", 1)],
                return_document=ReturnDocument.AFTER
            )

            if task is None:
                await asyncio.sleep(poll_interval)
                continue
                
            await process_task(task, client, storage, tasks_col, solutions_col, problems_col, max_retries)
            
    finally:
        await client.aclose()
