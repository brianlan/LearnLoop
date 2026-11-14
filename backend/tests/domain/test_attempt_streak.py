"""Pure streak-calculation tests for #708 (issue MANUAL-1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.attempt_streak import compute_correct_streak

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _record(
    offset_days: float,
    result: str | None,
    *,
    source: str = "practice",
    record_id: str = "r",
) -> tuple[datetime, str, str, str | None]:
    return (BASE + timedelta(days=offset_days), source, record_id, result)


def test_trailing_streak_counts_backwards_until_first_incorrect():
    # C, C, I, C, C chronologically -> newest first is C, C, I -> 2.
    records = [
        _record(1, "correct", record_id="a"),
        _record(2, "correct", record_id="b"),
        _record(3, "incorrect", record_id="c"),
        _record(4, "correct", record_id="d"),
        _record(5, "correct", record_id="e"),
    ]

    assert compute_correct_streak(records, has_aggregate_attempts=True) == 2


def test_latest_judged_incorrect_displays_zero():
    records = [
        _record(1, "correct", record_id="a"),
        _record(2, "correct", record_id="b"),
        _record(3, "incorrect", record_id="c"),
    ]

    assert compute_correct_streak(records, has_aggregate_attempts=True) == 0


def test_unjudged_records_are_skipped_without_breaking_streak():
    # I, C, pending-review, C -> pending skipped -> 2.
    records = [
        _record(1, "incorrect", record_id="a"),
        _record(2, "correct", record_id="b"),
        _record(3, "pending-review", record_id="c"),
        _record(4, "correct", record_id="d"),
    ]

    assert compute_correct_streak(records, has_aggregate_attempts=True) == 2


def test_ungraded_records_are_skipped_without_breaking_streak():
    records = [
        _record(1, "correct", record_id="a"),
        _record(2, "ungraded", record_id="b"),
        _record(3, "correct", record_id="c"),
    ]

    assert compute_correct_streak(records, has_aggregate_attempts=True) == 2


def test_pending_only_history_returns_zero_not_missing():
    assert (
        compute_correct_streak(
            [_record(1, "pending-review", record_id="a")],
            has_aggregate_attempts=True,
        )
        == 0
    )


def test_no_history_without_aggregate_attempts_returns_zero():
    assert compute_correct_streak([], has_aggregate_attempts=False) == 0


def test_aggregate_attempts_without_saved_history_returns_none():
    # Legacy rows: tracking counters nonzero but no attempt records exist.
    assert compute_correct_streak([], has_aggregate_attempts=True) is None


def test_partial_history_counts_only_saved_records():
    # Aggregate claims 5 attempts but only two judged records survived:
    # never fabricate events; count what exists.
    records = [
        _record(4, "correct", record_id="a"),
        _record(5, "correct", record_id="b"),
    ]

    assert compute_correct_streak(records, has_aggregate_attempts=True) == 2


def test_merged_sources_order_by_attempt_time_not_source():
    # Newest is an exam item (submittedAt), streak crosses sources.
    records = [
        _record(1, "correct", source="practice", record_id="p1"),
        _record(2, "correct", source="exam", record_id="e1"),
        _record(3, "correct", source="practice", record_id="p2"),
    ]

    assert compute_correct_streak(records, has_aggregate_attempts=True) == 3


def test_tied_timestamps_use_deterministic_source_id_tiebreak():
    # Same instant: "exam" sorts after "practice" in the endpoint's source
    # tiebreak, so with an incorrect exam and correct practice tied, the
    # exam (newest) breaks the streak.
    tied = (BASE + timedelta(days=1),)
    records = [
        (tied[0], "practice", "p1", "correct"),
        (tied[0], "exam", "e1", "incorrect"),
    ]

    assert compute_correct_streak(records, has_aggregate_attempts=True) == 0


def test_deferred_grading_keeps_original_attempt_position():
    # I, C, pending, C -> 2 while the pending item awaits resolution.
    records = [
        _record(1, "incorrect", record_id="a"),
        _record(2, "correct", record_id="b"),
        _record(3, "pending-review", record_id="c"),
        _record(4, "correct", record_id="d"),
    ]
    assert compute_correct_streak(records, has_aggregate_attempts=True) == 2

    # Resolved to incorrect at its ORIGINAL position (day 3): C(d4), I(d3) -> 1.
    resolved_wrong = [
        _record(1, "incorrect", record_id="a"),
        _record(2, "correct", record_id="b"),
        _record(3, "incorrect", record_id="c"),
        _record(4, "correct", record_id="d"),
    ]
    assert compute_correct_streak(resolved_wrong, has_aggregate_attempts=True) == 1

    # Resolved to correct: C(d4), C(d3), C(d2), I(d1) -> 3.
    resolved_right = [
        _record(1, "incorrect", record_id="a"),
        _record(2, "correct", record_id="b"),
        _record(3, "correct", record_id="c"),
        _record(4, "correct", record_id="d"),
    ]
    assert compute_correct_streak(resolved_right, has_aggregate_attempts=True) == 3


def test_untrustworthy_timestamps_are_skipped():
    # A non-datetime timestamp record is dropped, mirroring the history
    # endpoint; the remaining judged history decides the streak.
    records = [
        ("not-a-datetime", "practice", "bad", "incorrect"),  # type: ignore[list-item]
        _record(1, "correct", record_id="good"),
    ]

    assert compute_correct_streak(records, has_aggregate_attempts=True) == 1
