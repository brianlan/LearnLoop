"""Pure XP-calculation tests for #712."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.domain.experience import XpProgress, compute_xp_progress

BASE = datetime(2026, 1, 1, tzinfo=UTC)
WEEK_START = datetime(2026, 2, 2, tzinfo=UTC)  # Monday
WEEK_END = datetime(2026, 2, 9, tzinfo=UTC)  # next Monday

# Offsets relative to BASE: 32 == Monday 00:00 of the week, 39 == next Monday.
def _week(
    records: list[tuple[datetime, str, str, str, str, str | None]],
) -> XpProgress:
    return compute_xp_progress(records, week_start=WEEK_START, week_end=WEEK_END)


def _record(
    offset_days: float,
    judgment: str | None,
    *,
    problem_id: str = "p1",
    answer: str | None = "42",
    source: str = "practice",
    record_id: str = "r",
) -> tuple[datetime, str, str, str, str, str | None]:
    return (
        BASE + timedelta(days=offset_days),
        source,
        record_id,
        problem_id,
        judgment,
        answer,
    )


def test_first_correct_earns_five() -> None:
    assert compute_xp_progress([_record(1, "correct")]).totalXp == 5


def test_repeated_correct_earns_five_each_without_streak_bonus() -> None:
    progress = compute_xp_progress(
        [
            _record(1, "correct", record_id="a"),
            _record(2, "correct", record_id="b"),
            _record(3, "correct", record_id="c"),
        ]
    )
    assert progress.totalXp == 15


def test_repeated_incorrect_earns_ten_each() -> None:
    progress = compute_xp_progress(
        [
            _record(1, "incorrect", record_id="a"),
            _record(2, "incorrect", record_id="b"),
        ]
    )
    assert progress.totalXp == 20


def test_recovery_is_exclusive_twenty_total() -> None:
    # Incorrect then correct: 10 + 20 = 30, not 10 + 20 + 5.
    progress = compute_xp_progress(
        [
            _record(1, "incorrect", record_id="a"),
            _record(2, "correct", record_id="b"),
        ]
    )
    assert progress.totalXp == 30


def test_recovery_then_repeat_correct_continues_at_five() -> None:
    progress = compute_xp_progress(
        [
            _record(1, "incorrect", record_id="a"),
            _record(2, "correct", record_id="b"),
            _record(3, "correct", record_id="c"),
        ]
    )
    assert progress.totalXp == 35


def test_mixed_problem_ids_track_predecessors_independently() -> None:
    # p1 recovery (10 + 20), p2 first correct (5).
    progress = compute_xp_progress(
        [
            _record(1, "incorrect", problem_id="p1", record_id="a"),
            _record(2, "correct", problem_id="p2", record_id="b"),
            _record(3, "correct", problem_id="p1", record_id="c"),
        ]
    )
    assert progress.totalXp == 35


def test_variant_problem_ids_are_independent() -> None:
    # Original incorrect does not make the variant's first correct a recovery.
    progress = compute_xp_progress(
        [
            _record(1, "incorrect", problem_id="orig", record_id="a"),
            _record(2, "correct", problem_id="variant", record_id="b"),
        ]
    )
    assert progress.totalXp == 15


def test_unlimited_repeats_keep_earning() -> None:
    progress = compute_xp_progress(
        [_record(i / 10, "incorrect", record_id=str(i)) for i in range(1, 11)]
    )
    assert progress.totalXp == 100


def test_unsorted_input_folds_chronologically() -> None:
    # Same records as the recovery test, supplied newest-first.
    progress = compute_xp_progress(
        [
            _record(2, "correct", record_id="b"),
            _record(1, "incorrect", record_id="a"),
        ]
    )
    assert progress.totalXp == 30


def test_blank_answer_earns_nothing_and_sets_no_predecessor() -> None:
    # Whitespace incorrect must not create the recovery predecessor.
    progress = compute_xp_progress(
        [
            _record(1, "incorrect", answer="   ", record_id="a"),
            _record(2, "correct", record_id="b"),
        ]
    )
    assert progress.totalXp == 5


def test_missing_answer_earns_nothing() -> None:
    assert compute_xp_progress([_record(1, "correct", answer=None)]).totalXp == 0


def test_pending_and_ungraded_records_are_skipped_without_predecessor() -> None:
    progress = compute_xp_progress(
        [
            _record(1, "pending-review", record_id="a"),
            _record(2, "ungraded", record_id="b"),
            _record(3, "correct", record_id="c"),
        ]
    )
    assert progress.totalXp == 5


def test_empty_history_returns_level_one_at_zero() -> None:
    progress = compute_xp_progress([])
    assert (progress.totalXp, progress.level, progress.xpInLevel, progress.xpToNextLevel) == (
        0,
        1,
        0,
        100,
    )


def test_level_thresholds() -> None:
    at_95 = compute_xp_progress(
        [_record(-1, "correct", record_id="first")]
        + [_record(i / 10, "incorrect", record_id=str(i)) for i in range(1, 10)]
    )
    assert at_95.totalXp == 95
    assert (at_95.level, at_95.xpInLevel, at_95.xpToNextLevel) == (1, 95, 5)

    at_100 = compute_xp_progress([_record(i / 10, "incorrect", record_id=str(i)) for i in range(1, 11)])
    assert (at_100.level, at_100.xpInLevel, at_100.xpToNextLevel) == (2, 0, 100)

    at_245 = compute_xp_progress(
        [_record(-1, "correct", record_id="first")]
        + [_record(i / 10, "incorrect", record_id=str(i)) for i in range(1, 25)]
    )
    assert at_245.totalXp == 245
    assert (at_245.level, at_245.xpInLevel, at_245.xpToNextLevel) == (3, 45, 55)


def test_tied_timestamps_break_ties_deterministically() -> None:
    # Same instant: the streak endpoint's (source, recordId) precedence makes
    # the practice record the older one, so the exam correct is a recovery.
    records = [
        _record(1, "correct", source="exam", record_id="b"),
        _record(1, "incorrect", source="practice", record_id="a"),
    ]
    assert compute_xp_progress(records).totalXp == 30


def test_older_pending_exam_resolved_after_newer_correct_practice_recovers() -> None:
    # Older exam incorrect (resolved later) precedes a newer practice correct
    # in submission order, so the practice correct is a recovery: 10 + 20.
    progress = compute_xp_progress(
        [
            _record(5, "correct", source="practice", record_id="new"),
            _record(1, "incorrect", source="exam", record_id="old"),
        ]
    )
    assert progress.totalXp == 30


def test_unusable_timestamps_are_skipped() -> None:
    records = [
        ("not-a-date", "practice", "a", "p1", "correct", "42"),
        _record(1, "correct", record_id="b"),
    ]
    assert compute_xp_progress(records).totalXp == 5


def test_mixed_naive_and_aware_utc_timestamps_normalize() -> None:
    # Mongo reads are naive UTC while other sources may carry tzinfo; the
    # sort key must normalize both instead of raising TypeError (#712).
    records = [
        (datetime(2026, 1, 2, tzinfo=UTC), "practice", "b", "p1", "correct", "x"),
        (datetime(2026, 1, 1), "practice", "a", "p1", "incorrect", "x"),
    ]
    progress = compute_xp_progress(records)
    # Older incorrect precedes the correct: 10 + recovery 20.
    assert progress.totalXp == 30
    assert progress.level == 1


def test_non_utc_offset_timestamps_sort_by_absolute_time() -> None:
    # 2026-01-01T21:00-05:00 == 2026-01-02T02:00Z, the newest of the three;
    # treating the wall-clock value as UTC would misorder the recovery.
    offset_est = timezone(timedelta(hours=-5))
    records = [
        (datetime(2026, 1, 1, 21, tzinfo=offset_est), "practice", "c", "p1", "correct", "x"),
        (datetime(2026, 1, 2), "practice", "a", "p1", "incorrect", "x"),
        (datetime(2026, 1, 2, 1, tzinfo=UTC), "practice", "b", "p1", "incorrect", "x"),
    ]
    # Order: a (00:00Z incorrect) -> b (01:00Z incorrect) -> c (02:00Z recovery).
    assert compute_xp_progress(records).totalXp == 40


def test_weekly_empty_history_has_zero_weekly_xp() -> None:
    progress = _week([])
    assert (progress.totalXp, progress.weeklyXp) == (0, 0)


def test_weekly_no_current_week_history_keeps_lifetime_total() -> None:
    progress = _week([_record(1, "correct"), _record(30, "incorrect")])
    assert (progress.totalXp, progress.weeklyXp) == (15, 0)


def test_weekly_counts_rewards_inside_the_week() -> None:
    # Repeated correct (5 each) and failures (10 each) all count weekly.
    progress = _week(
        [
            _record(32, "correct", record_id="a"),
            _record(33, "incorrect", record_id="b"),
            _record(34, "incorrect", record_id="c"),
        ]
    )
    assert (progress.totalXp, progress.weeklyXp) == (25, 25)


def test_weekly_boundaries_are_monday_inclusive_next_monday_exclusive() -> None:
    progress = _week(
        [
            _record(32, "correct", problem_id="p1", record_id="a"),  # Mon 00:00 exact
            _record(39, "correct", problem_id="p2", record_id="b"),  # next Mon 00:00 exact
            _record(31.9, "correct", problem_id="p3", record_id="c"),  # prior Sunday
        ]
    )
    assert (progress.totalXp, progress.weeklyXp) == (15, 5)


def test_weekly_recovery_across_start_boundary_counts_twenty() -> None:
    # Prior-week failure keeps its 10 out of the week; this-week correct is a
    # recovery 20 inside the week.
    progress = _week(
        [
            _record(31, "incorrect", record_id="a"),
            _record(33, "correct", record_id="b"),
        ]
    )
    assert (progress.totalXp, progress.weeklyXp) == (30, 20)


def test_weekly_variants_and_problems_track_rewards_independently() -> None:
    progress = _week(
        [
            _record(31, "incorrect", problem_id="p1", record_id="a"),
            _record(33, "correct", problem_id="p1", record_id="b"),
            _record(34, "correct", problem_id="variant", record_id="c"),
        ]
    )
    assert (progress.totalXp, progress.weeklyXp) == (35, 25)


def test_weekly_bounds_never_change_lifetime_totals() -> None:
    records = [
        _record(1, "incorrect", record_id="a"),
        _record(30, "correct", record_id="b"),
        _record(33, "correct", record_id="c"),
    ]
    with_bounds = _week(records)
    without_bounds = compute_xp_progress(records)
    assert (with_bounds.totalXp, with_bounds.level, with_bounds.xpInLevel, with_bounds.xpToNextLevel) == (
        without_bounds.totalXp,
        without_bounds.level,
        without_bounds.xpInLevel,
        without_bounds.xpToNextLevel,
    )
    assert with_bounds.totalXp == 35


def test_weekly_unsorted_and_tied_timestamps_count_chronologically() -> None:
    # Same pair as test_tied_timestamps_break_ties_deterministically, inside
    # one week: practice incorrect precedes the exam correct recovery.
    progress = _week(
        [
            _record(33, "correct", source="exam", record_id="b"),
            _record(33, "incorrect", source="practice", record_id="a"),
        ]
    )
    assert (progress.totalXp, progress.weeklyXp) == (30, 30)


def test_weekly_blank_and_pending_records_earn_nothing() -> None:
    progress = _week(
        [
            _record(33, "pending-review", record_id="a"),
            _record(34, "incorrect", answer="   ", record_id="b"),
            _record(35, "correct", record_id="c"),
        ]
    )
    assert (progress.totalXp, progress.weeklyXp) == (5, 5)


def test_late_resolved_last_week_failure_makes_this_week_correct_a_recovery() -> None:
    # Last-week exam incorrect (resolved after this week's success) precedes
    # the this-week correct in submission order: weekly 5 -> 20, and the
    # failure's own 10 stays in last week (#717).
    progress = _week(
        [
            _record(33, "correct", source="practice", record_id="new"),
            _record(31, "incorrect", source="exam", record_id="old"),
        ]
    )
    assert (progress.totalXp, progress.weeklyXp) == (30, 20)


def test_late_resolved_last_week_success_moves_recovery_out_of_this_week() -> None:
    # A last-week correct resolved late slots between an older failure and a
    # this-week correct: the recovery 20 moves to last week and the this-week
    # subtotal drops from 20 to 5 (#717).
    progress = _week(
        [
            _record(24, "incorrect", source="exam", record_id="old-failure"),
            _record(33, "correct", source="practice", record_id="this-week"),
            _record(31, "correct", source="exam", record_id="late-last-week"),
        ]
    )
    assert (progress.totalXp, progress.weeklyXp) == (35, 5)
