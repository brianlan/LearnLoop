from __future__ import annotations

from typing import Any


class InvalidBoxError(ValueError):
    pass


def validate_boxes(
    boxes: list[dict[str, Any]],
    image_width: int | None,
    image_height: int | None,
) -> list[dict[str, Any]]:
    """Validate and normalize box payloads.

    Boxes must have positive width/height. If image dimensions are provided,
    every box must fit inside the image bounds.
    """
    validated: list[dict[str, Any]] = []
    for index, box in enumerate(boxes):
        try:
            x = float(box["x"])
            y = float(box["y"])
            width = float(box["width"])
            height = float(box["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidBoxError(f"Box {index} is missing numeric coordinates") from exc

        if width <= 0 or height <= 0:
            raise InvalidBoxError(f"Box {index} must have positive width and height")

        if image_width is not None and image_height is not None:
            if x < 0 or y < 0 or x + width > image_width or y + height > image_height:
                raise InvalidBoxError(f"Box {index} exceeds image bounds")

        box_id = str(box.get("boxId") or f"box-{index + 1}")
        validated.append(
            {"boxId": box_id, "x": x, "y": y, "width": width, "height": height}
        )
    return validated
