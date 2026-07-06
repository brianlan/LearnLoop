from __future__ import annotations

from dataclasses import dataclass

import pymupdf

# DPI used when rendering PDF pages to raster images.
PDF_RENDER_DPI = 150


class PdfRenderError(Exception):
    """Raised when a PDF cannot be rendered to page images."""


@dataclass(frozen=True)
class RenderedPage:
    bytes: bytes
    width: int
    height: int
    content_type: str = "image/png"


def render_pdf_pages(pdf_bytes: bytes) -> list[RenderedPage]:
    """Render each page of *pdf_bytes* into a PNG image.

    Raises :class:`PdfRenderError` when the PDF is empty, encrypted, or
    cannot be parsed.
    """
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PdfRenderError(f"Could not open PDF: {exc}") from exc

    try:
        if document.is_encrypted:
            raise PdfRenderError("PDF is encrypted")
        if document.page_count == 0:
            raise PdfRenderError("PDF has no pages")

        pages: list[RenderedPage] = []
        for page in document:
            pixmap = page.get_pixmap(dpi=PDF_RENDER_DPI)
            pages.append(
                RenderedPage(
                    bytes=pixmap.tobytes("png"),
                    width=pixmap.width,
                    height=pixmap.height,
                )
            )
        return pages
    finally:
        document.close()
