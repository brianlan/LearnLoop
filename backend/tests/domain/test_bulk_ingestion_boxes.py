from __future__ import annotations

import pytest

from app.domain.ingestion import InvalidBoxError, validate_boxes


def test_validate_boxes_accepts_valid_boxes() -> None:
    boxes = [{"x": 10, "y": 20, "width": 30, "height": 40}]
    assert validate_boxes(boxes, 100, 100) == [
        {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0}
    ]


def test_validate_boxes_rejects_zero_width() -> None:
    with pytest.raises(InvalidBoxError):
        validate_boxes([{"x": 0, "y": 0, "width": 0, "height": 10}], 100, 100)


def test_validate_boxes_rejects_zero_height() -> None:
    with pytest.raises(InvalidBoxError):
        validate_boxes([{"x": 0, "y": 0, "width": 10, "height": 0}], 100, 100)


def test_validate_boxes_rejects_negative_coordinate() -> None:
    with pytest.raises(InvalidBoxError):
        validate_boxes([{"x": -1, "y": 0, "width": 10, "height": 10}], 100, 100)


def test_validate_boxes_rejects_box_exceeding_width() -> None:
    with pytest.raises(InvalidBoxError):
        validate_boxes([{"x": 50, "y": 0, "width": 60, "height": 10}], 100, 100)


def test_validate_boxes_rejects_box_exceeding_height() -> None:
    with pytest.raises(InvalidBoxError):
        validate_boxes([{"x": 0, "y": 90, "width": 10, "height": 20}], 100, 100)


def test_validate_boxes_skips_bounds_when_dimensions_missing() -> None:
    boxes = [{"x": 0, "y": 0, "width": 10, "height": 10}]
    assert validate_boxes(boxes, None, None) == [
        {"x": 0.0, "y": 0.0, "width": 10.0, "height": 10.0}
    ]
