from datetime import datetime, timedelta, timezone
from app.domain import (
    Problem,
    CorrectAnswer,
    ProblemType,
    SelectionPolicyConfig,
    select_problems,
)


def create_test_problem(
    problem_id: str,
    is_deleted: bool = False,
    last_tested_days_ago: int | None = None,
    failed_count: int = 0,
    exposure_count: int = 1,
    created_at: datetime | None = None,
) -> Problem:
    from app.domain import Tracking
    tracking = Tracking(
        exposureCount=exposure_count,
        failedCount=failed_count,
    )
    if last_tested_days_ago is not None:
        tracking.lastTestedAt = datetime.now(timezone.utc) - timedelta(days=last_tested_days_ago)
    
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
    config = SelectionPolicyConfig(recencyWeight=1.0, failureWeight=1.0)
    selected = select_problems(problems, 2, config)
    assert len(selected) == 1
    assert selected[0].id == "1"


def test_selects_top_weighted():
    # Let's test the weighting logic without relying on random sampling
    from app.domain.selection import select_problems as original_select
    import random
    # Temporarily replace random.sample to return first N elements
    def mock_sample(pop, k):
        return pop[:k]
    
    original_random_sample = random.sample
    try:
        random.sample = mock_sample
        problems = [
            create_test_problem("1", last_tested_days_ago=1, failed_count=0),
            create_test_problem("2", last_tested_days_ago=30, failed_count=0),
            create_test_problem("3", last_tested_days_ago=1, failed_count=2, exposure_count=2),
        ]
        config = SelectionPolicyConfig(recencyWeight=1.0, failureWeight=1.0)
        selected = select_problems(problems, 2, config)
        assert len(selected) == 2
        selected_ids = [p.id for p in selected]
        # Verify top 2 weighted are 3 then 2 (since they have higher weights)
        assert selected_ids == ["3", "2"]
    finally:
        random.sample = original_random_sample


def test_min_age_excludes_too_new():
    now = datetime.now(timezone.utc)
    too_new = now - timedelta(days=1)
    old_enough = now - timedelta(days=5)
    problems = [
        create_test_problem("new", created_at=too_new),
        create_test_problem("old", created_at=old_enough),
    ]
    config = SelectionPolicyConfig(recencyWeight=1.0, failureWeight=1.0, minProblemAgeDays=3)
    selected = select_problems(problems, 2, config)

    assert len(selected) == 1
    assert selected[0].id == "old"


def test_min_age_boundary_at_threshold():
    now = datetime.now(timezone.utc)
    exactly_at = now - timedelta(days=3)
    problems = [create_test_problem("boundary", created_at=exactly_at)]
    config = SelectionPolicyConfig(recencyWeight=1.0, failureWeight=1.0, minProblemAgeDays=3)
    selected = select_problems(problems, 1, config)

    assert len(selected) == 1
    assert selected[0].id == "boundary"


def test_min_age_zero_allows_immediate():
    now = datetime.now(timezone.utc)
    just_created = now - timedelta(seconds=10)
    problems = [create_test_problem("fresh", created_at=just_created)]
    config = SelectionPolicyConfig(recencyWeight=1.0, failureWeight=1.0, minProblemAgeDays=0)
    selected = select_problems(problems, 1, config)

    assert len(selected) == 1
    assert selected[0].id == "fresh"


def test_min_age_all_too_new_returns_empty():
    now = datetime.now(timezone.utc)
    too_new = now - timedelta(days=1)
    problems = [
        create_test_problem("a", created_at=too_new),
        create_test_problem("b", created_at=too_new),
    ]
    config = SelectionPolicyConfig(recencyWeight=1.0, failureWeight=1.0, minProblemAgeDays=3)
    selected = select_problems(problems, 2, config)

    assert selected == []


def test_min_age_default_value():
    config = SelectionPolicyConfig(recencyWeight=1.0, failureWeight=1.0)
    assert config.minProblemAgeDays == 3
