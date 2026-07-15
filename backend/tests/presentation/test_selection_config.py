from app.domain.practice_selection import PracticeSelectionConfig
from app.domain.selection import ProblemSelectionConfig
from app.presentation.selection_config import (
    practice_selection_config,
    problem_selection_config,
)


def test_problem_selection_config_defaults_match_domain() -> None:
    mapped = problem_selection_config()
    domain_default = ProblemSelectionConfig()

    assert mapped == domain_default
    assert isinstance(mapped, ProblemSelectionConfig)


def test_problem_selection_config_all_primitives() -> None:
    mapped = problem_selection_config(
        cooldown_days=5,
        last_wrong_weight=1.5,
        failure_rate_weight=2.0,
        recency_weight=2.5,
        min_problem_age_days=0,
    )

    assert mapped.cooldown_days == 5
    assert mapped.last_wrong_weight == 1.5
    assert mapped.failure_rate_weight == 2.0
    assert mapped.recency_weight == 2.5
    assert mapped.min_problem_age_days == 0


def test_practice_selection_config_defaults_match_domain() -> None:
    mapped = practice_selection_config()
    domain_default = PracticeSelectionConfig()

    assert mapped == domain_default
    assert isinstance(mapped, PracticeSelectionConfig)


def test_practice_selection_config_all_primitives() -> None:
    mapped = practice_selection_config(
        cooldown_days=5,
        last_wrong_weight=1.5,
        failure_rate_weight=2.0,
        recency_weight=2.5,
        min_problem_age_days=0,
    )

    assert mapped.cooldown_days == 5
    assert mapped.last_wrong_weight == 1.5
    assert mapped.failure_rate_weight == 2.0
    assert mapped.recency_weight == 2.5
    assert mapped.min_problem_age_days == 0


def test_practice_selection_config_partial_preserves_defaults() -> None:
    """Omitted weights fall back to domain defaults; only cooldown/min_age are supplied."""
    mapped = practice_selection_config(
        cooldown_days=14,
        min_problem_age_days=1,
    )

    assert mapped.cooldown_days == 14
    assert mapped.min_problem_age_days == 1
    assert mapped.last_wrong_weight == 1.0
    assert mapped.failure_rate_weight == 1.0
    assert mapped.recency_weight == 1.0
    assert mapped == PracticeSelectionConfig(
        cooldown_days=14,
        min_problem_age_days=1,
    )


def test_problem_selection_config_and_practice_selection_config_to_shared_values() -> None:
    """For identical primitive inputs the shared selection math receives identical values."""
    problem_config = problem_selection_config(
        cooldown_days=7,
        last_wrong_weight=1.5,
        failure_rate_weight=2.0,
        recency_weight=2.5,
        min_problem_age_days=3,
    )
    practice_config = practice_selection_config(
        cooldown_days=7,
        last_wrong_weight=1.5,
        failure_rate_weight=2.0,
        recency_weight=2.5,
        min_problem_age_days=3,
    )

    assert problem_config == practice_config.to_shared_config()
