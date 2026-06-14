import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import List, Optional

from .models import Problem
from .selection import ensure_utc


@dataclass
class PracticeSelectionConfig:
    cooldown_days: int = 7
    last_wrong_weight: float = 1.0
    failure_rate_weight: float = 1.0
    recency_weight: float = 1.0
    min_problem_age_days: int = 3


@dataclass
class PracticeSelectionResult:
    selected_problem: Optional[Problem]
    status: str  # "ok", "no_eligible", "no_problems"


def get_eligible_practice_problems(
    problems: List[Problem],
    config: PracticeSelectionConfig,
    now: datetime,
) -> list[Problem]:
    eligible = []
    for p in problems:
        if p.isDeleted:
            continue
        if not p.correctAnswer or not p.correctAnswer.normalizedText:
            continue
        if config.min_problem_age_days > 0:
            created_at = ensure_utc(p.createdAt)
            age_cutoff = now - timedelta(days=config.min_problem_age_days)
            if created_at > age_cutoff:
                continue
        if p.tracking.lastTestedAt:
            last_tested = ensure_utc(p.tracking.lastTestedAt)
            cutoff = now - timedelta(days=config.cooldown_days)
            if last_tested > cutoff:
                continue
        eligible.append(p)
    return eligible


def _has_practiceable_answer(problems: List[Problem]) -> list[Problem]:
    return [p for p in problems if not p.isDeleted and p.correctAnswer and p.correctAnswer.normalizedText]


def select_practice_problem(
    problems: List[Problem],
    config: PracticeSelectionConfig,
    now: datetime,
    rng: random.Random | None = None
) -> PracticeSelectionResult:
    if not problems:
        return PracticeSelectionResult(None, "no_problems")

    eligible = get_eligible_practice_problems(problems, config, now)

    if not eligible:
        if _has_practiceable_answer(problems):
            return PracticeSelectionResult(None, "no_eligible")
        return PracticeSelectionResult(None, "no_problems")

    weighted = []
    for problem in eligible:
        weight = _compute_problem_weight(problem, config, now)
        weighted.append((problem, weight))

    total = sum(w for _, w in weighted)
    if total <= 0:
        selected = (rng or random).choice(eligible)
    else:
        r = (rng or random).random() * total
        cumulative = 0.0
        selected = eligible[0]
        for problem, weight in weighted:
            cumulative += weight
            if r <= cumulative:
                selected = problem
                break

    return PracticeSelectionResult(selected, "ok")


def _compute_problem_weight(problem: Problem, config: PracticeSelectionConfig, now: datetime) -> float:
    last_wrong_score = 1.0
    if problem.tracking.lastAttemptCorrect is False:
        last_wrong_score = 2.0

    failure_score = 1.0
    attempt_count = problem.tracking.correctCount + problem.tracking.failedCount
    if attempt_count > 0:
        failure_rate = problem.tracking.failedCount / attempt_count
        failure_score = 1.0 + failure_rate

    recency_score = 1.0
    if problem.tracking.lastTestedAt:
        days_since = (now - ensure_utc(problem.tracking.lastTestedAt)).days
        recency_score = 1.0 + days_since / 30.0

    return (
        last_wrong_score * config.last_wrong_weight +
        failure_score * config.failure_rate_weight +
        recency_score * config.recency_weight
    )
