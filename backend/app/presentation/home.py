from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.domain.models import ExamState, GradingStatus
from app.presentation.deps import DatabaseDependency, get_current_user

router = APIRouter(prefix="/home", tags=["home"])

CurrentUserDependency = Annotated[dict[str, Any], Depends(get_current_user)]

_MASTERY_STATUSES = {GradingStatus.CORRECT.value, GradingStatus.INCORRECT.value}


class HomeCoverage(BaseModel):
    totalProblems: int
    triedProblems: int
    percentage: int


class HomeConquest(BaseModel):
    totalProblems: int
    masteredProblems: int
    percentage: int


class HomeActivityDay(BaseModel):
    date: str
    count: int


class HomeActivity(BaseModel):
    startDate: str
    endDate: str
    days: list[HomeActivityDay]


class HomeSummaryResponse(BaseModel):
    coverage: HomeCoverage
    conquest: HomeConquest
    activity: HomeActivity


@router.get("/summary", response_model=HomeSummaryResponse)
async def get_home_summary(
    database: DatabaseDependency,
    current_user: CurrentUserDependency,
) -> HomeSummaryResponse:
    user_id = current_user["_id"]

    problem_documents = await database["problems"].find(
        {"userId": user_id, "isDeleted": False}
    ).to_list(length=None)
    non_deleted_problem_ids = {doc["_id"] for doc in problem_documents}
    total_problems = len(non_deleted_problem_ids)

    tried_problem_ids: set[Any] = set()
    latest_mastery: dict[Any, tuple[datetime, bool]] = {}

    def _consider_mastery(problem_id: Any, event_time: Any, status: str) -> None:
        if status not in _MASTERY_STATUSES:
            return
        if not isinstance(event_time, datetime):
            return
        is_correct = status == GradingStatus.CORRECT.value
        previous = latest_mastery.get(problem_id)
        if previous is None or event_time > previous[0]:
            latest_mastery[problem_id] = (event_time, is_correct)

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

    tried_problems = len(tried_problem_ids)
    percentage = round((tried_problems / total_problems) * 100) if total_problems else 0

    mastered_problems = sum(1 for _, is_correct in latest_mastery.values() if is_correct)
    conquest_percentage = (
        round((mastered_problems / total_problems) * 100) if total_problems else 0
    )

    today = datetime.now(UTC).date()
    start_date = today - timedelta(days=364)

    daily_counts: dict[str, int] = {}
    current = start_date
    while current <= today:
        daily_counts[current.strftime("%Y-%m-%d")] = 0
        current += timedelta(days=1)

    def _date_key(value: Any) -> str | None:
        if not isinstance(value, datetime):
            return None
        event_date = value.astimezone(UTC).date()
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
        activity=HomeActivity(
            startDate=start_date.strftime("%Y-%m-%d"),
            endDate=today.strftime("%Y-%m-%d"),
            days=days,
        ),
    )
