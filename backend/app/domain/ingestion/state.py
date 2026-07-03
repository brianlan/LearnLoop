from __future__ import annotations

from enum import Enum

from app.domain.state import InvalidStateTransitionError


class BatchState(str, Enum):
    """Lifecycle states for a server-backed bulk ingestion batch."""

    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    DELETED = "deleted"


class ImageState(str, Enum):
    """Lifecycle states for an image inside a bulk ingestion batch."""

    UPLOADED = "uploaded"
    DETECTING = "detecting"
    DETECT_FAILED = "detect-failed"
    READY = "ready"
    COMMITTED = "committed"
    DELETED = "deleted"


class ItemState(str, Enum):
    """Lifecycle states for an extraction item inside a bulk ingestion batch."""

    QUEUED = "queued"
    EXTRACTING = "extracting"
    READY = "ready"
    FAILED = "failed"
    SUBMIT_FAILED = "submit-failed"
    DELETED = "deleted"
    SUBMITTED = "submitted"


BATCH_TRANSITIONS: dict[BatchState, list[BatchState]] = {
    BatchState.ACTIVE: [BatchState.COMPLETED, BatchState.EXPIRED, BatchState.DELETED],
    BatchState.COMPLETED: [],
    BatchState.EXPIRED: [],
    BatchState.DELETED: [],
}

IMAGE_TRANSITIONS: dict[ImageState, list[ImageState]] = {
    ImageState.UPLOADED: [ImageState.DETECTING, ImageState.DELETED],
    ImageState.DETECTING: [ImageState.READY, ImageState.DETECT_FAILED],
    ImageState.DETECT_FAILED: [ImageState.DETECTING, ImageState.READY, ImageState.DELETED],
    ImageState.READY: [ImageState.COMMITTED, ImageState.DELETED],
    ImageState.COMMITTED: [ImageState.DELETED],
    ImageState.DELETED: [],
}

ITEM_TRANSITIONS: dict[ItemState, list[ItemState]] = {
    ItemState.QUEUED: [ItemState.EXTRACTING, ItemState.DELETED],
    ItemState.EXTRACTING: [ItemState.READY, ItemState.FAILED],
    ItemState.FAILED: [ItemState.QUEUED, ItemState.DELETED],
    ItemState.READY: [ItemState.SUBMITTED, ItemState.SUBMIT_FAILED, ItemState.DELETED],
    ItemState.SUBMIT_FAILED: [ItemState.QUEUED, ItemState.DELETED],
    ItemState.DELETED: [],
    ItemState.SUBMITTED: [],
}

BATCH_TERMINAL_STATES = {BatchState.COMPLETED, BatchState.EXPIRED, BatchState.DELETED}
IMAGE_TERMINAL_STATES = {ImageState.DELETED}
ITEM_TERMINAL_STATES = {ItemState.DELETED, ItemState.SUBMITTED}


def transition_batch_state(current: BatchState, target: BatchState) -> BatchState:
    if target not in BATCH_TRANSITIONS.get(current, []):
        raise InvalidStateTransitionError(f"Invalid batch transition from {current} to {target}")
    return target


def transition_image_state(current: ImageState, target: ImageState) -> ImageState:
    if target not in IMAGE_TRANSITIONS.get(current, []):
        raise InvalidStateTransitionError(f"Invalid image transition from {current} to {target}")
    return target


def transition_item_state(current: ItemState, target: ItemState) -> ItemState:
    if target not in ITEM_TRANSITIONS.get(current, []):
        raise InvalidStateTransitionError(f"Invalid item transition from {current} to {target}")
    return target


def is_batch_terminal(state: BatchState) -> bool:
    return state in BATCH_TERMINAL_STATES


def is_image_terminal(state: ImageState) -> bool:
    return state in IMAGE_TERMINAL_STATES


def is_item_terminal(state: ItemState) -> bool:
    return state in ITEM_TERMINAL_STATES
