import pytest
from app.domain import (
    IngestionPreviewStatus,
    ExamState,
    transition_preview_state,
    transition_exam_state,
    InvalidStateTransitionError,
)


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


def test_exam_invalid_transitions():
    with pytest.raises(InvalidStateTransitionError):
        transition_exam_state(ExamState.SUBMITTED, ExamState.IN_PROGRESS)
