from __future__ import annotations

import asyncio
import logging
import secrets
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument

from app.domain.models import ExamState, GradingStatus, ProblemType
from app.domain.state import transition_exam_state
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.storage.mongo import EXAM_GRADING_TASKS_COLLECTION, _safe_get_collection
from app.infrastructure.storage.s3 import S3StorageAdapter
from app.infrastructure.vlm.client import VLMClient, build_grading_vlm_client
from app.presentation.exam_grading import build_tracking_update, grade_item
from app.presentation.exam_helpers import TERMINAL_GRADING_STATUSES, build_exam_summary

logger = logging.getLogger(__name__)

TASK_PENDING = "pending"
TASK_PROCESSING = "processing"
TASK_COMPLETED = "completed"


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _claim_task(
    tasks_col: Any,
    *,
    now: datetime,
    lease_seconds: float,
) -> dict[str, Any] | None:
    """Atomically claim one pending or stale-leased task with an ownership token."""
    claim_token = secrets.token_hex(16)
    lease_until = now + timedelta(seconds=lease_seconds)

    # First try to claim a pending task.
    claimed = await tasks_col.find_one_and_update(
        {"status": TASK_PENDING},
        {
            "$set": {
                "status": TASK_PROCESSING,
                "claimToken": claim_token,
                "leaseUntil": lease_until,
                "updatedAt": now,
            }
        },
        sort=[("createdAt", 1)],
        return_document=ReturnDocument.AFTER,
    )
    if claimed is not None:
        return claimed

    # Then reclaim a processing task whose lease has expired.
    claimed = await tasks_col.find_one_and_update(
        {
            "status": TASK_PROCESSING,
            "leaseUntil": {"$lte": now},
        },
        {
            "$set": {
                "claimToken": claim_token,
                "leaseUntil": lease_until,
                "updatedAt": now,
            }
        },
        sort=[("createdAt", 1)],
        return_document=ReturnDocument.AFTER,
    )
    return claimed


async def _verify_ownership(
    tasks_col: Any,
    task_id: ObjectId,
    claim_token: str,
) -> bool:
    task = await tasks_col.find_one({"_id": task_id})
    if task is None:
        return False
    return task.get("status") == TASK_PROCESSING and task.get("claimToken") == claim_token


async def _refresh_lease(
    tasks_col: Any,
    task_id: ObjectId,
    claim_token: str,
    *,
    now: datetime,
    lease_seconds: float,
) -> bool:
    """Refresh the lease on a task we still own."""
    lease_until = now + timedelta(seconds=lease_seconds)
    result = await tasks_col.update_one(
        {"_id": task_id, "status": TASK_PROCESSING, "claimToken": claim_token},
        {"$set": {"leaseUntil": lease_until, "updatedAt": now}},
    )
    return result.modified_count == 1


async def _release_task(
    tasks_col: Any,
    task_id: ObjectId,
    claim_token: str,
    *,
    now: datetime,
) -> None:
    """Release ownership so another worker can reclaim the task."""
    await tasks_col.update_one(
        {"_id": task_id, "status": TASK_PROCESSING, "claimToken": claim_token},
        {
            "$set": {
                "status": TASK_PENDING,
                "claimToken": None,
                "leaseUntil": None,
                "updatedAt": now,
            }
        },
    )


