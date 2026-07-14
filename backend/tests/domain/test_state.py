import copy
from datetime import UTC, datetime, timedelta

import pytest
from app.domain import (
    IngestionPreviewStatus,
    ExamState,
    transition_preview_state,
    transition_exam_state,
    InvalidStateTransitionError,
)
from app.domain.state import recover_stale_preview

# Domain-owned constant value used as the failure code for stale-preview recovery.
_STALE_PREVIEW_FAILURE_CODE = "vlm-stale-preview-timeout"


def test_preview_valid_transitions():
    assert transition_preview_state(IngestionPreviewStatus.UPLOADED, IngestionPreviewStatus.EXTRACTING) == IngestionPreviewStatus.EXTRACTING
    assert transition_preview_state(IngestionPreviewStatus.EXTRACTING, IngestionPreviewStatus.READY) == IngestionPreviewStatus.READY
    assert transition_preview_state(IngestionPreviewStatus.EXTRACTING, IngestionPreviewStatus.VLM_FAILED) == IngestionPreviewStatus.VLM_FAILED
    assert transition_preview_state(IngestionPreviewStatus.VLM_FAILED, IngestionPreviewStatus.EXTRACTING) == IngestionPreviewStatus.EXTRACTING
    assert transition_preview_state(IngestionPreviewStatus.READY, IngestionPreviewStatus.CONFIRMED) == IngestionPreviewStatus.CONFIRMED


def test_preview_invalid_transitions():
    with pytest.raises(InvalidStateTransitionError):
        transition_preview_state(IngestionPreviewStatus.UPLOADED, IngestionPreviewStatus.CONFIRMED)
    with pytest.raises(InvalidStateTransitionError):
        transition_preview_state(IngestionPreviewStatus.CONFIRMED, IngestionPreviewStatus.EXTRACTING)
    with pytest.raises(InvalidStateTransitionError):
        transition_preview_state(IngestionPreviewStatus.EXPIRED, IngestionPreviewStatus.READY)


def test_exam_valid_transitions():
    assert transition_exam_state(ExamState.IN_PROGRESS, ExamState.SUBMITTED) == ExamState.SUBMITTED
    assert transition_exam_state(ExamState.IN_PROGRESS, ExamState.GRADING) == ExamState.GRADING
    assert transition_exam_state(ExamState.IN_PROGRESS, ExamState.DISCARDED) == ExamState.DISCARDED
    assert transition_exam_state(ExamState.GRADING, ExamState.SUBMITTED) == ExamState.SUBMITTED
    assert transition_exam_state(ExamState.GRADING, ExamState.DISCARDED) == ExamState.DISCARDED


def test_exam_invalid_transitions():
    with pytest.raises(InvalidStateTransitionError):
        transition_exam_state(ExamState.SUBMITTED, ExamState.IN_PROGRESS)
    with pytest.raises(InvalidStateTransitionError):
        transition_exam_state(ExamState.SUBMITTED, ExamState.GRADING)
    with pytest.raises(InvalidStateTransitionError):
        transition_exam_state(ExamState.GRADING, ExamState.IN_PROGRESS)
    with pytest.raises(InvalidStateTransitionError):
        transition_exam_state(ExamState.DISCARDED, ExamState.SUBMITTED)
    with pytest.raises(InvalidStateTransitionError):
        transition_exam_state(ExamState.IN_PROGRESS, ExamState.IN_PROGRESS)


# ---------------------------------------------------------------------------
# Characterization tests for recover_stale_preview
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _stale_preview(**overrides) -> dict:
    """Build a preview that is well past the extracting window."""
    base = {
        "status": "extracting",
        "createdAt": _NOW - timedelta(seconds=200),
        "updatedAt": _NOW - timedelta(seconds=200),
        "extraction": {
            "requestStartedAt": _NOW - timedelta(seconds=200),
            "requestFinishedAt": None,
            "success": None,
            "failureCode": None,
            "failureMessage": None,
        },
    }
    base.update(overrides)
    return base


def test_recover_stale_preview_non_extracting_status_returns_none() -> None:
    """Non-'extracting' status previews are never recovered."""
    for status in ("uploaded", "ready", "vlm-failed", "confirmed", "expired"):
        preview = _stale_preview(status=status)
        assert recover_stale_preview(preview, now=_NOW, extracting_window_seconds=30) is None


