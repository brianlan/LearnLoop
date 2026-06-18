import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import List, Optional

from .models import Problem
from .selection import (
    ProblemSelectionConfig,
    ScoreBreakdown,
    compute_score_breakdown,
    ensure_utc,
    get_eligible_problems,
    select_problems as _select_problems,
)


@dataclass
class PracticeSelectionConfig:
    cooldown_days: int = 7
    last_wrong_weight: float = 1.0
    failure_rate_weight: float = 1.0
    recency_weight: float = 1.0
    min_problem_age_days: int = 3

    def to_shared_config(self) -> ProblemSelectionConfig:
        return ProblemSelectionConfig(
            cooldown_days=self.cooldown_days,
            last_wrong_weight=self.last_wrong_weight,
            failure_rate_weight=self.failure_rate_weight,
            recency_weight=self.recency_weight,
            min_problem_age_days=self.min_problem_age_days,
        )


@dataclass
class PracticeSelectionResult:
    selected_problem: Optional[Problem]
    status: str  # "ok", "no_eligible", "no_problems"


@dataclass
class PracticeWeightBreakdown:
    lastWrong: float
    failure: float
    recency: float
    total: float


def get_eligible_practice_problems(
    problems: List[Problem],
    config: PracticeSelectionConfig,
    now: datetime,
) -> list[Problem]:
    return get_eligible_problems(problems, config.to_shared_config(), now)


def compute_problem_weight_breakdown(
    problem: Problem, config: PracticeSelectionConfig, now: datetime
) -> PracticeWeightBreakdown:
    shared = compute_score_breakdown(problem, config.to_shared_config(), now)
    return PracticeWeightBreakdown(
        lastWrong=shared.last_wrong,
        failure=shared.failure,
        recency=shared.recency,
        total=shared.total,
    )


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

    selected = _select_problems(problems, 1, config.to_shared_config(), now, rng=rng)

    if not selected:
        return PracticeSelectionResult(None, "no_eligible")

    return PracticeSelectionResult(selected[0], "ok")


def _compute_problem_weight(problem: Problem, config: PracticeSelectionConfig, now: datetime) -> float:
    return compute_problem_weight_breakdown(problem, config, now).total
