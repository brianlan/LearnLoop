from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from litellm.exceptions import APIConnectionError

from app.infrastructure.vlm.client import VLMClient, VLMError
from app.infrastructure.vlm.solution_coaching_client import (
    SolutionCoachingVLMError,
    SolutionVLMClient,
)


def _network_completion(**kwargs: Any) -> Any:
    raise APIConnectionError(message="boom", model="demo", llm_provider="openai")


def _build_vlm_client(completion_fn: Callable[..., Any]) -> VLMClient:
    return VLMClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
        completion_fn=completion_fn,
    )


def _build_solution_client(completion_fn: Callable[..., Any]) -> SolutionVLMClient:
    from app.infrastructure.config.settings import Settings

    settings = Settings(
        math_solution_vlm_endpoint="https://solution.example/api",
        math_solution_vlm_model="solution-model",
        math_solution_vlm_api_key="solution-key",
        math_solution_vlm_timeout_seconds=7,
    )
    return SolutionVLMClient(settings=settings, completion_fn=completion_fn)


@pytest.mark.asyncio
async def test_vlm_error_and_solution_coaching_error_are_independently_catchable() -> None:
    vlm_client = _build_vlm_client(_network_completion)
    solution_client = _build_solution_client(_network_completion)

    with pytest.raises(VLMError) as vlm_exc:
        await vlm_client.extract(image_url="https://example.com/img.png")
    assert not isinstance(vlm_exc.value, SolutionCoachingVLMError)

    from app.infrastructure.vlm.solution_coaching_client import SolutionVLMRequest

    with pytest.raises(SolutionCoachingVLMError) as solution_exc:
        await solution_client.generate_solution(
            SolutionVLMRequest(problem_text="test", correct_answer="1")
        )
    assert not isinstance(solution_exc.value, VLMError)


@pytest.mark.asyncio
async def test_vlm_error_is_not_solution_coaching_error() -> None:
    client = _build_vlm_client(_network_completion)
    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="https://example.com/img.png")

    assert not isinstance(exc_info.value, SolutionCoachingVLMError)


@pytest.mark.asyncio
async def test_solution_coaching_error_is_not_vlm_error() -> None:
    client = _build_solution_client(_network_completion)
    from app.infrastructure.vlm.solution_coaching_client import SolutionVLMRequest

    with pytest.raises(SolutionCoachingVLMError) as exc_info:
        await client.generate_solution(
            SolutionVLMRequest(problem_text="test", correct_answer="1")
        )

    assert not isinstance(exc_info.value, VLMError)
