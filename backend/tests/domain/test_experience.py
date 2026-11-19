"""Pure XP-calculation tests for #712."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.domain.experience import compute_xp_progress

BASE = datetime(2026, 1, 1, tzinfo=UTC)


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
