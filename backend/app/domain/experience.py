"""Lifetime XP and level calculation from merged judged attempt history (#712).

Pure module — no I/O. Records use ``(attemptedAt, source, recordId,
problemId, judgment, answer)`` tuples. Callers merge practice attempts
(``createdAt``, ``submittedAnswer``) and submitted-exam items (task
``createdAt`` fallback ``submittedAt``, ``answer.raw``) for the owner and
must not include creation activities, non-submitted exams, or filter by the
active problem pool.

Ordering mirrors ``attempt_streak``: deterministic newest-first sort with
the source/record-id tie-breaker, folded oldest-first so a later-resolved
judgment lands at its original submission position and recovery bonuses
recalculate from history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .selection import ensure_utc

XP_FOR_INCORRECT = 10
XP_FOR_CORRECT = 5
XP_FOR_RECOVERY = 20
XP_PER_LEVEL = 100

_JUDGED_RESULTS = frozenset({"correct", "incorrect"})


@dataclass(frozen=True)
class XpProgress:
    totalXp: int
    level: int
    xpInLevel: int
    xpToNextLevel: int
    weeklyXp: int


def compute_xp_progress(
    records: list[tuple[datetime, str, str, str, str, str | None]],
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> XpProgress:
    """Fold eligible judged attempts oldest-first into lifetime XP.

    Only explicit ``correct``/``incorrect`` judgments with a nonblank answer
    qualify. Blank/whitespace, pending-review, and ungraded records neither
    earn XP nor establish a predecessor. Incorrect earns 10; correct earns 5,
    or 20 when recovering from a previous incorrect judgment on the same
    problem (exclusive — recovery is 20 total, never 20 + 5).

    When concrete half-open ``week_start``/``week_end`` bounds are given, each
    earned reward is also added to ``weeklyXp`` when its normalized submission
    timestamp falls inside the interval. Records are never filtered before the
    fold, so prior-week predecessor context keeps driving recovery bonuses.
    """
    usable = [record for record in records if isinstance(record[0], datetime)]
    usable.sort(key=lambda record: (record[1], record[2]))
    usable.sort(key=lambda record: ensure_utc(record[0]), reverse=True)

    start = ensure_utc(week_start) if week_start is not None else None
    end = ensure_utc(week_end) if week_end is not None else None

    total_xp = 0
    weekly_xp = 0
    previous_judgment: dict[str, str] = {}
    for submitted_at, _, _, problem_id, judgment, answer in reversed(usable):  # oldest first
        if judgment not in _JUDGED_RESULTS:
            continue
        if not isinstance(answer, str) or not answer.strip():
            continue
        if judgment == "incorrect":
            reward = XP_FOR_INCORRECT
        else:
            was_incorrect = previous_judgment.get(problem_id) == "incorrect"
            reward = XP_FOR_RECOVERY if was_incorrect else XP_FOR_CORRECT
        total_xp += reward
        if start is not None and end is not None and start <= ensure_utc(submitted_at) < end:
            weekly_xp += reward
        previous_judgment[problem_id] = judgment

    return XpProgress(
        totalXp=total_xp,
        level=1 + total_xp // XP_PER_LEVEL,
        xpInLevel=total_xp % XP_PER_LEVEL,
        xpToNextLevel=XP_PER_LEVEL - total_xp % XP_PER_LEVEL,
        weeklyXp=weekly_xp,
    )
