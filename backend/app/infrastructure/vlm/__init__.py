"""VLM infrastructure namespace."""

from app.infrastructure.vlm.base_client import (
    FAILURE_CODE_INVALID_RESPONSE,
    FAILURE_CODE_NETWORK,
    FAILURE_CODE_PROVIDER,
    FAILURE_CODE_PROVIDER_REJECTED,
    FAILURE_CODE_TIMEOUT,
)
from app.infrastructure.vlm.client import (
    FAILURE_CODE_STALE_PREVIEW,
    ExtractionResult,
    GradingResult,
    VLMClient,
    VLMError,
)
from app.infrastructure.vlm.solution_coaching_client import (
    CoachingMessage,
    CoachingVLMClient,
    CoachingVLMRequest,
    CoachingVLMResult,
    SolutionCoachingVLMError,
    SolutionVLMClient,
    SolutionVLMRequest,
    SolutionVLMResult,
)

__all__ = [
    "FAILURE_CODE_INVALID_RESPONSE",
    "FAILURE_CODE_NETWORK",
    "FAILURE_CODE_PROVIDER",
    "FAILURE_CODE_PROVIDER_REJECTED",
    "FAILURE_CODE_STALE_PREVIEW",
    "FAILURE_CODE_TIMEOUT",
    "CoachingMessage",
    "CoachingVLMClient",
    "CoachingVLMRequest",
    "CoachingVLMResult",
    "ExtractionResult",
    "GradingResult",
    "SolutionCoachingVLMError",
    "SolutionVLMClient",
    "SolutionVLMRequest",
    "SolutionVLMResult",
    "VLMClient",
    "VLMError",
]