def test_recover_stale_preview_falls_back_to_updated_at() -> None:
    """When extraction.requestStartedAt is missing, falls back to updatedAt."""
    preview = _stale_preview(extraction={})
    recovered = recover_stale_preview(preview, now=_NOW, extracting_window_seconds=30)
    assert recovered is not None
    assert recovered["status"] == "vlm-failed"


def test_recover_stale_preview_falls_back_to_created_at() -> None:
    """When requestStartedAt and updatedAt are missing, falls back to createdAt."""
    preview = _stale_preview(extraction={})
    preview["updatedAt"] = None
    recovered = recover_stale_preview(preview, now=_NOW, extracting_window_seconds=30)
    assert recovered is not None


def test_recover_stale_preview_non_datetime_started_at_returns_none() -> None:
    """When started_at resolves to a truthy non-datetime, returns None (no fallback)."""
    preview = _stale_preview(extraction={"requestStartedAt": "2024-01-01"})
    assert recover_stale_preview(preview, now=_NOW, extracting_window_seconds=30) is None


def test_recover_stale_preview_all_started_at_sources_none_returns_none() -> None:
    """When no started_at source is a datetime, returns None."""
    preview = _stale_preview(extraction={})
    preview["extraction"]["requestStartedAt"] = None
    preview["updatedAt"] = None
    preview["createdAt"] = None
    assert recover_stale_preview(preview, now=_NOW, extracting_window_seconds=30) is None


def test_recover_stale_preview_naive_datetime_gets_utc() -> None:
    """Naive datetime (no tzinfo) is treated as UTC."""
    naive_started = datetime(2026, 1, 15, 11, 57, 0)  # 200s before _NOW, no tzinfo
    preview = _stale_preview()
    preview["extraction"]["requestStartedAt"] = naive_started
    recovered = recover_stale_preview(preview, now=_NOW, extracting_window_seconds=30)
    assert recovered is not None
    assert recovered["status"] == "vlm-failed"


def test_recover_stale_preview_does_not_mutate_input() -> None:
    """The input preview mapping is never mutated."""
    preview = _stale_preview()
    original = copy.deepcopy(preview)
    recovered = recover_stale_preview(preview, now=_NOW, extracting_window_seconds=30)
    assert recovered is not None
    assert preview == original
    assert preview["status"] == "extracting"
    assert preview["extraction"]["failureCode"] is None
    assert preview["extraction"]["success"] is None


def test_recover_stale_preview_exact_failure_code() -> None:
    """Recovered preview uses the exact domain failure code."""
    recovered = recover_stale_preview(_stale_preview(), now=_NOW, extracting_window_seconds=30)
    assert recovered is not None
    assert recovered["extraction"]["failureCode"] == _STALE_PREVIEW_FAILURE_CODE


def test_recover_stale_preview_exact_recovered_shape() -> None:
    """Recovered preview has the exact expected document shape."""
    preview = _stale_preview()
    recovered = recover_stale_preview(preview, now=_NOW, extracting_window_seconds=30)
    assert recovered is not None
    # Changed fields
    assert recovered["status"] == "vlm-failed"
    assert recovered["updatedAt"] == _NOW
    # Extraction update fields
    assert recovered["extraction"]["success"] is False
    assert recovered["extraction"]["requestFinishedAt"] == _NOW
    assert recovered["extraction"]["failureCode"] == _STALE_PREVIEW_FAILURE_CODE
    assert recovered["extraction"]["failureMessage"] == "Preview extraction exceeded the configured extracting window."
    # Preserved fields
    assert recovered["createdAt"] == preview["createdAt"]
    assert recovered["extraction"]["requestStartedAt"] == preview["extraction"]["requestStartedAt"]


def test_recover_stale_preview_fresh_extraction_returns_none() -> None:
    """A preview within the extracting window is not recovered."""
    preview = _stale_preview()
    preview["extraction"]["requestStartedAt"] = _NOW - timedelta(seconds=10)
    assert recover_stale_preview(preview, now=_NOW, extracting_window_seconds=30) is None
