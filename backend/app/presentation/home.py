from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.domain.models import ExamState, GradingStatus
from app.domain.selection import compute_score_breakdown, ensure_utc
from app.presentation.deps import CurrentUserDependency, DatabaseDependency, SettingsDependency
from app.presentation.problem_serialization import problem_document_to_model
from app.presentation.selection_config import problem_selection_config

router = APIRouter(prefix="/home", tags=["home"])

_MASTERY_STATUSES = {GradingStatus.CORRECT.value, GradingStatus.INCORRECT.value}


class HomeCoverage(BaseModel):
    totalProblems: int
    triedProblems: int
    percentage: int


class HomeConquest(BaseModel):
    totalProblems: int
    masteredProblems: int
    percentage: int


class HomeFirstPass(BaseModel):
    attemptedProblems: int
    firstPassCorrectProblems: int
    percentage: int


class HomeActivityDay(BaseModel):
    date: str
    count: int


class HomeActivity(BaseModel):
    startDate: str
    endDate: str
    days: list[HomeActivityDay]


class ScoreDistributionBucket(BaseModel):
    start: int
    neverTested: int
    minAged: int
    tested: int
    cooldown: int


class ScoreDistribution(BaseModel):
    buckets: list[ScoreDistributionBucket]


class HomeSummaryResponse(BaseModel):
    coverage: HomeCoverage
    conquest: HomeConquest
    firstPass: HomeFirstPass
    activity: HomeActivity
    scoreDistribution: ScoreDistribution


def _selection_config_from_settings(settings: Any):
    return problem_selection_config(
        cooldown_days=settings.problem_selection_cooldown_days,
        last_wrong_weight=settings.problem_selection_last_wrong_weight,
        failure_rate_weight=settings.problem_selection_failure_rate_weight,
        recency_weight=settings.problem_selection_recency_weight,
        min_problem_age_days=settings.problem_selection_min_age_days,
    )


def _build_score_distribution(
    problem_documents: list[dict[str, Any]],
    config: ProblemSelectionConfig,
    now: datetime,
) -> ScoreDistribution:
    """Bucket problems by their raw pre-clamp selection score.

    Reuses ``compute_score_breakdown`` for the component math and sums the
    three components before the ``max(0, ...)`` floor so negative buckets are
    meaningful. Problems that fail model validation are skipped so a malformed
    document does not break the home summary.
    """
    # Category indexes: 0=neverTested, 1=minAged, 2=tested, 3=cooldown.
    cooldown_cutoff = now - timedelta(days=config.cooldown_days)
    age_cutoff = now - timedelta(days=config.min_problem_age_days)
    counts: dict[int, list[int]] = {}
    for doc in problem_documents:
        try:
            problem = problem_document_to_model(doc)
        except Exception:
            continue
        breakdown = compute_score_breakdown(problem, config, now)
        raw = breakdown.recency + breakdown.failure + breakdown.last_wrong
        bucket = math.floor(raw)
        if bucket not in counts:
            counts[bucket] = [0, 0, 0, 0]
        last_tested = problem.tracking.lastTestedAt
        if last_tested is not None:
            # Parent-status split: tested problems never classify as Min aged.
            idx = 3 if ensure_utc(last_tested) > cooldown_cutoff else 2
        elif ensure_utc(problem.createdAt) > age_cutoff:
            idx = 1
        else:
            idx = 0
        counts[bucket][idx] += 1

    if not counts:
        return ScoreDistribution(buckets=[])

    lowest = min(counts)
    highest = max(counts)
    buckets = [
        ScoreDistributionBucket(
            start=start,
            neverTested=counts.get(start, [0, 0, 0, 0])[0],
            minAged=counts.get(start, [0, 0, 0, 0])[1],
            tested=counts.get(start, [0, 0, 0, 0])[2],
            cooldown=counts.get(start, [0, 0, 0, 0])[3],
        )
        for start in range(lowest, highest + 1)
    ]
    return ScoreDistribution(buckets=buckets)


