from __future__ import annotations

import io
from typing import Any

from PIL import Image


def _content_type_from_format(image_format: str | None) -> str:
    if image_format == "JPEG":
        return "image/jpeg"
    if image_format == "PNG":
        return "image/png"
    return "image/png"


def crop_image_to_box(
    image_bytes: bytes,
    box: dict[str, Any],
) -> tuple[bytes, str, int, int]:
    """Crop a region from image bytes and return the crop bytes, content type, width, height."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        image_format = img.format
        img_width, img_height = img.size

        x = max(0, int(box["x"]))
        y = max(0, int(box["y"]))
        right = min(img_width, int(box["x"] + box["width"]))
        bottom = min(img_height, int(box["y"] + box["height"]))

        if right <= x or bottom <= y:
            raise ValueError("Crop box has no area after clamping")

        cropped = img.crop((x, y, right, bottom))
        content_type = _content_type_from_format(image_format)

        output_format = image_format if image_format in {"PNG", "JPEG"} else "PNG"
        buffer = io.BytesIO()
        cropped.save(buffer, format=output_format)
        return buffer.getvalue(), content_type, cropped.width, cropped.height
