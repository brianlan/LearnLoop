from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from random import Random
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Query

from app.domain.attempt_streak import compute_correct_streak
from app.domain.models import ExamState, GradingStatus, ProblemType, SelectionPolicyConfig
from app.domain.selection import (
    DEFAULT_SORT_CRITERIA,
    RANK_SORT_FIELDS,
    SortCriterion,
    filter_and_score_problems,
    get_eligible_problems,
    select_problems,
    sort_scored_problems,
)
from app.domain.state import transition_exam_state
from app.exam_grading import build_exam_summary, build_tracking_update, grade_item
from app.presentation.selection_config import problem_selection_config_from_settings
from app.presentation.exam_helpers import (
    exam_requires_vlm_grading,
    find_item,
    get_owned_exam,
    make_exam_item,
    requires_vlm_grading,
)
from app.presentation.problem_serialization import problem_document_to_model
from app.presentation.problems import ProblemSortOrder
from app.presentation.deps import (
    AdapterDependency,
    CurrentUserDependency,
    DatabaseDependency,
    GradingVLMDependency,
    SettingsDependency,
    StorageDependency,
)
from app.presentation.errors import ApiError
from app.presentation.exam_serialization import (
    CreateExamRequest,
    CreateExamResponse,
    ExamHistoryItemPayload,
    ExamHistoryResponse,
    ExamResponse,
    SaveAnswerRequest,
    SaveAnswerResponse,
    SelfReportRequest,
    SelfReportResponse,
    SelectionCandidatePayload,
    SelectionCandidateSortBy,
    SelectionCandidatesResponse,
    serialize_exam,
    serialize_exam_item,
    serialize_exam_summary,
)

router = APIRouter(prefix="/exams", tags=["exams"])

MANUAL_SELECTION_MAX = 30


def _validate_manual_problem_ids(problem_ids: list[str] | None) -> list[str]:
    """Shape-validate manual ``problemIds``; every failure is one 422 shape (#680).

    Deliberately no ``parse_object_id``/``get_owned_problem`` here: those map
    bad shapes to 404/500, while manual-selection failures must all funnel
    through ``422 INVALID_SELECTION`` with the offending ids in ``details``.

    Returns canonical ``str(ObjectId(...))`` spellings so duplicate detection,
    the ownership query and the order-sensitive document lookup all compare
    one identity form — uppercase/mixed-case spellings of a valid id must not
    be reported missing or treated as a distinct id (#680).
    """
    if not problem_ids:
        raise ApiError(
            422,
            "INVALID_SELECTION",
            "problemIds is required for manual mode",
            details={"problemIds": problem_ids or []},
        )
    if len(problem_ids) > MANUAL_SELECTION_MAX:
        raise ApiError(
            422,
            "INVALID_SELECTION",
            "Too many problems selected",
            details={"problemIds": problem_ids},
        )
    canonical_ids: list[str] = []
    invalid: list[str] = []
    for problem_id in problem_ids:
        try:
            canonical_ids.append(str(ObjectId(problem_id)))
        except Exception:
            # bson's InvalidId is not a ValueError subclass in all pymongo
            # versions; any construction failure means "bad id format" (#680).
            invalid.append(problem_id)
    if invalid:
        raise ApiError(
            422,
            "INVALID_SELECTION",
            "Invalid problem id format",
            details={"problemIds": invalid},
        )
    seen: set[str] = set()
    duplicates: list[str] = []
    for canonical_id in canonical_ids:
        if canonical_id in seen and canonical_id not in duplicates:
            duplicates.append(canonical_id)
        seen.add(canonical_id)
    if duplicates:
        raise ApiError(
            422,
            "INVALID_SELECTION",
            "Duplicate problem ids in selection",
            details={"problemIds": duplicates},
        )
    return canonical_ids


