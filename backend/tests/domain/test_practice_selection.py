from datetime import UTC, datetime, timedelta
import random

import pytest

from app.domain.models import CorrectAnswer, Problem, Tracking
from app.domain.practice_selection import (
    PracticeSelectionConfig,
    PracticeSelectionResult,
    compute_problem_weight_breakdown,
    get_eligible_practice_problems,
    select_practice_problem,
)


def _make_problem(
    pid: str,
    text: str = "Test problem",
    is_deleted: bool = False,
    correct_answer: str | None = "answer",
    last_tested_at: datetime | None = None,
    last_attempt_correct: bool | None = None,
    exposure_count: int = 0,
    correct_count: int | None = None,
    failed_count: int = 0,
    created_at: datetime | None = None,
) -> Problem:
    cc = correct_count if correct_count is not None else max(0, exposure_count - failed_count)
    return Problem(
        id=pid,
        userId="user1",
        text=text,
        problemType="single-choice",
        correctAnswer=CorrectAnswer(
            display=correct_answer or "",
            normalizedText=correct_answer or "",
            normalizedSet=[correct_answer] if correct_answer else [],
            format="single",
        ),
        tracking=Tracking(
            exposureCount=exposure_count,
            correctCount=cc,
            failedCount=failed_count,
            lastTestedAt=last_tested_at,
            lastAttemptCorrect=last_attempt_correct,
        ),
        isDeleted=is_deleted,
        createdAt=created_at or datetime.now(UTC) - timedelta(days=30),
    )


def test_cooldown_enforcement():
    now = datetime.now(UTC)
    recent = now - timedelta(days=2)
    old = now - timedelta(days=10)

    problems = [
        _make_problem("recent", last_tested_at=recent),
        _make_problem("old", last_tested_at=old),
    ]
    config = PracticeSelectionConfig(cooldown_days=7)
    result = select_practice_problem(problems, config, now)

    assert result.status == "ok"
    assert result.selected_problem.id == "old"


def test_shared_cooldown_exam_within_period():
    now = datetime.now(UTC)
    recent = now - timedelta(days=3)

    problems = [_make_problem("exam-tested", last_tested_at=recent)]
    config = PracticeSelectionConfig(cooldown_days=7)
    result = select_practice_problem(problems, config, now)

    assert result.status == "no_eligible"


def test_last_wrong_boost():
    now = datetime.now(UTC)
    problems = [
        _make_problem("correct", last_attempt_correct=True, exposure_count=1, failed_count=0),
        _make_problem("wrong", last_attempt_correct=False, exposure_count=1, failed_count=1),
    ]
    config = PracticeSelectionConfig()
    rng = random.Random(42)

    wrong_count = 0
    for _ in range(100):
        result = select_practice_problem(problems, config, now, rng=rng)
        if result.selected_problem.id == "wrong":
            wrong_count += 1

    assert wrong_count > 50


def test_failure_rate_weight():
    now = datetime.now(UTC)
    problems = [
        _make_problem("low-fail", exposure_count=10, failed_count=1),
        _make_problem("high-fail", exposure_count=10, failed_count=8),
    ]
    config = PracticeSelectionConfig()
    rng = random.Random(42)

    high_fail_count = 0
    for _ in range(100):
        result = select_practice_problem(problems, config, now, rng=rng)
        if result.selected_problem.id == "high-fail":
            high_fail_count += 1

    assert high_fail_count > 50


def test_recency_weight():
    now = datetime.now(UTC)
    old = now - timedelta(days=60)
    recent = now - timedelta(days=5)

    problems = [
        _make_problem("recent-test", last_tested_at=recent),
        _make_problem("old-test", last_tested_at=old),
    ]
    config = PracticeSelectionConfig(cooldown_days=3)
    rng = random.Random(42)

    old_count = 0
    for _ in range(100):
        result = select_practice_problem(problems, config, now, rng=rng)
        if result.selected_problem.id == "old-test":
            old_count += 1

    assert old_count > 50


def test_all_equal_weights_baseline():
    now = datetime.now(UTC)
    problems = [
        _make_problem("a"),
        _make_problem("b"),
        _make_problem("c"),
    ]
    config = PracticeSelectionConfig()
    rng = random.Random(42)

    counts = {"a": 0, "b": 0, "c": 0}
    for _ in range(300):
        result = select_practice_problem(problems, config, now, rng=rng)
        counts[result.selected_problem.id] += 1

    assert all(c > 50 for c in counts.values())


def test_zero_eligible_problems():
    now = datetime.now(UTC)
    problems = [_make_problem("deleted", is_deleted=True)]
    config = PracticeSelectionConfig()
    result = select_practice_problem(problems, config, now)

    assert result.status == "no_problems"