async def _persist_item_result(
    database: Any,
    exam_id: ObjectId,
    item_id: str,
    grading: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Atomically update one item's grading and the recomputed partial summary.

    Uses targeted positional updates so a stale full-items array is never written
    over newer state.
    """
    exams_col = _safe_get_collection(database, "exams")
    if exams_col is None:
        return None

    fresh_exam = await exams_col.find_one({"_id": exam_id})
    if fresh_exam is None:
        return None
    if fresh_exam.get("state") != ExamState.GRADING.value:
        return None

    fresh_items = list(fresh_exam.get("items", []))
    target_index = None
    for index, item in enumerate(fresh_items):
        if str(item.get("itemId")) == item_id:
            target_index = index
            break
    if target_index is None:
        return None

    updated_items = deepcopy(fresh_items)
    updated_items[target_index]["grading"] = grading
    new_summary = build_exam_summary(updated_items)

    result = await exams_col.update_one(
        {"_id": exam_id, "items.itemId": item_id, "state": ExamState.GRADING.value},
        {
            "$set": {
                "items.$.grading": grading,
                "summary": new_summary,
                "updatedAt": now,
            }
        },
    )
    if result.modified_count == 0:
        return None
    return new_summary


async def _finalize_exam(
    database: Any,
    exam_id: ObjectId,
    user_id: ObjectId,
    task_id: ObjectId,
    claim_token: str,
    tasks_col: Any,
    *,
    now: datetime,
    adapter: Any | None = None,
) -> bool:
    """Transition the exam to submitted and mark the task completed, exactly once.

    The exam state transition, problem-tracking updates, and task completion are
    committed in a single MongoDB transaction when an adapter is available,
    matching the issue's finalization contract.
    """
    exams_col = _safe_get_collection(database, "exams")
    problems_col = _safe_get_collection(database, "problems")
    if exams_col is None or problems_col is None:
        return False

    async def _finalize(session: Any) -> bool:
        fresh_exam = await exams_col.find_one({"_id": exam_id}, session=session)
        if fresh_exam is None:
            return False
        if fresh_exam.get("state") == ExamState.SUBMITTED.value:
            # Already finalized (e.g. after restart). Mark the task completed if we own it.
            await tasks_col.update_one(
                {"_id": task_id, "status": TASK_PROCESSING, "claimToken": claim_token},
                {"$set": {"status": TASK_COMPLETED, "updatedAt": now}},
                session=session,
            )
            return True
        if fresh_exam.get("state") != ExamState.GRADING.value:
            return False

        items = list(fresh_exam.get("items", []))
        all_terminal = all(
            str(dict(item.get("grading", {})).get("status", GradingStatus.UNGRADED.value))
            in TERMINAL_GRADING_STATUSES
            for item in items
        )
        if not all_terminal:
            return False

        summary = build_exam_summary(items)
        submitted_at = now
        next_state = transition_exam_state(ExamState.GRADING, ExamState.SUBMITTED)

        # Transition the exam to submitted.
        result = await exams_col.update_one(
            {"_id": exam_id, "state": ExamState.GRADING.value},
            {
                "$set": {
                    "state": next_state.value,
                    "summary": summary,
                    "submittedAt": submitted_at,
                    "updatedAt": submitted_at,
                }
            },
            session=session,
        )
        if result.modified_count == 0:
            # Another worker finalized first.
            return False

        # Apply problem-tracking updates exactly once for non-pending-review items.
        for item in items:
            grading = dict(item.get("grading", {}))
            if grading.get("status") == GradingStatus.PENDING_REVIEW.value:
                continue
            is_correct = bool(grading.get("isCorrect"))
            problem = await problems_col.find_one(
                {"_id": item["problemId"], "userId": user_id},
                session=session,
            )
            if problem is None:
                continue
            tracking_update = build_tracking_update(
                dict(problem.get("tracking", {})),
                now=submitted_at,
                is_correct=is_correct,
            )
            await problems_col.update_one(
                {"_id": problem["_id"]},
                {"$set": {"tracking": tracking_update, "updatedAt": submitted_at}},
                session=session,
            )

        # Mark the task completed in the same transaction.
        await tasks_col.update_one(
            {"_id": task_id, "status": TASK_PROCESSING, "claimToken": claim_token},
            {"$set": {"status": TASK_COMPLETED, "updatedAt": now}},
            session=session,
        )
        return True

    if adapter is not None:
        async with adapter.start_session() as session:
            return await session.with_transaction(_finalize)
    return await _finalize(None)


async def process_exam_grading_task(
    task: dict[str, Any],
    database: Any,
    vlm_client: VLMClient,
    storage: S3StorageAdapter,
    settings: Settings,
    tasks_col: Any,
    *,
    adapter: Any | None = None,
) -> None:
    """Grade all non-terminal items of one exam sequentially, then finalize."""
    task_id = task["_id"]
    exam_id = task["examId"]
    user_id = task["userId"]
    claim_token = task["claimToken"]
    lease_seconds = settings.exam_grading_lease_seconds
    refresh_interval = settings.exam_grading_lease_refresh_seconds

    exams_col = _safe_get_collection(database, "exams")
    if exams_col is None:
        return

    # Refresh the lease in the background while grading runs.
    stop_refresh = asyncio.Event()

    async def _lease_refresher() -> None:
        while not stop_refresh.is_set():
            try:
                await asyncio.wait_for(stop_refresh.wait(), timeout=refresh_interval)
                break
            except asyncio.TimeoutError:
                pass
            still_owner = await _refresh_lease(
                tasks_col, task_id, claim_token,
                now=_utc_now(), lease_seconds=lease_seconds,
            )
            if not still_owner:
                logger.warning(
                    "Lost ownership of exam grading task %s during lease refresh", task_id
                )
                return

    refresher = asyncio.create_task(_lease_refresher())

    try:
        while True:
            if not await _verify_ownership(tasks_col, task_id, claim_token):
                logger.warning("Lost ownership of exam grading task %s", task_id)
                return

            fresh_exam = await exams_col.find_one({"_id": exam_id})
            if fresh_exam is None:
                return
            if fresh_exam.get("state") == ExamState.SUBMITTED.value:
                # Already finalized (e.g. after a prior run). Ensure task is completed.
                await tasks_col.update_one(
                    {"_id": task_id, "status": TASK_PROCESSING, "claimToken": claim_token},
                    {"$set": {"status": TASK_COMPLETED, "updatedAt": _utc_now()}},
                )
                return
            if fresh_exam.get("state") != ExamState.GRADING.value:
                return

            items = list(fresh_exam.get("items", []))
            next_item: dict[str, Any] | None = None
            for item in items:
                status = str(dict(item.get("grading", {})).get("status", GradingStatus.UNGRADED.value))
                if status not in TERMINAL_GRADING_STATUSES:
                    next_item = item
                    break

            if next_item is None:
                # All items terminal: finalize.
                finalized = await _finalize_exam(
                    database, exam_id, user_id, task_id, claim_token, tasks_col,
                    now=_utc_now(), adapter=adapter,
                )
                if not finalized:
                    logger.warning("Could not finalize exam %s for task %s", exam_id, task_id)
                return

            # Grade one item. Per-item failures become pending-review and do not stop others.
            try:
                graded = await grade_item(
                    next_item,
                    vlm_client=vlm_client,
                    storage=storage,
                    now=_utc_now(),
                )
                grading = dict(graded.get("grading", {}))
            except Exception as exc:
                logger.exception(
                    "Unexpected error grading exam item %s for exam %s",
                    next_item.get("itemId"), exam_id,
                )
                grading = {
                    "status": GradingStatus.PENDING_REVIEW.value,
                    "method": "vlm",
                    "isCorrect": None,
                    "score": None,
                    "feedback": str(exc),
                    "providerModel": None,
                    "rawProviderResponse": None,
                    "gradedAt": _utc_now(),
                    "retryCount": 0,
                    "selfReportedCorrect": None,
                }

            # Verify ownership before persisting so a stale worker cannot write.
            if not await _verify_ownership(tasks_col, task_id, claim_token):
                logger.warning("Lost ownership before persisting item result for task %s", task_id)
                return

            item_id = str(next_item.get("itemId"))
            await _persist_item_result(
                database, exam_id, item_id, grading, now=_utc_now()
            )
    finally:
        stop_refresh.set()
        try:
            await asyncio.wait_for(refresher, timeout=5.0)
        except asyncio.TimeoutError:
            refresher.cancel()
            try:
                await refresher
            except asyncio.CancelledError:
                pass


async def run_exam_grading_worker(
    database: Any,
    storage: S3StorageAdapter,
    settings: Settings,
    stop_event: asyncio.Event | None = None,
    *,
    adapter: Any | None = None,
) -> None:
    """Poll for exam grading tasks and process them sequentially."""
    if not settings.exam_grading_worker_enabled:
        return

    tasks_col = _safe_get_collection(database, EXAM_GRADING_TASKS_COLLECTION)
    if tasks_col is None:
        logger.error("Exam grading tasks collection missing")
        return

    poll_interval = settings.exam_grading_worker_poll_interval_seconds
    lease_seconds = settings.exam_grading_lease_seconds

    logger.info("Exam grading worker started")

    while True:
        if stop_event and stop_event.is_set():
            break

        now = _utc_now()
        task = await _claim_task(tasks_col, now=now, lease_seconds=lease_seconds)
        if task is None:
            if stop_event:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                except asyncio.TimeoutError:
                    continue
                break
            await asyncio.sleep(poll_interval)
            continue

        # Build a grading VLM client for this task's exam.
        vlm_client = build_grading_vlm_client(settings)
        try:
            await process_exam_grading_task(
                task, database, vlm_client, storage, settings, tasks_col,
                adapter=adapter,
            )
        except Exception:
            logger.exception("Exam grading task %s failed; releasing for retry", task.get("_id"))
            await _release_task(tasks_col, task["_id"], task["claimToken"], now=_utc_now())
        finally:
            await vlm_client.aclose()