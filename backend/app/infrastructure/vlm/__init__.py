"""VLM infrastructure namespace."""

from app.infrastructure.vlm.client import (
    FAILURE_CODE_INVALID_RESPONSE,
    FAILURE_CODE_STALE_PREVIEW,
    FAILURE_CODE_TIMEOUT,
    ExtractionResult,
    GradingResult,
    VLMClient,
    VLMError,
    recover_stale_preview,
)

__all__ = [
    "FAILURE_CODE_INVALID_RESPONSE",
    "FAILURE_CODE_STALE_PREVIEW",
    "FAILURE_CODE_TIMEOUT",
    "ExtractionResult",
    "GradingResult",
    "VLMClient",
    "VLMError",
    "recover_stale_preview",
]
