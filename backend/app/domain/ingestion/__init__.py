"""Ingestion domain namespace."""

from .boxes import InvalidBoxError, validate_boxes
from .state import (
    BatchState,
    ImageState,
    ItemState,
    BATCH_TRANSITIONS,
    IMAGE_TRANSITIONS,
    ITEM_TRANSITIONS,
    BATCH_TERMINAL_STATES,
    IMAGE_TERMINAL_STATES,
    ITEM_TERMINAL_STATES,
    transition_batch_state,
    transition_image_state,
    transition_item_state,
    is_batch_terminal,
    is_image_terminal,
    is_item_terminal,
)

__all__ = [
    "BatchState",
    "ImageState",
    "ItemState",
    "BATCH_TRANSITIONS",
    "IMAGE_TRANSITIONS",
    "ITEM_TRANSITIONS",
    "BATCH_TERMINAL_STATES",
    "IMAGE_TERMINAL_STATES",
    "ITEM_TERMINAL_STATES",
    "transition_batch_state",
    "transition_image_state",
    "transition_item_state",
    "is_batch_terminal",
    "is_image_terminal",
    "is_item_terminal",
    "InvalidBoxError",
    "validate_boxes",
]
