from __future__ import annotations


def get_image_size(image_bytes: bytes) -> tuple[int, int] | None:
    """Return (width, height) for PNG or JPEG bytes without external deps."""
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(image_bytes) >= 24:
        width = int.from_bytes(image_bytes[16:20], "big")
        height = int.from_bytes(image_bytes[20:24], "big")
        return width, height

    if image_bytes.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 < len(image_bytes):
            if image_bytes[offset] != 0xFF:
                return None
            marker = image_bytes[offset + 1]
            if marker in (0xD9, 0x00):
                offset += 2
                continue
            if marker in (0xD8,):
                offset += 2
                continue
            length = int.from_bytes(image_bytes[offset + 2 : offset + 4], "big")
            if marker in (
                0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
            ):
                if offset + 9 < len(image_bytes):
                    height = int.from_bytes(image_bytes[offset + 5 : offset + 7], "big")
                    width = int.from_bytes(image_bytes[offset + 7 : offset + 9], "big")
                    return width, height
                return None
            offset += 2 + length

    return None
