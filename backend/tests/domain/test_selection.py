from datetime import datetime, timedelta, timezone
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
    get_eligible_problems,
    select_problems,
)


def create_test_problem(
    problem_id: str,
    is_deleted: bool = False,
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
    # days_since = 60, recency_score = 1.0 + 60/30 = 3.0
    assert breakdown.recency == 3.0


def test_recency_uses_created_at_when_not_tested():
    now = datetime.now(timezone.utc)
    created_at = now - timedelta(days=45)
    problem = create_test_problem("p1", last_tested_at=None, created_at=created_at)
    config = ProblemSelectionConfig(recency_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    # days_since = 45, recency_score = 1.0 + 45/30 = 2.5
    assert breakdown.recency == 2.5


def test_failure_score_zero_percent():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=10, failed_count=0, correct_count=10)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    # failure_rate = 0/10 = 0, failure_score = 1.0 + (0 - 0.5)/0.5 = 0.0
    assert breakdown.failure == 0.0


def test_failure_score_fifty_percent():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=10, failed_count=5, correct_count=5)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    # failure_rate = 5/10 = 0.5, failure_score = 1.0 + (0.5 - 0.5)/0.5 = 1.0
    assert breakdown.failure == 1.0


def test_failure_score_hundred_percent():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=10, failed_count=10, correct_count=0)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    # failure_rate = 10/10 = 1.0, failure_score = 1.0 + (1.0 - 0.5)/0.5 = 2.0
    assert breakdown.failure == 2.0


def test_failure_score_no_attempts():
    now = datetime.now(timezone.utc)
    problem = create_test_problem("p1", exposure_count=0, failed_count=0, correct_count=0)
    config = ProblemSelectionConfig(failure_rate_weight=1.0)
    breakdown = compute_score_breakdown(problem, config, now)
    assert breakdown.failure == 1.0


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
        failed_count=5,
        correct_count=5,
    )
    config = ProblemSelectionConfig(
        recency_weight=1.0,
        failure_rate_weight=2.0,
        last_wrong_weight=1.5,
    )
    breakdown = compute_score_breakdown(problem, config, now)
    # recency_score = 1.0 + 30/30 = 2.0, recency = 2.0 * 1.0 = 2.0
    assert breakdown.recency == 2.0
    # failure_rate = 5/10 = 0.5, failure_score = 1.0 + (0.5 - 0.5)/0.5 = 1.0, failure = 1.0 * 2.0 = 2.0
    assert breakdown.failure == 2.0
    # last_wrong_score = 2.0, last_wrong = 2.0 * 1.5 = 3.0
    assert breakdown.last_wrong == 3.0
    assert breakdown.total == 7.0
    assert breakdown.total == breakdown.recency + breakdown.failure + breakdown.last_wrong


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


def test_zero_total_weights_fallback_uniform():
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
    assert len(selected) == 1
    assert selected[0].id in ("a", "b")


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