@router.post("", response_model=CreateExamResponse, status_code=201)
async def create_exam(
    payload: CreateExamRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
    settings: SettingsDependency,
) -> CreateExamResponse:
    query = {
        "userId": current_user["_id"],
        "state": {"$in": [ExamState.IN_PROGRESS.value, ExamState.GRADING.value]},
    }
    selection_config = problem_selection_config_from_settings(settings)
    selection_policy = SelectionPolicyConfig(
        cooldownDays=settings.problem_selection_cooldown_days,
        lastWrongWeight=settings.problem_selection_last_wrong_weight,
        failureRateWeight=settings.problem_selection_failure_rate_weight,
        recencyWeight=settings.problem_selection_recency_weight,
        minProblemAgeDays=settings.problem_selection_min_age_days,
    )

    async def _transaction(session: Any) -> dict[str, Any]:
        existing = await database["exams"].find_one(query, session=session)
        if existing is not None:
            raise ApiError(409, "ACTIVE_EXAM_EXISTS", "An active exam already exists")

        now = datetime.now(UTC)

        if payload.mode == "manual":
            # Canonical ids: uppercase/mixed-case spellings must match the
            # stored documents and never duplicate one identity (#680).
            problem_ids = _validate_manual_problem_ids(payload.problemIds)
            problem_documents = await database["problems"].find(
                {
                    "_id": {"$in": [ObjectId(pid) for pid in problem_ids]},
                    "userId": current_user["_id"],
                },
                session=session,
            ).to_list(length=None)
            document_by_id = {str(doc["_id"]): doc for doc in problem_documents}
            missing = [pid for pid in problem_ids if pid not in document_by_id]
            if missing:
                # Unknown and foreign ids are deliberately indistinguishable.
                raise ApiError(
                    422,
                    "INVALID_SELECTION",
                    "Some selected problems do not exist",
                    details={"problemIds": missing},
                )
            selected_models = [
                problem_document_to_model(doc) for doc in problem_documents
            ]
            eligible_ids = {
                problem.id
                for problem in get_eligible_problems(selected_models, selection_config, now)
            }
            ineligible = [pid for pid in problem_ids if pid not in eligible_ids]
            if ineligible:
                raise ApiError(
                    422,
                    "INELIGIBLE_PROBLEMS",
                    "Some selected problems are not exam-eligible",
                    details={"problemIds": ineligible},
                )
            selected_documents = [document_by_id[pid] for pid in problem_ids]
            max_problem_count = len(problem_ids)
        else:
            problem_documents = await database["problems"].find(
                {
                    "userId": current_user["_id"],
                    "isDeleted": False,
                    "isDisabled": {"$ne": True},
                },
                session=session,
            ).to_list(length=None)
            eligible_documents = [
                problem
                for problem in problem_documents
                if problem.get("correctAnswer")
                and str(problem.get("correctAnswer", {}).get("display", "")).strip()
            ]
            if not eligible_documents:
                raise ApiError(422, "NO_ELIGIBLE_PROBLEMS", "No eligible problems available")

            selected_models = select_problems(
                [problem_document_to_model(problem) for problem in eligible_documents],
                payload.maxProblemCount,
                selection_config,
                now=now,
                rng=Random(),
            )
            if not selected_models:
                raise ApiError(422, "NO_ELIGIBLE_PROBLEMS", "No eligible problems available")

            document_by_id = {str(problem["_id"]): problem for problem in eligible_documents}
            selected_documents = [
                document_by_id[problem.id]
                for problem in selected_models
                if problem.id is not None and problem.id in document_by_id
            ]
            max_problem_count = payload.maxProblemCount

        items = [
            make_exam_item(problem, order=index)
            for index, problem in enumerate(selected_documents, start=1)
        ]
        exam = {
            "_id": ObjectId(),
            "userId": current_user["_id"],
            "state": ExamState.IN_PROGRESS.value,
            "configSnapshot": {
                "maxProblemCount": max_problem_count,
                "selectionPolicy": selection_policy.model_dump(),
                "generatedAt": now,
                "mode": payload.mode,
            },
            "items": items,
            "summary": build_exam_summary(items),
            "createdAt": now,
            "startedAt": None,
            "submittedAt": None,
            "updatedAt": now,
        }
        await database["exams"].insert_one(exam, session=session)
        return exam

    async with adapter.start_session() as session:
        exam = await session.with_transaction(_transaction)
    return CreateExamResponse(exam=serialize_exam(exam))


