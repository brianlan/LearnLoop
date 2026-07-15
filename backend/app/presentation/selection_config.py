from app.domain.practice_selection import PracticeSelectionConfig
from app.domain.selection import ProblemSelectionConfig

__all__ = ["problem_selection_config", "practice_selection_config"]


def problem_selection_config(
    *,
    cooldown_days: int = 7,
    last_wrong_weight: float = 1.0,
    failure_rate_weight: float = 1.0,
    recency_weight: float = 1.0,
    min_problem_age_days: int = 3,
) -> ProblemSelectionConfig:
    """Build a presentation-agnostic ``ProblemSelectionConfig`` from primitives.

    Default values mirror the domain defaults and must not change without a
    corresponding settings/API review.
    """
    return ProblemSelectionConfig(
        cooldown_days=cooldown_days,
        last_wrong_weight=last_wrong_weight,
        failure_rate_weight=failure_rate_weight,
        recency_weight=recency_weight,
        min_problem_age_days=min_problem_age_days,
    )


def practice_selection_config(
    *,
    cooldown_days: int = 7,
    last_wrong_weight: float | None = None,
    failure_rate_weight: float | None = None,
    recency_weight: float | None = None,
    min_problem_age_days: int | None = None,
) -> PracticeSelectionConfig:
    """Build a ``PracticeSelectionConfig`` from primitives.

    Omitted weight values retain the domain defaults. This keeps the existing
    partial/default construction path used by ``/practice/stats`` intact: that
    endpoint intentionally supplies only ``cooldown_days`` and
    ``min_problem_age_days``.
    """
    return PracticeSelectionConfig(
        cooldown_days=cooldown_days,
        last_wrong_weight=last_wrong_weight if last_wrong_weight is not None else 1.0,
        failure_rate_weight=failure_rate_weight if failure_rate_weight is not None else 1.0,
        recency_weight=recency_weight if recency_weight is not None else 1.0,
        min_problem_age_days=min_problem_age_days if min_problem_age_days is not None else 3,
    )
