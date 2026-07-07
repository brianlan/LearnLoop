from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import IngestionPreviewStatus, ExamState

# Domain-owned failure code for stale-preview recovery.
FAILURE_CODE_STALE_PREVIEW = "vlm-stale-preview-timeout"


class InvalidStateTransitionError(Exception):
    pass


def recover_stale_preview(
    preview: Mapping[str, Any],
    *,
    now: datetime | None = None,
    extracting_window_seconds: float,
) -> dict[str, Any] | None:
    status = preview.get("status")
    if status != "extracting":
        return None

    current_time = now or datetime.now(UTC)
    extraction = deepcopy(dict(preview.get("extraction", {})))
    started_at = extraction.get("requestStartedAt") or preview.get("updatedAt") or preview.get("createdAt")
    if not isinstance(started_at, datetime):
        return None

    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)

    window = timedelta(seconds=extracting_window_seconds)
    if current_time - started_at <= window:
        return None

    recovered_preview = deepcopy(dict(preview))
    recovered_extraction = deepcopy(dict(recovered_preview.get("extraction", {})))
    recovered_extraction.update(
        {
            "success": False,
            "requestFinishedAt": current_time,
            "failureCode": FAILURE_CODE_STALE_PREVIEW,
            "failureMessage": "Preview extraction exceeded the configured extracting window.",
        }
    )
    recovered_preview["status"] = "vlm-failed"
    recovered_preview["extraction"] = recovered_extraction
    recovered_preview["updatedAt"] = current_time
    return recovered_preview


# Preview State Transitions
PREVIEW_TRANSITIONS = {
    IngestionPreviewStatus.UPLOADED: [IngestionPreviewStatus.EXTRACTING, IngestionPreviewStatus.EXPIRED],
    IngestionPreviewStatus.EXTRACTING: [IngestionPreviewStatus.READY, IngestionPreviewStatus.VLM_FAILED, IngestionPreviewStatus.EXPIRED],
    IngestionPreviewStatus.READY: [IngestionPreviewStatus.CONFIRMED, IngestionPreviewStatus.EXPIRED],
    IngestionPreviewStatus.VLM_FAILED: [
        IngestionPreviewStatus.EXTRACTING,
        IngestionPreviewStatus.CONFIRMED,
        IngestionPreviewStatus.EXPIRED,
    ],
    IngestionPreviewStatus.CONFIRMED: [],
    IngestionPreviewStatus.EXPIRED: [],
}


def transition_preview_state(
    current: IngestionPreviewStatus,
    target: IngestionPreviewStatus
) -> IngestionPreviewStatus:
    valid_targets = PREVIEW_TRANSITIONS.get(current, [])
    if target not in valid_targets:
        raise InvalidStateTransitionError(
            f"Invalid transition from {current} to {target}"
        )
    return target


# Exam State Transitions
EXAM_TRANSITIONS = {
    ExamState.IN_PROGRESS: [ExamState.SUBMITTED, ExamState.DISCARDED],
    ExamState.SUBMITTED: [],
    ExamState.DISCARDED: [],
}


def transition_exam_state(
    current: ExamState,
    target: ExamState
) -> ExamState:
    valid_targets = EXAM_TRANSITIONS.get(current, [])
    if target not in valid_targets:
        raise InvalidStateTransitionError(
            f"Invalid transition from {current} to {target}"
        )
    return target