@router.get("/summary", response_model=HomeSummaryResponse)
async def get_home_summary(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
    settings: SettingsDependency,
    timezone: Annotated[str | None, Query()] = None,
) -> HomeSummaryResponse:
    if timezone is not None:
        try:
            tz = ZoneInfo(timezone)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid timezone")
    else:
        tz = UTC

    user_id = current_user["_id"]

    problem_documents = await database["problems"].find(
        {"userId": user_id, "isDeleted": False}
    ).to_list(length=None)
    non_deleted_problem_ids = {doc["_id"] for doc in problem_documents}
    total_problems = len(non_deleted_problem_ids)

    tried_problem_ids: set[Any] = set()
    latest_mastery: dict[Any, tuple[datetime, bool]] = {}
    earliest_attempt: dict[Any, tuple[datetime, bool]] = {}

    def _consider_mastery(problem_id: Any, event_time: Any, status: str) -> None:
        if status not in _MASTERY_STATUSES:
            return
        if not isinstance(event_time, datetime):
            return
        is_correct = status == GradingStatus.CORRECT.value
        previous = latest_mastery.get(problem_id)
        if previous is None or event_time > previous[0]:
            latest_mastery[problem_id] = (event_time, is_correct)

    def _consider_first_pass(problem_id: Any, event_time: Any, status: str) -> None:
        if not isinstance(event_time, datetime):
            return
        is_correct = status == GradingStatus.CORRECT.value
        previous = earliest_attempt.get(problem_id)
        if previous is None or event_time < previous[0]:
            earliest_attempt[problem_id] = (event_time, is_correct)
        elif event_time == previous[0]:
            earliest_attempt[problem_id] = (event_time, previous[1] or is_correct)

    practice_attempts = await database["practice_attempts"].find(
        {"userId": user_id}
    ).to_list(length=None)
    for attempt in practice_attempts:
        problem_id = attempt.get("problemId")
        if problem_id in non_deleted_problem_ids:
            tried_problem_ids.add(problem_id)
            _consider_mastery(
                problem_id,
                attempt.get("createdAt"),
                str(attempt.get("gradingStatus", "")),
            )
            _consider_first_pass(
                problem_id,
                attempt.get("createdAt"),
                str(attempt.get("gradingStatus", "")),
            )

    submitted_exams = await database["exams"].find(
        {"userId": user_id, "state": ExamState.SUBMITTED.value}
    ).to_list(length=None)
    for exam in submitted_exams:
        submitted_at = exam.get("submittedAt")
        for item in exam.get("items", []):
            problem_id = item.get("problemId")
            if problem_id not in non_deleted_problem_ids:
                continue
            tried_problem_ids.add(problem_id)
            grading = item.get("grading") or {}
            _consider_mastery(
                problem_id,
                submitted_at,
                str(grading.get("status", "")),
            )
            _consider_first_pass(
                problem_id,
                submitted_at,
                str(grading.get("status", "")),
            )

    tried_problems = len(tried_problem_ids)
    percentage = round((tried_problems / total_problems) * 100) if total_problems else 0

    mastered_problems = sum(1 for _, is_correct in latest_mastery.values() if is_correct)
    conquest_percentage = (
        round((mastered_problems / total_problems) * 100) if total_problems else 0
    )

    first_pass_correct_problems = sum(
        1 for _, is_correct in earliest_attempt.values() if is_correct
    )
    first_pass_percentage = (
        round((first_pass_correct_problems / tried_problems) * 100) if tried_problems else 0
    )

    today = datetime.now(tz).date()
    start_date = today - timedelta(days=364)

    daily_counts: dict[str, int] = {}
    current = start_date
    while current <= today:
        daily_counts[current.strftime("%Y-%m-%d")] = 0
        current += timedelta(days=1)

    def _date_key(value: Any) -> str | None:
        if not isinstance(value, datetime):
            return None
        event_date = value.astimezone(tz).date()
        if start_date <= event_date <= today:
            return event_date.strftime("%Y-%m-%d")
        return None

    for attempt in practice_attempts:
        date_str = _date_key(attempt.get("createdAt"))
        if date_str is not None:
            daily_counts[date_str] += 1

    for exam in submitted_exams:
        date_str = _date_key(exam.get("submittedAt"))
        if date_str is not None:
            daily_counts[date_str] += len(exam.get("items", []))

    days = [
        HomeActivityDay(date=date_str, count=count)
        for date_str, count in daily_counts.items()
    ]

    selection_config = _selection_config_from_settings(settings)
    score_distribution = _build_score_distribution(
        problem_documents, selection_config, datetime.now(UTC)
    )

    return HomeSummaryResponse(
        coverage=HomeCoverage(
            totalProblems=total_problems,
            triedProblems=tried_problems,
            percentage=percentage,
        ),
        conquest=HomeConquest(
            totalProblems=total_problems,
            masteredProblems=mastered_problems,
            percentage=conquest_percentage,
        ),
        firstPass=HomeFirstPass(
            attemptedProblems=tried_problems,
            firstPassCorrectProblems=first_pass_correct_problems,
            percentage=first_pass_percentage,
        ),
        activity=HomeActivity(
            startDate=start_date.strftime("%Y-%m-%d"),
            endDate=today.strftime("%Y-%m-%d"),
            days=days,
        ),
        scoreDistribution=score_distribution,
    )
