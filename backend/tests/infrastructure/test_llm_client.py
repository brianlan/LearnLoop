from __future__ import annotations

import json

import httpx
import pytest

from app.infrastructure.config.settings import Settings
from app.infrastructure.llm.client import (
    FAILURE_CODE_INVALID_RESPONSE,
    FAILURE_CODE_NETWORK,
    FAILURE_CODE_PROVIDER,
    CoachingLLMClient,
    CoachingLLMRequest,
    CoachingMessage,
    LLMClientError,
    SolutionLLMClient,
    SolutionLLMRequest,
)
from app.infrastructure.llm.prompts import COACHING_PROMPT_VERSION, SOLUTION_PROMPT_VERSION


def _build_solution_client(handler) -> SolutionLLMClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://solution.example/api",
        timeout=5,
        headers={"Authorization": "Bearer solution-key", "Content-Type": "application/json"},
    )
    settings = Settings(
        solution_llm_endpoint="https://solution.example/api",
        solution_llm_model="solution-model",
        solution_llm_api_key="solution-key",
        solution_llm_timeout_seconds=7,
    )
    return SolutionLLMClient(settings=settings, http_client=http_client)


def _build_coaching_client(handler) -> CoachingLLMClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://coaching.example/api",
        timeout=5,
        headers={"Authorization": "Bearer coaching-key", "Content-Type": "application/json"},
    )
    settings = Settings(
        coaching_llm_endpoint="https://coaching.example/api",
        coaching_llm_model="coaching-model",
        coaching_llm_api_key="coaching-key",
        coaching_llm_timeout_seconds=9,
    )
    return CoachingLLMClient(settings=settings, http_client=http_client)


def test_llm_clients_use_capability_specific_timeouts() -> None:
    solution_client = SolutionLLMClient(
        settings=Settings(solution_llm_timeout_seconds=123),
        http_client=httpx.AsyncClient(),
    )
    coaching_client = CoachingLLMClient(
        settings=Settings(coaching_llm_timeout_seconds=45),
        http_client=httpx.AsyncClient(),
    )

    assert solution_client._timeout_seconds == 123
    assert coaching_client._timeout_seconds == 45


@pytest.mark.asyncio
async def test_solution_llm_client_builds_policy_prompt_and_uses_solution_config() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat/completions"
        assert request.headers["Authorization"] == "Bearer solution-key"
        payload = json.loads(await request.aread())
        assert payload["model"] == "solution-model"
        prompt = payload["messages"][0]["content"][0]["text"]
        assert "written in Simplified Chinese" in prompt
        assert "Do not use advanced or out-of-scope methods" in prompt
        assert "the answer key may be only one valid wording or format" in prompt
        assert "Return valid JSON only" in prompt
        assert "已知 x + 3 = 5" in prompt
        assert "2" in prompt
        assert payload["messages"][0]["content"][1]["image_url"]["url"] == "https://example.com/problem.png"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "steps_markdown": "1. 两边同时减 3。\n2. 得到 x = 2。",
                                    "final_answer": "x = 2",
                                    "math_level_classification": "middle-school",
                                }
                            ),
                        },
                    }
                ]
            },
        )

    client = _build_solution_client(handler)
    result = await client.generate_solution(
        SolutionLLMRequest(
            problem_text="已知 x + 3 = 5，求 x。",
            correct_answer="2",
            image_url="https://example.com/problem.png",
        )
    )
    await client.aclose()

    assert result.prompt_version == SOLUTION_PROMPT_VERSION
    assert result.model == "solution-model"
    assert result.final_answer == "x = 2"
    assert result.math_level_classification == "middle-school"


@pytest.mark.asyncio
async def test_solution_llm_client_accepts_fenced_json_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "```json\n{\"steps_markdown\":\"步骤\",\"final_answer\":\"42\",\"math_level_classification\":\"primary\"}\n```",
                        },
                    }
                ]
            },
        )

    client = _build_solution_client(handler)
    result = await client.generate_solution(
        SolutionLLMRequest(problem_text="题目", correct_answer="42", image_url="https://example.com/problem.png")
    )
    await client.aclose()

    assert result.steps_markdown == "步骤"
    assert result.final_answer == "42"


