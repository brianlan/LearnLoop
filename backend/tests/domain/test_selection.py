from datetime import datetime, timedelta, timezone

import pytest

from app.domain import (
    Problem,
    CorrectAnswer,
    ProblemType,
)
from app.domain.selection import (
    ProblemSelectionConfig,
    ScoreBreakdown,
    compute_score_breakdown,
    ensure_utc,
    filter_and_score_problems,
    get_eligible_problems,
    rank_eligible_problems,
    select_problems,
    sort_scored_problems,
)


def create_test_problem(
    problem_id: str,
    is_deleted: bool = False,
    is_disabled: bool = False,
    last_tested_at: datetime | None = None,
    failed_count: int = 0,
    exposure_count: int = 1,
    correct_count: int | None = None,
    created_at: datetime | None = None,
    last_attempt_correct: bool | None = None,
) -> Problem:
    from app.domain import Tracking
    cc = correct_count if correct_count is not None else max(0, exposure_count - failed_count)
    tracking = Tracking(
        exposureCount=exposure_count,
        correctCount=cc,
        failedCount=failed_count,
    )
    if last_tested_at is not None:
        tracking.lastTestedAt = last_tested_at
    if last_attempt_correct is not None:
        tracking.lastAttemptCorrect = last_attempt_correct

    return Problem(
        id=problem_id,
        userId="u1",
        text="test",
        problemType=ProblemType.SINGLE_CHOICE,
        correctAnswer=CorrectAnswer(display="a", normalizedText="a", normalizedSet=[], format="single"),
        isDeleted=is_deleted,
        isDisabled=is_disabled,
        tracking=tracking,
        createdAt=created_at or datetime.now(timezone.utc) - timedelta(days=30),
    )


def test_exclude_deleted_problems():
    problems = [
        create_test_problem("1", is_deleted=False),
        create_test_problem("2", is_deleted=True),
    ]
    config = ProblemSelectionConfig(recency_weight=1.0, failure_rate_weight=1.0)
    now = datetime.now(timezone.utc)
    selected = select_problems(problems, 2, config, now=now)
    assert len(selected) == 1
    assert selected[0].id == "1"


def test_exclude_disabled_problems():
    problems = [
        create_test_problem("1", is_disabled=False),
        create_test_problem("2", is_disabled=True),
    ]
    config = ProblemSelectionConfig(recency_weight=1.0, failure_rate_weight=1.0)
    now = datetime.now(timezone.utc)
    eligible = get_eligible_problems(problems, config, now)
    assert [p.id for p in eligible] == ["1"]
    selected = select_problems(problems, 2, config, now=now)
    assert len(selected) == 1
    assert selected[0].id == "1"


def test_selects_weighted_without_replacement():
    now = datetime.now(timezone.utc)
    problems = [
        create_test_problem("1", last_tested_at=now - timedelta(days=10), failed_count=0),
        create_test_problem("2", last_tested_at=now - timedelta(days=30), failed_count=0),
        create_test_problem("3", last_tested_at=now - timedelta(days=10), failed_count=2, exposure_count=2),
    ]
    config = ProblemSelectionConfig(recency_weight=1.0, failure_rate_weight=1.0, cooldown_days=0)
    import random
    rng = random.Random(42)
    selected = select_problems(problems, 2, config, now=now, rng=rng)
    assert len(selected) == 2
    assert len({p.id for p in selected}) == 2


def test_min_age_excludes_too_new():
    now = datetime.now(timezone.utc)
    too_new = now - timedelta(days=1)
    old_enough = now - timedelta(days=5)
    problems = [
        create_test_problem("new", created_at=too_new),
        create_test_problem("old", created_at=old_enough),
    ]
    config = ProblemSelectionConfig(recency_weight=1.0, failure_rate_weight=1.0, min_problem_age_days=3)
    selected = select_problems(problems, 2, config, now=now)

    assert len(selected) == 1
    assert selected[0].id == "old"


