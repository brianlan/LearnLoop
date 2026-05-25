"""LLM provider integration namespace."""

from app.infrastructure.llm.client import (
    FAILURE_CODE_INVALID_RESPONSE,
    FAILURE_CODE_NETWORK,
    FAILURE_CODE_PROVIDER,
    FAILURE_CODE_PROVIDER_REJECTED,
    FAILURE_CODE_TIMEOUT,
    CoachingLLMClient,
    CoachingLLMRequest,
    CoachingLLMResult,
    CoachingMessage,
    LLMClientError,
    SolutionLLMClient,
    SolutionLLMRequest,
    SolutionLLMResult,
)

__all__ = [
    "FAILURE_CODE_INVALID_RESPONSE",
    "FAILURE_CODE_NETWORK",
    "FAILURE_CODE_PROVIDER",
    "FAILURE_CODE_PROVIDER_REJECTED",
    "FAILURE_CODE_TIMEOUT",
    "CoachingLLMClient",
    "CoachingLLMRequest",
    "CoachingLLMResult",
    "CoachingMessage",
    "LLMClientError",
    "SolutionLLMClient",
    "SolutionLLMRequest",
    "SolutionLLMResult",
]
