from datetime import UTC, datetime, timedelta, timezone
from typing import List
import random
from .models import Problem, SelectionPolicyConfig


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def select_problems(
    problems: List[Problem],
    count: int,
    config: SelectionPolicyConfig,
    rng: random.Random | None = None
) -> List[Problem]:
    now = datetime.now(timezone.utc)
    eligible = []
    for p in problems:
        if p.isDeleted:
            continue
        if config.minProblemAgeDays > 0:
            created_at = ensure_utc(p.createdAt)
            age_cutoff = now - timedelta(days=config.minProblemAgeDays)
            if created_at > age_cutoff:
                continue
        eligible.append(p)

    if not eligible:
        return []

    weighted = []

    for problem in eligible:
        recency_score = 1.0
        if problem.tracking.lastTestedAt:
            days_since = (now - ensure_utc(problem.tracking.lastTestedAt)).days
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

    sample_size = min(count, len(top_candidates))
    if rng is None:
        selected = random.sample(top_candidates, sample_size)
    else:
        selected = rng.sample(top_candidates, sample_size)

    return selected
