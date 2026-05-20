from datetime import UTC, datetime, timedelta
import random

from app.domain.models import CorrectAnswer, Problem, Tracking
from app.domain.practice_selection import (
    PracticeSelectionConfig,
    PracticeSelectionResult,
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
    failed_count: int = 0,
) -> Problem:
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
            correctCount=exposure_count - failed_count if exposure_count >= failed_count else 0,
            failedCount=failed_count,
            lastTestedAt=last_tested_at,
            lastAttemptCorrect=last_attempt_correct,
        ),
        isDeleted=is_deleted,
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
