from datetime import datetime, timezone
from typing import List
import random
from .models import Problem, SelectionPolicyConfig


def select_problems(
    problems: List[Problem],
    count: int,
    config: SelectionPolicyConfig,
    rng: random.Random | None = None
) -> List[Problem]:
    eligible = [
        p for p in problems
        if not p.isDeleted
    ]

    if not eligible:
        return []

    now = datetime.now(timezone.utc)
    weighted = []

    for problem in eligible:
        recency_score = 1.0
        if problem.tracking.lastTestedAt:
            days_since = (now - problem.tracking.lastTestedAt).days
            recency_score = min(1.0 + days_since / 30.0, 3.0)

        failure_score = 1.0
        if problem.tracking.exposureCount > 0:
            failure_rate = problem.tracking.failedCount / problem.tracking.exposureCount
            failure_score = 1.0 + failure_rate * 2.0

        total_weight = (
            recency_score * config.recencyWeight +
            failure_score * config.failureWeight
        )

        weighted.append((problem, total_weight))

    weighted.sort(key=lambda x: x[1], reverse=True)
    top_candidates = [p for p, _ in weighted[:count * 2]]

    if rng is None:
        rng = random

    selected = rng.sample(top_candidates, min(count, len(top_candidates)))

    return selected
