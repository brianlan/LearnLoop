from __future__ import annotations

import httpx
import pytest

from app.infrastructure.vlm.client import VLMClient, VLMError
from app.infrastructure.vlm.solution_coaching_client import (
    SolutionCoachingVLMError,
    SolutionVLMClient,
)


def _build_vlm_client(handler) -> VLMClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://vlm.example/api",
        timeout=5,
        headers={"Authorization": "Bearer demo", "Content-Type": "application/json"},
    )
    return VLMClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
        http_client=http_client,
    )


def _build_solution_client(handler) -> SolutionVLMClient:
    from app.infrastructure.config.settings import Settings

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://solution.example/api",
        timeout=5,
        headers={"Authorization": "Bearer solution-key", "Content-Type": "application/json"},
    )
    settings = Settings(
        math_solution_vlm_endpoint="https://solution.example/api",
        math_solution_vlm_model="solution-model",
        math_solution_vlm_api_key="solution-key",
        math_solution_vlm_timeout_seconds=7,
    )
    return SolutionVLMClient(settings=settings, http_client=http_client)


@pytest.mark.asyncio
async def test_vlm_error_and_solution_coaching_error_are_independently_catchable() -> None:
    async def vlm_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async def solution_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    vlm_client = _build_vlm_client(vlm_handler)
    solution_client = _build_solution_client(solution_handler)

    with pytest.raises(VLMError) as vlm_exc:
        await vlm_client.extract(image_url="https://example.com/img.png")
    assert not isinstance(vlm_exc.value, SolutionCoachingVLMError)
    await vlm_client.aclose()

    from app.infrastructure.vlm.solution_coaching_client import SolutionVLMRequest

    with pytest.raises(SolutionCoachingVLMError) as solution_exc:
        await solution_client.generate_solution(
            SolutionVLMRequest(problem_text="test", correct_answer="1")
        )
    assert not isinstance(solution_exc.value, VLMError)
    await solution_client.aclose()


@pytest.mark.asyncio
async def test_vlm_error_is_not_solution_coaching_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _build_vlm_client(handler)
    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="https://example.com/img.png")
    await client.aclose()

    assert not isinstance(exc_info.value, SolutionCoachingVLMError)


@pytest.mark.asyncio
async def test_solution_coaching_error_is_not_vlm_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _build_solution_client(handler)
    from app.infrastructure.vlm.solution_coaching_client import SolutionVLMRequest

    with pytest.raises(SolutionCoachingVLMError) as exc_info:
        await client.generate_solution(
            SolutionVLMRequest(problem_text="test", correct_answer="1")
        )
    await client.aclose()

    assert not isinstance(exc_info.value, VLMError)
