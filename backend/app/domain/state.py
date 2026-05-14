from .models import IngestionPreviewStatus, ExamState


class InvalidStateTransitionError(Exception):
    pass


# Preview State Transitions
PREVIEW_TRANSITIONS = {
    IngestionPreviewStatus.UPLOADED: [IngestionPreviewStatus.EXTRACTING, IngestionPreviewStatus.EXPIRED],
    IngestionPreviewStatus.EXTRACTING: [IngestionPreviewStatus.READY, IngestionPreviewStatus.VLM_FAILED, IngestionPreviewStatus.EXPIRED],
    IngestionPreviewStatus.READY: [IngestionPreviewStatus.CONFIRMED, IngestionPreviewStatus.EXPIRED],
    IngestionPreviewStatus.VLM_FAILED: [IngestionPreviewStatus.EXTRACTING, IngestionPreviewStatus.EXPIRED],
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
    ExamState.IN_PROGRESS: [ExamState.SUBMITTED],
    ExamState.SUBMITTED: [],
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