@router.get("/active", response_model=ExamResponse)
async def get_active_exam(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ExamResponse:
    exam = await database["exams"].find_one(
        {
            "userId": current_user["_id"],
            "state": {"$in": [ExamState.IN_PROGRESS.value, ExamState.GRADING.value]},
        }
    )
    if exam is None:
        raise ApiError(404, "NOT_FOUND", "Active exam not found")

    if exam.get("state") == ExamState.IN_PROGRESS.value and exam.get("startedAt") is None:
        now = datetime.now(UTC)
        await database["exams"].update_one(
            {"_id": exam["_id"], "startedAt": None},
            {"$set": {"startedAt": now, "updatedAt": now}},
        )
        exam["startedAt"] = now
        exam["updatedAt"] = now

    return ExamResponse(exam=serialize_exam(exam))


_SORT_DIRECTIONS = ("asc", "desc")


def _parse_sort_criteria(
    sort: str | None,
    sort_by: str | None,
    sort_order: str | None,
) -> list[SortCriterion]:
    """Ordered ``sort`` criteria; legacy single-column params normalize to one (#708).

    Wire encoding: ``sort=field:direction,field:direction`` — comma-separated
    ``field:direction`` pairs, first pair = highest priority. ``sortBy``/
    ``sortOrder`` (the #680 single-column params) keep their meaning and are
    accepted when ``sort`` is absent: ``sortOrder`` alone still applies to the
    default ``selectionScore`` field, and omitted sorting defaults to Score
    descending. Empty list members, malformed fields, directions, and
    duplicate fields reject with the request-validation error convention.
    """
    if sort is not None:
        if sort_by is not None:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "Use either sort or sortBy, not both",
            )
        criteria: list[SortCriterion] = []
        seen: set[str] = set()
        for part in sort.split(","):
            stripped = part.strip()
            field, _, direction = stripped.partition(":")
            field = field.strip()
            direction = direction.strip()
            if (
                field not in RANK_SORT_FIELDS
                or direction not in _SORT_DIRECTIONS
                or field in seen
            ):
                raise ApiError(
                    422,
                    "VALIDATION_ERROR",
                    f"Invalid sort criteria: {stripped}",
                )
            seen.add(field)
            criteria.append((field, direction))
        return criteria
    if sort_by is not None:
        return [(sort_by, sort_order or "desc")]
    if sort_order is not None:
        # Legacy #680 behavior: a bare sortOrder applies to the default field.
        return [(DEFAULT_SORT_CRITERIA[0][0], sort_order)]
    return list(DEFAULT_SORT_CRITERIA)


async def _load_correct_streaks(
    database: Any,
    user_id: Any,
    problems: list[Any],
) -> dict[str, int | None]:
    """Batch-load trailing correct streaks for the given candidate problems (#708).

    Two collection queries total — practice attempts and submitted exams —
    regardless of candidate count; timestamps and tie-breakers reuse the
    attempt-history endpoint semantics. Creation activities and non-submitted
    exams are never attempts, so they are not fetched.
    """
    problem_oids = [ObjectId(problem.id) for problem in problems if problem.id]
    if not problem_oids:
        return {}

    practice_docs = await database["practice_attempts"].find(
        {
            "userId": user_id,
            "problemId": {"$in": problem_oids},
            "createdAt": {"$ne": None},
        }
    ).to_list(length=None)
    exam_docs = await database["exams"].find(
        {
            "userId": user_id,
            "state": ExamState.SUBMITTED.value,
            "submittedAt": {"$ne": None},
            "items.problemId": {"$in": problem_oids},
        }
    ).to_list(length=None)

    records_by_problem: dict[str, list[tuple[Any, str, str, str | None]]] = {}
    for doc in practice_docs:
        created_at = doc.get("createdAt")
        if not isinstance(created_at, datetime):
            continue
        records_by_problem.setdefault(str(doc["problemId"]), []).append(
            (
                created_at,
                "practice",
                str(doc["_id"]),
                str(doc.get("gradingStatus", "ungraded")),
            )
        )
    for exam in exam_docs:
        submitted_at = exam.get("submittedAt")
        if not isinstance(submitted_at, datetime):
            continue
        seen_problem_ids: set[str] = set()
        for item in exam.get("items", []):
            problem_id = str(item.get("problemId"))
            if problem_id in seen_problem_ids:
                continue
            seen_problem_ids.add(problem_id)
            grading = item.get("grading") or {}
            records_by_problem.setdefault(problem_id, []).append(
                (
                    submitted_at,
                    "exam",
                    str(item.get("itemId", exam["_id"])),
                    str(grading.get("status", "ungraded")),
                )
            )

    streaks: dict[str, int | None] = {}
    for problem in problems:
        if problem.id is None:
            continue
        tracking = problem.tracking
        has_aggregate = (tracking.correctCount + tracking.failedCount) > 0
        streaks[str(problem.id)] = compute_correct_streak(
            records_by_problem.get(str(problem.id), []),
            has_aggregate_attempts=has_aggregate,
        )
    return streaks


