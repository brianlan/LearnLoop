from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.presentation.helpers import guess_upload_extension


def _make_upload(filename: str | None, content_type: str | None) -> UploadFile:
    headers = Headers({"content-type": content_type} if content_type else {})
    return UploadFile(file=BytesIO(b""), filename=filename, headers=headers)


def test_returns_filename_suffix_when_present() -> None:
    upload = _make_upload("photo.png", "image/png")
    assert guess_upload_extension(upload) == ".png"


def test_suffix_takes_priority_over_content_type() -> None:
    # Suffix wins even when a different content type is present.
    upload = _make_upload("image.txt", "image/png")
    assert guess_upload_extension(upload) == ".txt"


def test_preserves_filename_suffix_case() -> None:
    # The suffix is returned verbatim, including its original case.
    upload = _make_upload("photo.PNG", "image/png")
    assert guess_upload_extension(upload) == ".PNG"


def test_uses_content_type_when_filename_has_no_suffix() -> None:
    upload = _make_upload("noext", "image/png")
    assert guess_upload_extension(upload) == ".png"


def test_uses_content_type_when_filename_is_empty() -> None:
    upload = _make_upload("", "image/png")
    assert guess_upload_extension(upload) == ".png"


def test_uses_content_type_when_filename_is_none() -> None:
    upload = _make_upload(None, "image/jpeg")
    assert guess_upload_extension(upload) == ".jpg"


def test_returns_bin_when_content_type_unknown() -> None:
    upload = _make_upload(None, "totally/fake")
    assert guess_upload_extension(upload) == ".bin"


def test_returns_bin_when_no_filename_and_no_content_type() -> None:
    upload = _make_upload(None, None)
    assert guess_upload_extension(upload) == ".bin"


def test_returns_bin_when_no_suffix_and_content_type_unknown() -> None:
    upload = _make_upload("noext", "totally/fake")
    assert guess_upload_extension(upload) == ".bin"


@pytest.mark.parametrize(
    ("filename", "content_type", "expected"),
    [
        ("photo.png", "image/png", ".png"),
        ("photo.png", None, ".png"),
        (None, "image/png", ".png"),
        (None, None, ".bin"),
        ("", "totally/fake", ".bin"),
        ("image.txt", "image/png", ".txt"),
    ],
)
def test_extension_selection_order(filename: str | None, content_type: str | None, expected: str) -> None:
    # Documented order: filename suffix first, then content-type guess, then .bin fallback.
    assert guess_upload_extension(_make_upload(filename, content_type)) == expected
