"""Trailing correct-streak calculation from merged judged attempt history (#708).

Pure module — no I/O. Records use the same tuple shape as the
attempt-history endpoint in ``presentation/problems.py``:
``(testedAt, source, recordId, result)``. Callers must merge practice
attempts (``createdAt``) and submitted-exam items (``submittedAt``) for the
owner/problem pair and must not include creation activities or
non-submitted exams.
"""

from __future__ import annotations

from datetime import datetime

_JUDGED_RESULTS = frozenset({"correct", "incorrect"})


def compute_correct_streak(
    records: list[tuple[datetime, str, str, str | None]],
    *,
    has_aggregate_attempts: bool,
) -> int | None:
    """Count correct results backwards from the newest judged result.

    Only explicit ``correct``/``incorrect`` results count; pending-review and
    ungraded records are skipped without breaking the streak. Records lacking
    a trustworthy timestamp are skipped, mirroring the attempt-history
    endpoint. Ordering is deterministic: ``testedAt`` desc with the
    endpoint's source/id tie-breaker, so deferred grading resolved later
    still sits at its original attempt time.

    Returns ``None`` only when no saved attempt records exist at all while
    aggregate tracking claims past attempts (legacy missing-history rows,
    displayed as ``—``). Saved-but-unjudged-only history returns ``0``,
    never ``None``.
    """
    usable = [record for record in records if isinstance(record[0], datetime)]
    usable.sort(key=lambda record: (record[1], record[2]))
    usable.sort(key=lambda record: record[0], reverse=True)

    judged = [record[3] for record in usable if record[3] in _JUDGED_RESULTS]
    if judged:
        streak = 0
        for result in judged:  # newest first
            if result != "correct":
                break
            streak += 1
        return streak
    if usable:  # saved attempts exist but none is judged
        return 0
    return None if has_aggregate_attempts else 0