def test_min_age_boundary_at_threshold():
    now = datetime.now(timezone.utc)
    exactly_at = now - timedelta(days=3)
    problems = [create_test_problem("boundary", created_at=exactly_at)]
    config = ProblemSelectionConfig(recency_weight=1.0, failure_rate_weight=1.0, min_problem_age_days=3)
    selected = select_problems(problems, 1, config, now=now)

    assert len(selected) == 1
    assert selected[0].id == "boundary"


def test_min_age_zero_allows_immediate():
    now = datetime.now(timezone.utc)
    just_created = now - timedelta(seconds=10)
    problems = [create_test_problem("fresh", created_at=just_created)]
    config = ProblemSelectionConfig(recency_weight=1.0, failure_rate_weight=1.0, min_problem_age_days=0)
    selected = select_problems(problems, 1, config, now=now)

    assert len(selected) == 1
    assert selected[0].id == "fresh"


def test_min_age_all_too_new_returns_empty():
    now = datetime.now(timezone.utc)
    too_new = now - timedelta(days=1)
    problems = [
        create_test_problem("a", created_at=too_new),
        create_test_problem("b", created_at=too_new),
    ]
    config = ProblemSelectionConfig(recency_weight=1.0, failure_rate_weight=1.0, min_problem_age_days=3)
    selected = select_problems(problems, 2, config, now=now)

    assert selected == []


def test_min_age_default_value():
    config = ProblemSelectionConfig(recency_weight=1.0, failure_rate_weight=1.0)
    assert config.min_problem_age_days == 3