def test_all_in_cooldown():
    now = datetime.now(UTC)
    recent = now - timedelta(days=2)

    problems = [
        _make_problem("a", last_tested_at=recent),
        _make_problem("b", last_tested_at=recent),
    ]
    config = PracticeSelectionConfig(cooldown_days=7)
    result = select_practice_problem(problems, config, now)

    assert result.status == "no_eligible"


def test_never_tested_always_eligible():
    now = datetime.now(UTC)
    problems = [
        _make_problem("never-tested", last_tested_at=None),
        _make_problem("recent-test", last_tested_at=now - timedelta(days=2)),
    ]
    config = PracticeSelectionConfig(cooldown_days=7)
    result = select_practice_problem(problems, config, now)

    assert result.status == "ok"
    assert result.selected_problem.id == "never-tested"


def test_default_config_values():
    config = PracticeSelectionConfig()

    assert config.cooldown_days == 7
    assert config.last_wrong_weight == 1.0
    assert config.failure_rate_weight == 1.0
    assert config.recency_weight == 1.0


def test_deleted_problems_excluded():
    now = datetime.now(UTC)
    problems = [
        _make_problem("deleted", is_deleted=True),
        _make_problem("active"),
    ]
    config = PracticeSelectionConfig()
    result = select_practice_problem(problems, config, now)

    assert result.status == "ok"
    assert result.selected_problem.id == "active"


def test_empty_answer_excluded():
    now = datetime.now(UTC)
    problems = [
        _make_problem("no-answer", correct_answer=""),
        _make_problem("has-answer", correct_answer="answer"),
    ]
    config = PracticeSelectionConfig()
    result = select_practice_problem(problems, config, now)

    assert result.status == "ok"
    assert result.selected_problem.id == "has-answer"


def test_empty_problem_list():
    now = datetime.now(UTC)
    config = PracticeSelectionConfig()
    result = select_practice_problem([], config, now)

    assert result.status == "no_problems"
    assert result.selected_problem is None


def test_failure_rate_uses_attempts_not_exposures():
    now = datetime.now(UTC)
    problems = [
        _make_problem("many-exposures-no-attempts", exposure_count=100, correct_count=0, failed_count=0),
        _make_problem("some-failed-attempts", exposure_count=5, correct_count=1, failed_count=4),
    ]
    config = PracticeSelectionConfig(failure_rate_weight=2.0)
    rng = random.Random(42)

    failed_count = 0
    for _ in range(100):
        result = select_practice_problem(problems, config, now, rng=rng)
        if result.selected_problem.id == "some-failed-attempts":
            failed_count += 1

    assert failed_count > 50


def test_get_eligible_excludes_cooldown():
    now = datetime.now(UTC)
    recent = now - timedelta(days=2)
    old = now - timedelta(days=10)

    problems = [
        _make_problem("recent", last_tested_at=recent),
        _make_problem("old", last_tested_at=old),
        _make_problem("never", last_tested_at=None),
    ]
    config = PracticeSelectionConfig(cooldown_days=7)
    eligible = get_eligible_practice_problems(problems, config, now)

    ids = [p.id for p in eligible]
    assert "old" in ids
    assert "never" in ids
    assert "recent" not in ids


def test_get_eligible_excludes_deleted_and_no_answer():
    now = datetime.now(UTC)
    problems = [
        _make_problem("deleted", is_deleted=True),
        _make_problem("no-answer", correct_answer=""),
        _make_problem("valid"),
    ]
    config = PracticeSelectionConfig()
    eligible = get_eligible_practice_problems(problems, config, now)

    assert len(eligible) == 1
    assert eligible[0].id == "valid"


def test_get_eligible_empty():
    now = datetime.now(UTC)
    config = PracticeSelectionConfig()
    eligible = get_eligible_practice_problems([], config, now)
    assert eligible == []


def test_get_eligible_all_in_cooldown():
    now = datetime.now(UTC)
    recent = now - timedelta(days=2)
    problems = [
        _make_problem("a", last_tested_at=recent),
        _make_problem("b", last_tested_at=recent),
    ]
    config = PracticeSelectionConfig(cooldown_days=7)
    eligible = get_eligible_practice_problems(problems, config, now)
    assert eligible == []


def test_min_age_excludes_too_new():
    now = datetime.now(UTC)
    too_new = now - timedelta(days=1)
    old_enough = now - timedelta(days=5)
    problems = [
        _make_problem("new", created_at=too_new),
        _make_problem("old", created_at=old_enough),
    ]
    config = PracticeSelectionConfig(min_problem_age_days=3)
    result = select_practice_problem(problems, config, now)

    assert result.status == "ok"
    assert result.selected_problem.id == "old"


def test_min_age_boundary_at_threshold():
    now = datetime.now(UTC)
    exactly_at = now - timedelta(days=3)
    problems = [_make_problem("boundary", created_at=exactly_at)]
    config = PracticeSelectionConfig(min_problem_age_days=3)
    result = select_practice_problem(problems, config, now)

    assert result.status == "ok"
    assert result.selected_problem.id == "boundary"