@pytest.mark.asyncio
async def test_solution_llm_client_rejects_malformed_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant", "content": "not-json"}}]},
        )

    client = _build_solution_client(handler)

    with pytest.raises(LLMClientError) as exc_info:
        await client.generate_solution(
            SolutionLLMRequest(problem_text="题目", correct_answer="42", image_url="https://example.com/problem.png")
        )
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_solution_llm_client_classifies_provider_failure_as_retryable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "overloaded"})

    client = _build_solution_client(handler)

    with pytest.raises(LLMClientError) as exc_info:
        await client.generate_solution(
            SolutionLLMRequest(problem_text="题目", correct_answer="42", image_url="https://example.com/problem.png")
        )
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_PROVIDER
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_coaching_llm_client_builds_context_prompt_and_uses_coaching_config() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat/completions"
        assert request.headers["Authorization"] == "Bearer coaching-key"
        payload = json.loads(await request.aread())
        assert payload["model"] == "coaching-model"
        prompt = payload["messages"][0]["content"]
        assert "Write this student-facing tutoring reply in Simplified Chinese" in prompt
        assert "Be warm, encouraging, and patient" in prompt
        assert "Canonical solution steps" in prompt
        assert "board.create('text', [x, y, 'label'], {anchorX:'middle', fontSize:12})" in prompt
        assert "Never write `board.create('text', [x, y, 'label', {options}])`" in prompt
        assert "student: 我想先看第一步" in prompt
        assert "coach: 先看已知条件" in prompt
        assert "tracking" not in prompt
        assert "exposureCount" not in prompt
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"text": "先看等式两边同时减 3。"}),
                        },
                    }
                ]
            },
        )

    client = _build_coaching_client(handler)
    result = await client.send_message(
        CoachingLLMRequest(
            problem_text="已知 x + 3 = 5，求 x。",
            correct_answer="2",
            canonical_steps_markdown="1. 两边同时减 3。\n2. 得到 x = 2。",
            canonical_final_answer="x = 2",
            math_level_classification="middle-school",
            conversation_history=[
                CoachingMessage(role="student", text="我想先看第一步"),
                CoachingMessage(role="coach", text="先看已知条件"),
            ],
            new_message="可以给我一个提示吗？",
        )
    )
    await client.aclose()

    assert result.prompt_version == COACHING_PROMPT_VERSION
    assert result.model == "coaching-model"
    assert result.text == "先看等式两边同时减 3。"


@pytest.mark.asyncio
async def test_coaching_llm_client_parses_optional_whiteboard_dsl() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {"text": "看图示。", "whiteboard_dsl": "board.create('point', [0, 0]);"}
                            ),
                        },
                    }
                ]
            },
        )

    client = _build_coaching_client(handler)
    result = await client.send_message(
        CoachingLLMRequest(
            problem_text="题目",
            correct_answer="答案",
            canonical_steps_markdown="步骤",
            canonical_final_answer="答案",
            math_level_classification="primary",
            new_message="请画图",
        )
    )
    await client.aclose()

    assert result.whiteboard_dsl == "board.create('point', [0, 0]);"


@pytest.mark.asyncio
async def test_coaching_llm_client_extracts_json_from_wrapped_markdown() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "下面是回复：\n```json\n{\"text\":\"先想想已知条件。\",\"whiteboard_dsl\":null}\n```",
                        },
                    }
                ]
            },
        )

    client = _build_coaching_client(handler)
    result = await client.send_message(
        CoachingLLMRequest(
            problem_text="题目",
            correct_answer="答案",
            canonical_steps_markdown="步骤",
            canonical_final_answer="答案",
            math_level_classification="primary",
            new_message="请提示一下",
        )
    )
    await client.aclose()

    assert result.text == "先想想已知条件。"
    assert result.whiteboard_dsl is None


@pytest.mark.asyncio
async def test_coaching_llm_client_network_failure_is_catchable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _build_coaching_client(handler)

    with pytest.raises(LLMClientError) as exc_info:
        await client.send_message(
            CoachingLLMRequest(
                problem_text="题目",
                correct_answer="答案",
                canonical_steps_markdown="步骤",
                canonical_final_answer="答案",
                math_level_classification="primary",
                new_message="你好",
            )
        )
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_NETWORK
    assert exc_info.value.retryable is True