def test_recency_uses_last_tested_when_present():
    now = datetime.now(timezone.utc)
    last_tested = now - timedelta(days=60)
    problem = create_test_problem("p1", last_tested_at=last_tested)
    config = ProblemSelectionConfig(recency_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    # Tested with unknown last result: base 0.0, rate 1/30, days_since = 60 -> 0.0 + 60*(1/30) = 2.0
    assert breakdown.recency == 2.0


def test_recency_uses_created_at_when_not_tested():
    now = datetime.now(timezone.utc)
    created_at = now - timedelta(days=45)
    problem = create_test_problem("p1", last_tested_at=None, created_at=created_at)
    config = ProblemSelectionConfig(recency_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    # Never tested: base 1.0, rate 1/20, days_since = 45 -> 1.0 + 45*(1/20) = 3.25
    assert breakdown.recency == 3.25


def test_recency_status_specific_rates():
    """Verify the four tracking states use their confirmed date references and rates."""
    now = datetime.now(timezone.utc)
    config = ProblemSelectionConfig(recency_weight=1.0)

    # Never tested, created 30 days ago -> createdAt reference, base 1.0, rate 1/20 -> 2.5
    never_tested = create_test_problem(
        "never", last_tested_at=None, created_at=now - timedelta(days=30)
    )
    assert compute_score_breakdown(never_tested, config, now).recency == 2.5

    # Last correct, tested 60 days ago -> lastTestedAt reference, base 0.0, rate 1/40 -> 1.5
    last_correct = create_test_problem(
        "correct", last_tested_at=now - timedelta(days=60), last_attempt_correct=True
    )
    assert compute_score_breakdown(last_correct, config, now).recency == 1.5

    # Last failed, tested 60 days ago -> lastTestedAt reference, base 0.5, rate 1/30 -> 2.5
    last_failed = create_test_problem(
        "failed", last_tested_at=now - timedelta(days=60), last_attempt_correct=False
    )
    assert compute_score_breakdown(last_failed, config, now).recency == 2.5

    # Tested 60 days ago with unknown last result -> lastTestedAt reference, base 0.0, rate 1/30 -> 2.0
    tested_unknown = create_test_problem(
        "unknown", last_tested_at=now - timedelta(days=60)
    )
    assert compute_score_breakdown(tested_unknown, config, now).recency == 2.0


def test_recency_tested_today_starts_at_zero():
    """A last-wrong problem tested today has 0.5 recency; a never-tested problem created today has 1.0."""
    now = datetime.now(timezone.utc)
    config = ProblemSelectionConfig(recency_weight=1.0)

    tested_today = create_test_problem(
        "tested", last_tested_at=now, last_attempt_correct=False
    )
    assert compute_score_breakdown(tested_today, config, now).recency == 0.5

    never_tested_today = create_test_problem(
        "never", last_tested_at=None, created_at=now
    )
    assert compute_score_breakdown(never_tested_today, config, now).recency == 1.0


def test_recency_weight_scales_status_specific_component():
    now = datetime.now(timezone.utc)
    # Never tested 30 days ago: raw recency = 2.5, scaled by recency_weight=2.0 -> 5.0
    problem = create_test_problem(
        "p1", last_tested_at=None, created_at=now - timedelta(days=30)
    )
    config = ProblemSelectionConfig(recency_weight=2.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.recency == 5.0


def test_recency_weight_scales_tested_component_and_base():
    """recency_weight scales the entire revised component (base + age) for tested problems too."""
    now = datetime.now(timezone.utc)
    # Last failed, tested 60 days ago: raw recency = 0.5 + 60*(1/30) = 2.5, scaled by 2.0 -> 5.0
    problem = create_test_problem(
        "p1", last_tested_at=now - timedelta(days=60), last_attempt_correct=False
    )
    config = ProblemSelectionConfig(recency_weight=2.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.recency == 5.0


@pytest.mark.parametrize(
    ("correct_count", "failed_count", "expected_recency"),
    [
        pytest.param(0, 1, 3.0, id="net_negative_keeps_base_rate"),
        pytest.param(3, 3, 3.0, id="net_zero_keeps_base_rate"),
        pytest.param(1, 0, 3.0, id="net_one_keeps_base_rate"),
        pytest.param(2, 0, 2.4, id="net_two_rate_1_over_50"),
        pytest.param(5, 0, 1.5, id="net_five_rate_1_over_80"),
        pytest.param(9, 0, 1.0, id="net_nine_rate_1_over_120"),
        pytest.param(16, 0, 120 / 190, id="net_sixteen_rate_1_over_190"),
    ],
)
def test_last_correct_recency_slows_with_net_correct_count(
    correct_count: int, failed_count: int, expected_recency: float
):
    """Last-correct daily rate is 1/(40 + 10*max(net - 1, 0)), uncapped (#706).

    Tested 120 days ago with recency_weight=1: net 1 -> 120/40 = 3.0,
    net 2 -> 120/50 = 2.4, net 5 -> 120/80 = 1.5, net 9 -> 120/120 = 1.0.
    """
    now = datetime.now(timezone.utc)
    problem = create_test_problem(
        "p1",
        last_tested_at=now - timedelta(days=120),
        last_attempt_correct=True,
        correct_count=correct_count,
        failed_count=failed_count,
        exposure_count=correct_count,
    )
    config = ProblemSelectionConfig(recency_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.recency == pytest.approx(expected_recency)


def test_last_correct_rate_depends_on_net_not_raw_counts():
    """Equal net counts with different correct/failed totals get the same rate (#706)."""
    now = datetime.now(timezone.utc)
    seven_correct = create_test_problem(
        "p1",
        last_tested_at=now - timedelta(days=80),
        last_attempt_correct=True,
        correct_count=7,
        failed_count=2,
        exposure_count=9,
    )
    five_correct = create_test_problem(
        "p2",
        last_tested_at=now - timedelta(days=80),
        last_attempt_correct=True,
        correct_count=5,
        failed_count=0,
        exposure_count=5,
    )
    config = ProblemSelectionConfig(recency_weight=1.0)
    # Both have net = 5 -> rate 1/80 -> 80/80 = 1.0.
    assert compute_score_breakdown(seven_correct, config, now).recency == pytest.approx(1.0)
    assert compute_score_breakdown(five_correct, config, now).recency == pytest.approx(1.0)


def test_last_correct_rate_continues_decreasing_without_cap():
    """No minimum positive rate or denominator cap above net 5 (#706)."""
    now = datetime.now(timezone.utc)
    config = ProblemSelectionConfig(recency_weight=1.0)
    recencies = []
    for net in (5, 16, 100):
        problem = create_test_problem(
            f"p{net}",
            last_tested_at=now - timedelta(days=120),
            last_attempt_correct=True,
            correct_count=net,
            failed_count=0,
            exposure_count=net,
        )
        recencies.append(compute_score_breakdown(problem, config, now).recency)
    assert recencies[0] > recencies[1] > recencies[2] > 0.0


def test_last_wrong_again_with_high_net_correct_keeps_last_wrong_rate():
    """A wrong-again problem gets no additional slowdown even with a large net count (#706)."""
    now = datetime.now(timezone.utc)
    problem = create_test_problem(
        "p1",
        last_tested_at=now - timedelta(days=60),
        last_attempt_correct=False,
        correct_count=10,
        failed_count=1,
        exposure_count=11,
    )
    config = ProblemSelectionConfig(recency_weight=1.0)
    # Unchanged last-wrong rule: base 0.5, rate 1/30 -> 0.5 + 60/30 = 2.5.
    assert compute_score_breakdown(problem, config, now).recency == 2.5


def test_tested_unknown_and_never_tested_ignore_net_correct_count():
    """Only last-correct problems use the net-count slowdown; other states keep their rates (#706)."""
    now = datetime.now(timezone.utc)
    config = ProblemSelectionConfig(recency_weight=1.0)

    tested_unknown = create_test_problem(
        "unknown",
        last_tested_at=now - timedelta(days=60),
        correct_count=10,
        failed_count=0,
        exposure_count=10,
    )
    # Tested unknown: base 0.0, rate 1/30 -> 2.0 (unchanged).
    assert compute_score_breakdown(tested_unknown, config, now).recency == 2.0

    never_tested = create_test_problem(
        "never",
        last_tested_at=None,
        created_at=now - timedelta(days=30),
        correct_count=16,
        failed_count=0,
        exposure_count=16,
    )
    # Never tested: base 1.0, rate 1/20 -> 2.5 (unchanged).
    assert compute_score_breakdown(never_tested, config, now).recency == 2.5


def test_last_correct_recency_weight_scaling_and_same_day_zero():
    """recency_weight scales the slowed last-correct component; same-day recency is zero (#706)."""
    config = ProblemSelectionConfig(recency_weight=2.0)
    now = datetime.now(timezone.utc)

    slowed = create_test_problem(
        "slowed",
        last_tested_at=now - timedelta(days=80),
        last_attempt_correct=True,
        correct_count=5,
        failed_count=0,
        exposure_count=5,
    )
    # Net 5 -> rate 1/80 -> raw 80/80 = 1.0, scaled by 2.0 -> 2.0.
    assert compute_score_breakdown(slowed, config, now).recency == pytest.approx(2.0)

    zero_weight = compute_score_breakdown(
        slowed, ProblemSelectionConfig(recency_weight=0.0), now
    )
    assert zero_weight.recency == 0.0

    tested_today = create_test_problem(
        "today", last_tested_at=now, last_attempt_correct=True
    )
    assert compute_score_breakdown(
        tested_today, ProblemSelectionConfig(recency_weight=1.0), now
    ).recency == 0.0


def test_last_correct_high_net_clamped_sampling_weight_preserves_raw_sum():
    """#706 acceptance: 9 correct / 0 failed / 120 days -> raw sum -1.5, sampling total 0.0.

    The problem is excluded from weighted sampling while its component
    values (the raw score consumers) stay untouched.
    """
    now = datetime.now(timezone.utc)
    problem = create_test_problem(
        "p1",
        last_tested_at=now - timedelta(days=120),
        last_attempt_correct=True,
        correct_count=9,
        failed_count=0,
        exposure_count=9,
        created_at=now - timedelta(days=200),
    )
    config = ProblemSelectionConfig(
        recency_weight=1.0, failure_rate_weight=1.0, last_wrong_weight=1.0
    )
    breakdown = compute_score_breakdown(problem, config, now)
    # Net 9 -> recency = 120 * 1/120 = 1.0; failure = -sqrt(9) = -3.0; last_wrong = 0.5.
    assert breakdown.recency == pytest.approx(1.0)
    assert breakdown.failure == pytest.approx(-3.0)
    assert breakdown.last_wrong == pytest.approx(0.5)
    raw_sum = breakdown.recency + breakdown.failure + breakdown.last_wrong
    assert raw_sum == pytest.approx(-1.5)
    # Sampling weight is clamped at zero; the raw component sum above stays -1.5.
    assert breakdown.total == 0.0
    assert select_problems([problem], 1, config, now=now) == []


def test_failure_score_no_attempts():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=0, failed_count=0, correct_count=0)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == 0.0


def test_failure_score_more_correct_than_failed():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=4, failed_count=0, correct_count=4)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == -2.0


def test_failure_score_more_failed_than_correct():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=4, failed_count=4, correct_count=0)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == 2.0


def test_failure_score_equal_failed_and_correct():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=10, failed_count=5, correct_count=5)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == 0.0


def test_failure_score_large_difference():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=9, failed_count=9, correct_count=0)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == 3.0


