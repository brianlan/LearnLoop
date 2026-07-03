import pytest

from app.domain.ingestion import (
    BatchState,
    ImageState,
    ItemState,
    transition_batch_state,
    transition_image_state,
    transition_item_state,
    is_batch_terminal,
    is_image_terminal,
    is_item_terminal,
)
from app.domain.state import InvalidStateTransitionError


class TestBatchStateTransitions:
    def test_active_to_completed(self):
        assert transition_batch_state(BatchState.ACTIVE, BatchState.COMPLETED) == BatchState.COMPLETED

    def test_active_to_expired(self):
        assert transition_batch_state(BatchState.ACTIVE, BatchState.EXPIRED) == BatchState.EXPIRED

    def test_active_to_deleted(self):
        assert transition_batch_state(BatchState.ACTIVE, BatchState.DELETED) == BatchState.DELETED

    def test_terminal_batch_states_reject_transitions(self):
        for source in (BatchState.COMPLETED, BatchState.EXPIRED, BatchState.DELETED):
            for target in BatchState:
                with pytest.raises(InvalidStateTransitionError):
                    transition_batch_state(source, target)

    def test_batch_terminal_states(self):
        assert is_batch_terminal(BatchState.COMPLETED) is True
        assert is_batch_terminal(BatchState.EXPIRED) is True
        assert is_batch_terminal(BatchState.DELETED) is True
        assert is_batch_terminal(BatchState.ACTIVE) is False


class TestImageStateTransitions:
    def test_uploaded_to_detecting(self):
        assert transition_image_state(ImageState.UPLOADED, ImageState.DETECTING) == ImageState.DETECTING

    def test_detecting_to_ready(self):
        assert transition_image_state(ImageState.DETECTING, ImageState.READY) == ImageState.READY

    def test_detecting_to_detect_failed(self):
        assert transition_image_state(ImageState.DETECTING, ImageState.DETECT_FAILED) == ImageState.DETECT_FAILED

    def test_detect_failed_to_detecting(self):
        assert transition_image_state(ImageState.DETECT_FAILED, ImageState.DETECTING) == ImageState.DETECTING

    def test_detect_failed_to_ready(self):
        assert transition_image_state(ImageState.DETECT_FAILED, ImageState.READY) == ImageState.READY

    def test_ready_to_committed(self):
        assert transition_image_state(ImageState.READY, ImageState.COMMITTED) == ImageState.COMMITTED

    def test_committed_to_deleted(self):
        assert transition_image_state(ImageState.COMMITTED, ImageState.DELETED) == ImageState.DELETED

    def test_invalid_image_transitions(self):
        with pytest.raises(InvalidStateTransitionError):
            transition_image_state(ImageState.UPLOADED, ImageState.READY)
        with pytest.raises(InvalidStateTransitionError):
            transition_image_state(ImageState.READY, ImageState.UPLOADED)
        with pytest.raises(InvalidStateTransitionError):
            transition_image_state(ImageState.DELETED, ImageState.UPLOADED)

    def test_image_terminal_states(self):
        assert is_image_terminal(ImageState.DELETED) is True
        assert is_image_terminal(ImageState.UPLOADED) is False


class TestItemStateTransitions:
    def test_queued_to_extracting(self):
        assert transition_item_state(ItemState.QUEUED, ItemState.EXTRACTING) == ItemState.EXTRACTING

    def test_extracting_to_ready(self):
        assert transition_item_state(ItemState.EXTRACTING, ItemState.READY) == ItemState.READY

    def test_extracting_to_failed(self):
        assert transition_item_state(ItemState.EXTRACTING, ItemState.FAILED) == ItemState.FAILED

    def test_failed_to_queued(self):
        assert transition_item_state(ItemState.FAILED, ItemState.QUEUED) == ItemState.QUEUED

    def test_ready_to_submitted(self):
        assert transition_item_state(ItemState.READY, ItemState.SUBMITTED) == ItemState.SUBMITTED

    def test_ready_to_submit_failed(self):
        assert transition_item_state(ItemState.READY, ItemState.SUBMIT_FAILED) == ItemState.SUBMIT_FAILED

    def test_submit_failed_to_queued(self):
        assert transition_item_state(ItemState.SUBMIT_FAILED, ItemState.QUEUED) == ItemState.QUEUED

    def test_deleted_item_is_terminal(self):
        assert transition_item_state(ItemState.READY, ItemState.DELETED) == ItemState.DELETED
        for source in (ItemState.DELETED, ItemState.SUBMITTED):
            for target in ItemState:
                with pytest.raises(InvalidStateTransitionError):
                    transition_item_state(source, target)

    def test_invalid_item_transitions(self):
        with pytest.raises(InvalidStateTransitionError):
            transition_item_state(ItemState.QUEUED, ItemState.SUBMITTED)
        with pytest.raises(InvalidStateTransitionError):
            transition_item_state(ItemState.SUBMITTED, ItemState.QUEUED)

    def test_item_terminal_states(self):
        assert is_item_terminal(ItemState.SUBMITTED) is True
        assert is_item_terminal(ItemState.DELETED) is True
        assert is_item_terminal(ItemState.READY) is False
        assert is_item_terminal(ItemState.SUBMIT_FAILED) is False
