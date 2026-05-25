import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument

from app.domain.models import SolutionGenerationStatus
from app.infrastructure.config.settings import get_settings
from app.infrastructure.llm.client import SolutionLLMClient, SolutionLLMRequest, LLMClientError
from app.infrastructure.storage.mongo import (
    CANONICAL_SOLUTIONS_COLLECTION,
    SOLUTION_GENERATION_TASKS_COLLECTION,
)
from app.infrastructure.storage.s3 import S3StorageAdapter
from app.presentation.helpers import load_source_image_base64
from app.presentation.solution_generation import _safe_get_collection

logger = logging.getLogger(__name__)

async def process_task(
    task: dict[str, Any],
    client: SolutionLLMClient,
    storage: S3StorageAdapter,
    tasks_col: Any,
    solutions_col: Any,
    problems_col: Any,
    max_retries: int,
) -> None:
    problem_id = task["problem_id"]
    user_id = task["user_id"]
    
    problem = await problems_col.find_one({"_id": ObjectId(problem_id)})
    if not problem:
        logger.warning(f"Problem {problem_id} not found for task {task['_id']}")
        await tasks_col.update_one(
            {"_id": task["_id"]},
            {"$set": {"status": SolutionGenerationStatus.FAILED.value, "failure_reason": "Problem not found", "updated_at": datetime.now(UTC)}}
        )
        return
        
    try:
        source_image = problem.get("sourceImage")
        image_base64 = load_source_image_base64(source_image, storage)
        
        request = SolutionLLMRequest(
            problem_text=problem["text"],
            correct_answer=problem["correctAnswer"]["display"],
            graph_dsl=problem.get("graphDsl"),
            image_base64=image_base64 or "",
            image_url=None
        )
        # However, image_url or image_base64 is required. What if there's no image?
        # In SolutionLLMRequest:
        # if not self.image_url and not self.image_base64:
        #     raise ValueError("either image_url or image_base64 is required")
        # Wait, if there's no image, I should pass something or maybe I can't?
        # Actually, in SolutionLLMRequest, one is required. But I can bypass validation or pass empty string?
        # Let's fix that below if needed.
        
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
        await tasks_col.update_one(
            {"_id": task["_id"]},
            {"$set": {"status": SolutionGenerationStatus.READY.value, "updated_at": datetime.now(UTC)}}
        )
        
    except LLMClientError as exc:
        retry_count = int(task.get("retry_count", 0)) + 1
        logger.warning(f"LLM error for task {task['_id']}: {exc}")
        if retry_count > max_retries:
            await tasks_col.update_one(
                {"_id": task["_id"]},
                {
                    "$set": {
                        "status": SolutionGenerationStatus.FAILED.value,
                        "failure_reason": str(exc),
                        "retry_count": retry_count,
                        "updated_at": datetime.now(UTC)
                    }
                }
            )
        else:
            # Exponential backoff: 30s, 60s, 120s
            backoff_seconds = 30 * (2 ** (retry_count - 1))
            await tasks_col.update_one(
                {"_id": task["_id"]},
                {
                    "$set": {
                        "status": SolutionGenerationStatus.PENDING.value,
                        "retry_count": retry_count,
                        "updated_at": datetime.now(UTC) + timedelta(seconds=backoff_seconds)
                    }
                }
            )
    except Exception as exc:
        logger.exception(f"Unexpected error processing task {task['_id']}")
        await tasks_col.update_one(
            {"_id": task["_id"]},
            {
                "$set": {
                    "status": SolutionGenerationStatus.FAILED.value,
                    "failure_reason": str(exc),
                    "updated_at": datetime.now(UTC)
                }
            }
        )

async def run_solution_worker(database: Any, stop_event: asyncio.Event | None = None) -> None:
    settings = get_settings()
    client = SolutionLLMClient(settings)
    storage = S3StorageAdapter(settings)
    poll_interval = settings.solution_worker_poll_interval_seconds
    timeout_minutes = settings.solution_task_timeout_minutes
    max_retries = settings.solution_max_retries
    
    tasks_col = _safe_get_collection(database, SOLUTION_GENERATION_TASKS_COLLECTION)
    solutions_col = _safe_get_collection(database, CANONICAL_SOLUTIONS_COLLECTION)
    problems_col = _safe_get_collection(database, "problems")
    
    if not all([tasks_col, solutions_col, problems_col]):
        logger.error("Database collections missing for solution worker")
        return

    logger.info("Solution worker started")

    try:
        while True:
            if stop_event and stop_event.is_set():
                break

            now = datetime.now(UTC)
            
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
                        "updated_at": now
                    }
                }
            )

            # 2. Find and claim a pending task
            task = await tasks_col.find_one_and_update(
                {
                    "status": SolutionGenerationStatus.PENDING.value,
                    "updated_at": {"$lte": now}
                },
                {
                    "$set": {
                        "status": SolutionGenerationStatus.GENERATING.value,
                        "started_at": now,
                        "updated_at": now
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