@router.get("/selection-candidates", response_model=SelectionCandidatesResponse)
async def list_selection_candidates(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    settings: SettingsDependency,
    q: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    sort_by: SelectionCandidateSortBy | None = Query(default=None, alias="sortBy"),
    sort_order: ProblemSortOrder | None = Query(default=None, alias="sortOrder"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> SelectionCandidatesResponse:
    """Eligibility-filtered, sortable candidate rows for manual exam mode (#680).

    Sorting (#708): ``sort`` carries ordered multi-column criteria; streaks
    are derived from batch-loaded judged history and ``correctStreak`` may be
    ``None`` (missing history). Ranking stays global; pagination is last.
    """
    sort_criteria = _parse_sort_criteria(sort, sort_by, sort_order)
    problem_documents = await database["problems"].find(
        {"userId": current_user["_id"], "isDeleted": False}
    ).to_list(length=None)
    scored = filter_and_score_problems(
        [problem_document_to_model(doc) for doc in problem_documents],
        problem_selection_config_from_settings(settings),
        now=datetime.now(UTC),
        q=q,
    )
    streaks = await _load_correct_streaks(
        database, current_user["_id"], [problem for problem, _ in scored]
    )
    ranked = sort_scored_problems(scored, criteria=sort_criteria, streaks=streaks)
    total = len(ranked)
    start = (page - 1) * page_size
    items = [
        SelectionCandidatePayload(
            id=str(problem.id),
            text=problem.text,
            selectionScore=score,
            createdAt=problem.createdAt,
            correctStreak=streaks.get(str(problem.id)),
            successCount=problem.tracking.correctCount,
            failedCount=problem.tracking.failedCount,
        )
        for problem, score in ranked[start : start + page_size]
    ]
    return SelectionCandidatesResponse(
        items=items, page=page, pageSize=page_size, total=total
    )


@router.get("/{exam_id}", response_model=ExamResponse)
async def get_exam_detail(
    exam_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ExamResponse:
    exam = await get_owned_exam(database, exam_id, current_user["_id"])
    return ExamResponse(exam=serialize_exam(exam))


@router.patch("/{exam_id}/items/{item_id}/answer", response_model=SaveAnswerResponse)
async def save_exam_answer(
    exam_id: str,
    item_id: str,
    payload: SaveAnswerRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> SaveAnswerResponse:
    exam = await get_owned_exam(database, exam_id, current_user["_id"])
    if exam.get("state") != ExamState.IN_PROGRESS.value:
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

    item_index, item = find_item(exam, item_id)
    now = datetime.now(UTC)
    new_answer = {"raw": payload.answer, "savedAt": now}
    # Conditional write: only persists if the exam is still in-progress,
    # so a submission that raced with this request cannot be overwritten.
    updated_item = deepcopy(item)
    updated_item["answer"] = new_answer
    items = deepcopy(list(exam.get("items", [])))
    items[item_index] = updated_item
    result = await database["exams"].update_one(
        {
            "_id": exam["_id"],
            "state": ExamState.IN_PROGRESS.value,
        },
        {
            "$set": {
                "items": items,
                "summary": build_exam_summary(items),
                "updatedAt": now,
            }
        },
    )
    if result.modified_count == 0:
        # The exam transitioned out of in-progress between read and write.
        current = await database["exams"].find_one({"_id": exam["_id"]})
        if current is not None and current.get("state") != ExamState.IN_PROGRESS.value:
            raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")
        # State matched but item disappeared or another issue; treat as not found.
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")
    return SaveAnswerResponse(item=serialize_exam_item(updated_item, include_correct_answer=False))


@router.post("/{exam_id}/submit", response_model=ExamResponse)
async def submit_exam(
    exam_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
    vlm_client: GradingVLMDependency,
    storage: StorageDependency,
) -> ExamResponse:
    exam = await get_owned_exam(database, exam_id, current_user["_id"])
    state = exam.get("state")
    if state == ExamState.GRADING.value:
        # Idempotent retry: return the already-grading exam without creating a new task.
        return ExamResponse(exam=serialize_exam(exam))
    if state == ExamState.SUBMITTED.value:
        # Idempotent retry: return the already-submitted exam.
        return ExamResponse(exam=serialize_exam(exam))
    if state != ExamState.IN_PROGRESS.value:
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

    items = list(exam.get("items", []))
    if not exam_requires_vlm_grading(items):
        return await _submit_exam_synchronous(
            exam, items, database, current_user, adapter, vlm_client, storage
        )
    return await _submit_exam_asynchronous(
        exam, database, current_user, adapter
    )


async def _submit_exam_synchronous(
    exam: dict[str, Any],
    items: list[dict[str, Any]],
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
    vlm_client: GradingVLMDependency,
    storage: StorageDependency,
) -> ExamResponse:
    grading_time = datetime.now(UTC)
    graded_items: list[dict[str, Any]] = []
    for item in items:
        graded_items.append(
            await grade_item(item, vlm_client=vlm_client, storage=storage, now=grading_time)
        )
    summary = build_exam_summary(graded_items)

    async def _transaction(session: Any) -> dict[str, Any]:
        current_exam = await database["exams"].find_one(
            {"_id": exam["_id"], "userId": current_user["_id"]},
            session=session,
        )
        if current_exam is None:
            raise ApiError(404, "NOT_FOUND", "Exam not found")
        if current_exam.get("state") != ExamState.IN_PROGRESS.value:
            raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

        original_items = exam.get("items", [])
        current_items_by_id = {
            str(item.get("itemId")): item
            for item in current_exam.get("items", [])
        }
        for original_item in original_items:
            item_id = str(original_item.get("itemId"))
            current_item = current_items_by_id.get(item_id)
            if current_item is None:
                raise ApiError(
                    409,
                    "ANSWERS_MODIFIED_DURING_GRADING",
                    "Answers were modified while grading was in progress. Please retry submission.",
                )
            original_answer = dict(original_item.get("answer", {})).get("raw")
            current_answer = dict(current_item.get("answer", {})).get("raw")
            if original_answer != current_answer:
                raise ApiError(
                    409,
                    "ANSWERS_MODIFIED_DURING_GRADING",
                    "Answers were modified while grading was in progress. Please retry submission.",
                )

        submitted_at = datetime.now(UTC)
        next_state = transition_exam_state(ExamState(current_exam["state"]), ExamState.SUBMITTED)
        await database["exams"].update_one(
            {"_id": current_exam["_id"]},
            {
                "$set": {
                    "state": next_state.value,
                    "items": graded_items,
                    "summary": summary,
                    "submittedAt": submitted_at,
                    "updatedAt": submitted_at,
                }
            },
            session=session,
        )

        for item in graded_items:
            grading = dict(item.get("grading", {}))
            if grading.get("status") == GradingStatus.PENDING_REVIEW.value:
                continue
            is_correct = bool(grading.get("isCorrect"))
            problem = await database["problems"].find_one(
                {"_id": item["problemId"], "userId": current_user["_id"]},
                session=session,
            )
            if problem is None:
                continue
            tracking_update = build_tracking_update(
                dict(problem.get("tracking", {})),
                now=submitted_at,
                is_correct=is_correct,
            )
            await database["problems"].update_one(
                {"_id": problem["_id"]},
                {"$set": {"tracking": tracking_update, "updatedAt": submitted_at}},
                session=session,
            )

        updated_exam = deepcopy(current_exam)
        updated_exam.update(
            {
                "state": next_state.value,
                "items": graded_items,
                "summary": summary,
                "submittedAt": submitted_at,
                "updatedAt": submitted_at,
            }
        )
        return updated_exam

    try:
        async with adapter.start_session() as session:
            submitted_exam = await session.with_transaction(_transaction)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(500, "SUBMISSION_FAILED", "Failed to submit exam") from exc

    return ExamResponse(exam=serialize_exam(submitted_exam))


async def _submit_exam_asynchronous(
    exam: dict[str, Any],
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
) -> ExamResponse:
    """Atomically freeze the exam to `grading` and insert its durable grading task."""

    async def _transaction(session: Any) -> dict[str, Any]:
        current_exam = await database["exams"].find_one(
            {"_id": exam["_id"], "userId": current_user["_id"]},
            session=session,
        )
        if current_exam is None:
            raise ApiError(404, "NOT_FOUND", "Exam not found")
        current_state = current_exam.get("state")
        if current_state == ExamState.GRADING.value:
            return current_exam
        if current_state == ExamState.SUBMITTED.value:
            return current_exam
        if current_state != ExamState.IN_PROGRESS.value:
            raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

        grading_at = datetime.now(UTC)
        next_state = transition_exam_state(
            ExamState(current_state), ExamState.GRADING
        )
        await database["exams"].update_one(
            {"_id": current_exam["_id"], "state": ExamState.IN_PROGRESS.value},
            {
                "$set": {
                    "state": next_state.value,
                    "updatedAt": grading_at,
                }
            },
            session=session,
        )
        from app.infrastructure.storage.mongo import EXAM_GRADING_TASKS_COLLECTION

        await database[EXAM_GRADING_TASKS_COLLECTION].insert_one(
            {
                "_id": ObjectId(),
                "examId": current_exam["_id"],
                "userId": current_user["_id"],
                "status": "pending",
                "claimToken": None,
                "leaseUntil": None,
                "error": None,
                "createdAt": grading_at,
                "updatedAt": grading_at,
            },
            session=session,
        )

        updated_exam = deepcopy(current_exam)
        updated_exam.update(
            {"state": next_state.value, "updatedAt": grading_at}
        )
        return updated_exam

    try:
        async with adapter.start_session() as session:
            grading_exam = await session.with_transaction(_transaction)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(500, "SUBMISSION_FAILED", "Failed to submit exam") from exc

    return ExamResponse(exam=serialize_exam(grading_exam))


@router.post("/{exam_id}/discard", response_model=ExamResponse)
async def discard_exam(
    exam_id: str,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> ExamResponse:
    exam = await get_owned_exam(database, exam_id, current_user["_id"])
    state = exam.get("state")
    if state not in (ExamState.IN_PROGRESS.value, ExamState.GRADING.value):
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not in progress")

    discarded_at = datetime.now(UTC)
    next_state = transition_exam_state(ExamState(exam["state"]), ExamState.DISCARDED)
    await database["exams"].update_one(
        {"_id": exam["_id"]},
        {
            "$set": {
                "state": next_state.value,
                "discardedAt": discarded_at,
                "updatedAt": discarded_at,
            }
        },
    )
    updated_exam = deepcopy(exam)
    updated_exam.update(
        {
            "state": next_state.value,
            "discardedAt": discarded_at,
            "updatedAt": discarded_at,
        }
    )
    return ExamResponse(exam=serialize_exam(updated_exam))


@router.post("/{exam_id}/items/{item_id}/self-report", response_model=SelfReportResponse)
async def self_report_exam_item(
    exam_id: str,
    item_id: str,
    payload: SelfReportRequest,
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    adapter: AdapterDependency,
) -> SelfReportResponse:
    exam = await get_owned_exam(database, exam_id, current_user["_id"])
    if exam.get("state") != ExamState.SUBMITTED.value:
        raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not submitted")

    item_index, item = find_item(exam, item_id)
    grading = dict(item.get("grading", {}))
    if grading.get("status") != GradingStatus.PENDING_REVIEW.value:
        raise ApiError(409, "ITEM_NOT_PENDING_REVIEW", "Exam item is not pending review")

    resolved_at = datetime.now(UTC)
    grading.update(
        {
            "status": GradingStatus.CORRECT.value if payload.isCorrect else GradingStatus.INCORRECT.value,
            "method": "self-report",
            "isCorrect": payload.isCorrect,
            "score": 1.0 if payload.isCorrect else 0.0,
            "gradedAt": resolved_at,
            "selfReportedCorrect": payload.isCorrect,
        }
    )
    updated_item = deepcopy(item)
    updated_item["grading"] = grading
    items = deepcopy(list(exam.get("items", [])))
    items[item_index] = updated_item
    summary = build_exam_summary(items)

    async def _transaction(session: Any) -> dict[str, Any]:
        current_exam = await database["exams"].find_one(
            {"_id": exam["_id"], "userId": current_user["_id"]},
            session=session,
        )
        if current_exam is None:
            raise ApiError(404, "NOT_FOUND", "Exam not found")
        if current_exam.get("state") != ExamState.SUBMITTED.value:
            raise ApiError(409, "INVALID_EXAM_STATE", "Exam is not submitted")

        await database["exams"].update_one(
            {"_id": current_exam["_id"]},
            {"$set": {"items": items, "summary": summary, "updatedAt": resolved_at}},
            session=session,
        )

        problem = await database["problems"].find_one(
            {"_id": updated_item["problemId"], "userId": current_user["_id"]},
            session=session,
        )
        if problem is not None:
            tracking_update = build_tracking_update(
                dict(problem.get("tracking", {})),
                now=resolved_at,
                is_correct=payload.isCorrect,
            )
            await database["problems"].update_one(
                {"_id": problem["_id"]},
                {"$set": {"tracking": tracking_update, "updatedAt": resolved_at}},
                session=session,
            )

        updated_exam = deepcopy(current_exam)
        updated_exam.update({"items": items, "summary": summary, "updatedAt": resolved_at})
        return updated_exam

    async with adapter.start_session() as session:
        updated_exam = await session.with_transaction(_transaction)
    return SelfReportResponse(
        item=serialize_exam_item(updated_item, include_correct_answer=True),
        summary=serialize_exam_summary(dict(updated_exam["summary"])),
    )


@router.get("", response_model=ExamHistoryResponse)
async def list_exam_history(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    include_discarded: bool = Query(default=False, alias="includeDiscarded"),
) -> ExamHistoryResponse:
    states = [ExamState.SUBMITTED.value, ExamState.GRADING.value]
    if include_discarded:
        states.append(ExamState.DISCARDED.value)
    query = {
        "userId": current_user["_id"],
        "state": {"$in": states},
    }
    total = await database["exams"].count_documents(query)
    cursor = database["exams"].find(query).sort("updatedAt", -1)
    cursor = cursor.skip((page - 1) * page_size).limit(page_size)
    documents = await cursor.to_list(length=page_size)
    return ExamHistoryResponse(
        items=[
            ExamHistoryItemPayload(
                id=str(document["_id"]),
                state=ExamState(document["state"]),
                createdAt=document["createdAt"],
                submittedAt=document.get("submittedAt"),
                discardedAt=document.get("discardedAt"),
                summary=serialize_exam_summary(dict(document.get("summary", {}))),
            )
            for document in documents
        ],
        page=page,
        pageSize=page_size,
        total=total,
    )