def test_failure_score_weighted_multiplier():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=4, failed_count=4, correct_count=0)
    config = ProblemSelectionConfig(failure_rate_weight=2.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == 4.0


def test_failure_score_ratio_when_more_failed_than_correct():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=6, failed_count=4, correct_count=2)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == 2.0


def test_failure_score_ratio_with_weight():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=10, failed_count=7, correct_count=3)
    config = ProblemSelectionConfig(failure_rate_weight=2.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == pytest.approx((7 / 3) * 2.0)


def test_failure_score_more_failed_than_correct_zero_correct_uses_sqrt():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=4, failed_count=4, correct_count=0)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == 2.0


def test_failure_score_ratio_total_positive():
    now = datetime.now(timezone.utc)
    problem = create_test_problem(
        "p1",
        last_tested_at=now - timedelta(days=30),
        last_attempt_correct=False,
        exposure_count=6,
        failed_count=4,
        correct_count=2,
    )
    config = ProblemSelectionConfig(
        recency_weight=1.0,
        failure_rate_weight=1.0,
        last_wrong_weight=1.0,
    )
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == 2.0
    assert breakdown.total == pytest.approx(breakdown.recency + breakdown.failure + breakdown.last_wrong)
    assert breakdown.total > 0


def test_last_wrong_never_tested():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", last_tested_at=None)
    config = ProblemSelectionConfig(last_wrong_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.last_wrong == 1.0


def test_last_wrong_correct():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", last_tested_at=now - timedelta(days=1), last_attempt_correct=True)
    config = ProblemSelectionConfig(last_wrong_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.last_wrong == 0.5


def test_last_wrong_wrong():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", last_tested_at=now - timedelta(days=1), last_attempt_correct=False)
    config = ProblemSelectionConfig(last_wrong_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.last_wrong == 2.0


def test_last_wrong_tested_unknown():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", last_tested_at=now - timedelta(days=1), last_attempt_correct=None)
    config = ProblemSelectionConfig(last_wrong_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.last_wrong == 1.0


def test_weighted_total_equals_component_sum():
    now = datetime.now(timezone.utc)
    last_tested = now - timedelta(days=30)
    problem = create_test_problem(
        "p1",
        last_tested_at=last_tested,
        last_attempt_correct=False,
        exposure_count=10,
        failed_count=7,
        correct_count=3,
    )
    config = ProblemSelectionConfig(
        recency_weight=1.0,
        failure_rate_weight=2.0,
        last_wrong_weight=1.5,
    )
    breakdown = compute_score_breakdown(problem, config, now)
    # recency_score = 0.5 + 30/30 = 1.5, recency = 1.5 * 1.0 = 1.5
    assert breakdown.recency == 1.5
    # failedCount=7 > correctCount=3 and correctCount>0 => ratio: 7/3
    # failure = (7/3) * 2.0 ≈ 4.6666666667
    assert breakdown.failure == pytest.approx((7 / 3) * 2.0)
    # last_wrong_score = 2.0, last_wrong = 2.0 * 1.5 = 3.0
    assert breakdown.last_wrong == 3.0
    assert breakdown.total == pytest.approx(1.5 + (7 / 3) * 2.0 + 3.0)
    assert breakdown.total == pytest.approx(breakdown.recency + breakdown.failure + breakdown.last_wrong)


def test_total_score_capped_at_zero():
    now = datetime.now(timezone.utc)
    problem = create_test_problem(
        "p1",
        exposure_count=10,
        failed_count=0,
        correct_count=10,
        last_attempt_correct=True,
    )
    config = ProblemSelectionConfig(
        recency_weight=0.0,
        failure_rate_weight=1.0,
        last_wrong_weight=1.0,
    )
    breakdown = compute_score_breakdown(problem, config, now)
    # failure = -sqrt(10) ≈ -3.162, last_wrong = 0.5, recency = 0
    assert breakdown.total == 0.0


def test_cooldown_excludes_recent():
    now = datetime.now(timezone.utc)
    problems = [
        create_test_problem("recent", last_tested_at=now - timedelta(days=2)),
        create_test_problem("old", last_tested_at=now - timedelta(days=10)),
    ]
    config = ProblemSelectionConfig(cooldown_days=7)
    eligible = get_eligible_problems(problems, config, now)
    ids = [p.id for p in eligible]
    assert "old" in ids
    assert "recent" not in ids


def test_zero_total_weights_excludes_all_candidates():
    now = datetime.now(timezone.utc)
    problems = [
        create_test_problem("a"),
        create_test_problem("b"),
    ]
    config = ProblemSelectionConfig(
        recency_weight=0.0,
        failure_rate_weight=0.0,
        last_wrong_weight=0.0,
    )
    import random
    rng = random.Random(42)
    selected = select_problems(problems, 1, config, now=now, rng=rng)
    assert selected == []


def test_zero_score_candidates_excluded_from_selection():
    now = datetime.now(timezone.utc)
    problems = [
        create_test_problem("zero", exposure_count=10, failed_count=0, correct_count=10, last_attempt_correct=True),
        create_test_problem("positive", exposure_count=10, failed_count=4, correct_count=0),
    ]
    config = ProblemSelectionConfig(
        recency_weight=0.0,
        failure_rate_weight=1.0,
        last_wrong_weight=0.0,
    )
    import random
    rng = random.Random(42)
    selected = select_problems(problems, 10, config, now=now, rng=rng)
    assert len(selected) == 1
    assert selected[0].id == "positive"


def test_multi_selection_no_duplicates():
    now = datetime.now(timezone.utc)
    problems = [
        create_test_problem("a"),
        create_test_problem("b"),
        create_test_problem("c"),
    ]
    config = ProblemSelectionConfig()
    import random
    rng = random.Random(42)
    selected = select_problems(problems, 3, config, now=now, rng=rng)
    assert len(selected) == 3
    assert len({p.id for p in selected}) == 3


def test_smaller_exam_when_fewer_eligible():
    now = datetime.now(timezone.utc)
    problems = [
        create_test_problem("a"),
        create_test_problem("b"),
    ]
    config = ProblemSelectionConfig()
    import random
    rng = random.Random(42)
    selected = select_problems(problems, 5, config, now=now, rng=rng)
    assert len(selected) == 2


def test_eligibility_excludes_unanswerable():
    now = datetime.now(timezone.utc)
    from app.domain import Tracking
    problem = Problem(
        id="no-answer",
        userId="u1",
        text="test",
        problemType=ProblemType.SINGLE_CHOICE,
        correctAnswer=CorrectAnswer(display="", normalizedText="", normalizedSet=[], format="single"),
        tracking=Tracking(),
        createdAt=now - timedelta(days=30),
    )
    config = ProblemSelectionConfig()
    eligible = get_eligible_problems([problem], config, now)
    assert eligible == []


def test_ensure_utc_naive():
    naive = datetime(2024, 1, 1, 12, 0, 0)
    result = ensure_utc(naive)
    assert result.tzinfo is not None
    assert result.hour == 12


def test_ensure_utc_aware():
    from datetime import timezone
    aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    result = ensure_utc(aware)
    assert result.tzinfo is not None
    assert result.hour == 12


def _picker_problem(
    problem_id: str,
    *,
    text: str = "problem",
    tags: list[str] | None = None,
    failed_count: int = 0,
    correct_count: int = 0,
    created_at: datetime | None = None,
    last_tested_at: datetime | None = None,
    last_attempt_correct: bool | None = None,
) -> Problem:
    """Picker-specific builder: controllable text/tags/counts for #680 tests."""
    from app.domain import Tracking

    tracking = Tracking(
        exposureCount=correct_count + failed_count,
        correctCount=correct_count,
        failedCount=failed_count,
    )
    if last_tested_at is not None:
        tracking.lastTestedAt = last_tested_at
    if last_attempt_correct is not None:
        tracking.lastAttemptCorrect = last_attempt_correct
    return Problem(
        id=problem_id,
        userId="u1",
        text=text,
        problemType=ProblemType.SINGLE_CHOICE,
        correctAnswer=CorrectAnswer(display="a", normalizedText="a", normalizedSet=[], format="single"),
        tags=tags or [],
        tracking=tracking,
        createdAt=created_at or datetime.now(timezone.utc) - timedelta(days=30),
    )


def _rank(problems, **kwargs):
    config = ProblemSelectionConfig(recency_weight=1.0, failure_rate_weight=1.0, last_wrong_weight=1.0)
    now = kwargs.pop("now", datetime.now(timezone.utc))
    return [p.id for p, _ in rank_eligible_problems(problems, config, now, **kwargs)]


def test_rank_keyword_filters_text_and_tags_case_insensitive():
    problems = [
        _picker_problem("1", text="What is 2+2?", tags=["algebra"]),
        _picker_problem("2", text="Capital of France?", tags=["GEOGRAPHY"]),
        _picker_problem("3", text="Plain text", tags=[]),
    ]

    assert _rank(problems, q="2+2") == ["1"]
    assert _rank(problems, q="france") == ["2"]
    assert _rank(problems, q="geo") == ["2"]


def test_rank_keyword_handles_regex_special_characters():
    problems = [_picker_problem("1", text="a.b+c(d)e*")]

    assert _rank(problems, q="a.b+c(d)e*") == ["1"]
    assert _rank(problems, q=".*") == []


def test_rank_excludes_min_age_and_cooldown_problems():
    now = datetime.now(timezone.utc)
    problems = [
        _picker_problem("fresh", created_at=now - timedelta(days=1)),
        _picker_problem("cooldown", last_tested_at=now - timedelta(days=2)),
        _picker_problem("ok", created_at=now - timedelta(days=30)),
    ]

    assert _rank(problems, now=now) == ["ok"]


def test_rank_sorts_by_all_columns_both_directions_with_id_tiebreak():
    now = datetime.now(timezone.utc)
    problems = [
        _picker_problem("a", text="a", correct_count=3, failed_count=1, created_at=now - timedelta(days=100)),
        _picker_problem("b", text="b", correct_count=3, failed_count=1, created_at=now - timedelta(days=200)),
        _picker_problem("c", text="c", correct_count=9, failed_count=0, created_at=now - timedelta(days=150)),
    ]

    assert _rank(problems, now=now, sort_criteria=[("failureCount", "desc")]) == ["a", "b", "c"]
    assert _rank(problems, now=now, sort_criteria=[("failureCount", "asc")]) == ["c", "a", "b"]
    assert _rank(problems, now=now, sort_criteria=[("correctStreak", "desc")], streaks={"a": 2, "b": 2, "c": 5}) == ["c", "a", "b"]
    assert _rank(problems, now=now, sort_criteria=[("correctStreak", "asc")], streaks={"a": 2, "b": 2, "c": 5}) == ["a", "b", "c"]
    assert _rank(problems, now=now, sort_criteria=[("addDate", "desc")]) == ["a", "c", "b"]
    assert _rank(problems, now=now, sort_criteria=[("addDate", "asc")]) == ["b", "c", "a"]
    # All four columns × both directions (#680): score order is monotonic in
    # age here (identical failure/last-wrong components), so the ranking is
    # b (200d) > c (150d) > a (100d).
    assert _rank(problems, now=now, sort_criteria=[("selectionScore", "desc")]) == ["b", "c", "a"]
    assert _rank(problems, now=now, sort_criteria=[("selectionScore", "asc")]) == ["a", "c", "b"]


def test_rank_multi_key_priority_across_directions_and_nulls():
    now = datetime.now(timezone.utc)
    problems = [
        _picker_problem("a", created_at=now - timedelta(days=100), failed_count=1),
        _picker_problem("b", created_at=now - timedelta(days=100), failed_count=1),
        _picker_problem("c", created_at=now - timedelta(days=300), failed_count=5),
        _picker_problem("d", created_at=now - timedelta(days=50), failed_count=0),
    ]
    streaks = {"a": 2, "b": 0, "c": None, "d": 2}

    # Streak ascending first (0 before 2, null last), then Added descending:
    # b (streak 0) | a/d tie on streak 2 -> newer first -> d, a | c null last
    # (nulls tie -> next criterion -> Added desc -> c created 300d ago).
    ranked = _rank(
        problems,
        now=now,
        sort_criteria=[("correctStreak", "asc"), ("addDate", "desc")],
        streaks=streaks,
    )
    assert ranked == ["b", "d", "a", "c"]

    # Streak descending flips the known groups but nulls stay last.
    ranked = _rank(
        problems,
        now=now,
        sort_criteria=[("correctStreak", "desc"), ("addDate", "desc")],
        streaks=streaks,
    )
    assert ranked == ["d", "a", "b", "c"]

    # Two nulls proceed to the next criterion: c and a second null-row tie on
    # streak, Added ascending (oldest first) then decides.
    e = _picker_problem("e", created_at=now - timedelta(days=10), failed_count=5)
    ranked = _rank(
        [problems[2], e],
        now=now,
        sort_criteria=[("correctStreak", "asc"), ("addDate", "asc")],
        streaks={"c": None, "e": None},
    )
    assert ranked == ["c", "e"]


def test_rank_all_criteria_ties_resolve_by_id_ascending():
    now = datetime.now(timezone.utc)
    problems = [
        _picker_problem("z9", created_at=now - timedelta(days=100), failed_count=1),
        _picker_problem("a2", created_at=now - timedelta(days=100), failed_count=1),
        _picker_problem("m5", created_at=now - timedelta(days=100), failed_count=1),
    ]
    streaks = {"z9": 1, "a2": 1, "m5": 1}

    # Identical streaks, dates, failures, and scores: id ascending regardless
    # of the criteria's directions.
    for direction in ("asc", "desc"):
        assert _rank(
            problems,
            now=now,
            sort_criteria=[("correctStreak", direction), ("failureCount", direction)],
            streaks=streaks,
        ) == ["a2", "m5", "z9"]


def test_rank_streak_sorting_is_global_before_pagination_boundaries():
    now = datetime.now(timezone.utc)
    problems = [
        _picker_problem(f"p{i:02d}", created_at=now - timedelta(days=100 + i), failed_count=i % 3)
        for i in range(10)
    ]
    streaks = {f"p{i:02d}": i for i in range(10)}

    ranked = _rank(
        problems,
        now=now,
        sort_criteria=[("correctStreak", "desc")],
        streaks=streaks,
    )
    # Page 2 of pageSize 4 must continue the global order, not re-sort locally.
    assert ranked[4:8] == ["p05", "p04", "p03", "p02"]


def test_rank_rejects_unknown_fields_and_directions():
    # Duplicate fields are the request layer's 422 concern (#708); the pure
    # sorter rejects unknown fields and directions.
    config = ProblemSelectionConfig()
    now = datetime.now(timezone.utc)
    scored = filter_and_score_problems([], config, now)

    with pytest.raises(ValueError):
        sort_scored_problems(scored, criteria=[("bogus", "asc")])
    with pytest.raises(ValueError):
        sort_scored_problems(scored, criteria=[("addDate", "sideways")])


def test_rank_default_is_selection_score_desc():
    now = datetime.now(timezone.utc)
    problems = [
        _picker_problem(
            "low",
            correct_count=8,
            failed_count=0,
            last_tested_at=now - timedelta(days=30),
            last_attempt_correct=True,
        ),
        _picker_problem("high", failed_count=5, correct_count=1),
    ]

    ranked = _rank(problems, now=now)
    assert ranked == ["high", "low"]
    explicit = _rank(problems, now=now, sort_criteria=[("selectionScore", "desc")])
    assert explicit == ranked