def test_min_age_allows_old_problems():
    now = datetime.now(UTC)
    old = now - timedelta(days=10)
    problems = [_make_problem("old", created_at=old)]
    config = PracticeSelectionConfig(min_problem_age_days=3)
    result = select_practice_problem(problems, config, now)

    assert result.status == "ok"
    assert result.selected_problem.id == "old"


def test_min_age_zero_allows_immediate():
    now = datetime.now(UTC)
    just_created = now - timedelta(seconds=10)
    problems = [_make_problem("fresh", created_at=just_created)]
    config = PracticeSelectionConfig(min_problem_age_days=0)
    result = select_practice_problem(problems, config, now)

    assert result.status == "ok"
    assert result.selected_problem.id == "fresh"


def test_min_age_no_eligible_when_all_too_new():
    now = datetime.now(UTC)
    too_new = now - timedelta(days=1)
    problems = [
        _make_problem("a", created_at=too_new),
        _make_problem("b", created_at=too_new),
    ]
    config = PracticeSelectionConfig(min_problem_age_days=3)
    result = select_practice_problem(problems, config, now)

    assert result.status == "no_eligible"


def test_get_eligible_excludes_too_new():
    now = datetime.now(UTC)
    too_new = now - timedelta(days=1)
    old_enough = now - timedelta(days=5)
    problems = [
        _make_problem("new", created_at=too_new),
        _make_problem("old", created_at=old_enough),
    ]
    config = PracticeSelectionConfig(min_problem_age_days=3)
    eligible = get_eligible_practice_problems(problems, config, now)

    ids = [p.id for p in eligible]
    assert "old" in ids
    assert "new" not in ids


def test_compute_problem_weight_breakdown_all_components():
    now = datetime.now(UTC)
    last_tested = now - timedelta(days=30)
    problem = _make_problem(
        "p1",
        last_attempt_correct=False,
        exposure_count=10,
        failed_count=7,
        correct_count=3,
        last_tested_at=last_tested,
    )
    config = PracticeSelectionConfig(
        last_wrong_weight=1.5,
        failure_rate_weight=2.0,
        recency_weight=1.0,
    )
    breakdown = compute_problem_weight_breakdown(problem, config, now)

    # last_wrong_score = 2.0 because lastAttemptCorrect is False
    # lastWrong = 2.0 * 1.5 = 3.0
    assert breakdown.lastWrong == 3.0

    # failedCount=7 > correctCount=3 and correctCount>0 => ratio: 7/3
    # failure = (7/3) * 2.0 ≈ 4.6666666667
    assert breakdown.failure == pytest.approx((7 / 3) * 2.0)

    # days_since = 30, recency_score = 0.0 + 30/30 = 1.0
    # recency = 1.0 * 1.0 = 1.0
    assert breakdown.recency == 1.0

    assert breakdown.total == pytest.approx(3.0 + (7 / 3) * 2.0 + 1.0)
    assert breakdown.total == pytest.approx(breakdown.lastWrong + breakdown.failure + breakdown.recency)


def test_compute_problem_weight_breakdown_never_tested():
    now = datetime.now(UTC)
    problem = _make_problem("p1", last_tested_at=None, last_attempt_correct=None, created_at=now - timedelta(days=30))
    config = PracticeSelectionConfig()
    breakdown = compute_problem_weight_breakdown(problem, config, now)

    assert breakdown.lastWrong == 1.0
    assert breakdown.failure == 0.0
    # Never tested: createdAt (30 days ago), rate 1/20 -> 1.0 + 30*(1/20) = 2.5
    assert breakdown.recency == 2.5
    assert breakdown.total == 3.5


def test_compute_problem_weight_breakdown_zero_attempts():
    now = datetime.now(UTC)
    problem = _make_problem("p1", exposure_count=0, correct_count=0, failed_count=0, created_at=now - timedelta(days=30))
    config = PracticeSelectionConfig()
    breakdown = compute_problem_weight_breakdown(problem, config, now)

    assert breakdown.lastWrong == 1.0
    assert breakdown.failure == 0.0
    # Never tested: createdAt (30 days ago), rate 1/20 -> 1.0 + 30*(1/20) = 2.5
    assert breakdown.recency == 2.5
    assert breakdown.total == 3.5


def test_compute_problem_weight_breakdown_uses_same_total_as_selection():
    now = datetime.now(UTC)
    last_tested = now - timedelta(days=15)
    problem = _make_problem(
        "p1",
        last_attempt_correct=True,
        exposure_count=8,
        failed_count=2,
        last_tested_at=last_tested,
    )
    config = PracticeSelectionConfig()
    breakdown = compute_problem_weight_breakdown(problem, config, now)

    # Regression check: the total from the breakdown helper must equal
    # the weight used internally by select_practice_problem.
    from app.domain.practice_selection import _compute_problem_weight

    internal_total = _compute_problem_weight(problem, config, now)
    assert breakdown.total == internal_total
