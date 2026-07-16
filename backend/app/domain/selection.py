from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import List, Optional
import math
import random

from .models import Problem


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass
class ProblemSelectionConfig:
    cooldown_days: int = 7
    last_wrong_weight: float = 1.0
    failure_rate_weight: float = 1.0
    recency_weight: float = 1.0
    min_problem_age_days: int = 3


@dataclass
class ScoreBreakdown:
    recency: float
    failure: float
    last_wrong: float
    total: float


def get_eligible_problems(
    problems: List[Problem],
    config: ProblemSelectionConfig,
    now: datetime,
) -> list[Problem]:
    eligible = []
    for p in problems:
        if p.isDeleted:
            continue
        if p.isDisabled:
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


def compute_score_breakdown(
    problem: Problem,
    config: ProblemSelectionConfig,
    now: datetime,
) -> ScoreBreakdown:
    # Recency score with status-specific daily growth rate.
    # Never tested grows fastest (1/10), last-correct slowest (1/60),
    # last-failed and tested-unknown retain the original pace (1/30).
    if problem.tracking.lastTestedAt is None:
        reference_dt = problem.createdAt
        daily_rate = 1 / 10
    else:
        reference_dt = problem.tracking.lastTestedAt
        daily_rate = 1 / 60 if problem.tracking.lastAttemptCorrect is True else 1 / 30

    if reference_dt is not None:
        days_since = (now - ensure_utc(reference_dt)).days
        recency_score = 1.0 + days_since * daily_rate
    else:
        recency_score = 1.0

    # Failure score
    failed_count = problem.tracking.failedCount
    correct_count = problem.tracking.correctCount
    if failed_count > correct_count and correct_count > 0:
        failure_score = failed_count / correct_count
    else:
        diff = failed_count - correct_count
        if diff == 0:
            failure_score = 0.0
        else:
            failure_score = math.copysign(math.sqrt(abs(diff)), diff)

    # Last wrong score
    if problem.tracking.lastTestedAt is None:
        last_wrong_score = 1.0
    elif problem.tracking.lastAttemptCorrect is True:
        last_wrong_score = 0.5
    elif problem.tracking.lastAttemptCorrect is False:
        last_wrong_score = 2.0
    else:
        last_wrong_score = 1.0

    recency = recency_score * config.recency_weight
    failure = failure_score * config.failure_rate_weight
    last_wrong = last_wrong_score * config.last_wrong_weight

    total = max(0.0, recency + failure + last_wrong)

    return ScoreBreakdown(
        recency=recency,
        failure=failure,
        last_wrong=last_wrong,
        total=total,
    )


def _weighted_sample_without_replacement(
    candidates: list[tuple[Problem, float]],
    count: int,
    rng: random.Random,
) -> list[Problem]:
    """Select up to `count` unique problems using weighted random sampling.

    Candidates are expected to have positive weights; zero or negative weights
    are filtered out by ``select_problems`` before this function is called.
    """
    selected: list[Problem] = []
    remaining = list(candidates)

    while len(selected) < count and remaining:
        total_weight = sum(weight for _, weight in remaining)
        if total_weight <= 0:
            break

        r = rng.random() * total_weight
        cumulative = 0.0
        chosen_idx = 0
        for idx, (_, weight) in enumerate(remaining):
            cumulative += weight
            if r <= cumulative:
                chosen_idx = idx
                break

        problem, _ = remaining.pop(chosen_idx)
        selected.append(problem)

    return selected


def select_problems(
    problems: List[Problem],
    count: int,
    config: ProblemSelectionConfig,
    now: datetime,
    rng: random.Random | None = None,
) -> list[Problem]:
    eligible = get_eligible_problems(problems, config, now)

    if not eligible:
        return []

    weighted = []
    for problem in eligible:
        breakdown = compute_score_breakdown(problem, config, now)
        if breakdown.total > 0:
            weighted.append((problem, breakdown.total))

    if not weighted:
        return []

    if rng is None:
        rng = random.Random()

    return _weighted_sample_without_replacement(weighted, count, rng)
