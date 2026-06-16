from __future__ import annotations

import json

import httpx
import pytest

from app.infrastructure.config.settings import Settings
from app.infrastructure.vlm.solution_coaching_client import (
    FAILURE_CODE_INVALID_RESPONSE,
    FAILURE_CODE_NETWORK,
    FAILURE_CODE_PROVIDER,
    CoachingVLMClient,
    CoachingVLMRequest,
    CoachingMessage,
    SolutionCoachingVLMError,
    SolutionVLMClient,
    SolutionVLMRequest,
)
from app.infrastructure.vlm.solution_coaching_prompts import (
    ENGLISH_COACHING_SYSTEM_PROMPT,
    MATH_COACHING_SYSTEM_PROMPT,
)


def _build_solution_client(handler) -> SolutionVLMClient:
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


def _build_coaching_client(handler) -> CoachingVLMClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://coaching.example/api",
        timeout=5,
        headers={"Authorization": "Bearer coaching-key", "Content-Type": "application/json"},
    )
    settings = Settings(
        math_coaching_vlm_endpoint="https://coaching.example/api",
        math_coaching_vlm_model="coaching-model",
        math_coaching_vlm_api_key="coaching-key",
        math_coaching_vlm_timeout_seconds=9,
    )
    return CoachingVLMClient(settings=settings, http_client=http_client)


def test_solution_coaching_vlm_clients_use_capability_specific_timeouts() -> None:
    solution_client = SolutionVLMClient(
        settings=Settings(math_solution_vlm_timeout_seconds=123),
        http_client=httpx.AsyncClient(),
    )
    coaching_client = CoachingVLMClient(
        settings=Settings(math_coaching_vlm_timeout_seconds=45),
        http_client=httpx.AsyncClient(),
    )

    assert solution_client._timeout_seconds == 123
    assert coaching_client._timeout_seconds == 45


def test_solution_vlm_client_selects_english_settings_and_prompt() -> None:
    settings = Settings(
        english_solution_vlm_endpoint="https://english-solution.example/api",
        english_solution_vlm_model="english-model",
        english_solution_vlm_api_key="english-key",
        english_solution_vlm_timeout_seconds=99,
    )
    client = SolutionVLMClient(settings=settings, subject="english", http_client=httpx.AsyncClient())

    assert client._endpoint == "https://english-solution.example/api"
    assert client._model == "english-model"
    assert client._api_key == "english-key"
    assert client._timeout_seconds == 99
    assert client._subject == "english"

    payload = client._build_payload(
        user_prompt="test",
        image_url=None,
        image_base64=None,
    )
    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    assert "English" in messages[0]["content"]


@pytest.mark.asyncio
async def test_coaching_vlm_client_selects_english_settings_and_prompt() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(await request.aread())
        assert payload["model"] == "english-model"
        assert payload["messages"][0]["role"] == "system"
        assert "English" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps({"text": "hi"})}}]},
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://english-coaching.example/api",
        timeout=5,
    )
    settings = Settings(
        english_coaching_vlm_endpoint="https://english-coaching.example/api",
        english_coaching_vlm_model="english-model",
        english_coaching_vlm_api_key="english-key",
        english_coaching_vlm_timeout_seconds=88,
    )
    client = CoachingVLMClient(settings=settings, subject="english", http_client=http_client)

    assert client._endpoint == "https://english-coaching.example/api"
    assert client._model == "english-model"
    assert client._api_key == "english-key"
    assert client._timeout_seconds == 88
    assert client._subject == "english"

    result = await client.send_message(
        CoachingVLMRequest(
            problem_text="text",
            correct_answer="ans",
            canonical_steps_markdown="steps",
            canonical_final_answer="ans",
            level_classification="basic",
            new_message="hello",
        )
    )
    assert result.text == "hi"
    await client.aclose()


@pytest.mark.asyncio
async def test_solution_vlm_client_builds_policy_prompt_and_uses_solution_config() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat/completions"
        assert request.headers["Authorization"] == "Bearer solution-key"
        payload = json.loads(await request.aread())
        assert payload["model"] == "solution-model"
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        system_prompt = payload["messages"][0]["content"]
        user_content = payload["messages"][1]["content"]
        user_prompt = user_content[0]["text"]
        assert "written in Simplified Chinese" in system_prompt
        assert "Do not use advanced or out-of-scope methods" in system_prompt
        assert "the answer key may be only one valid wording or format" in system_prompt
        assert "Return valid JSON only" in system_prompt
        assert "已知 x + 3 = 5" in user_prompt
        assert '"answerKey": "2"' in user_prompt
        assert payload["messages"][1]["content"][1]["image_url"]["url"] == "https://example.com/problem.png"
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
                                    "level_classification": "middle-school",
                                }
                            ),
                        },
                    }
                ]
            },
        )

    client = _build_solution_client(handler)
    result = await client.generate_solution(
        SolutionVLMRequest(
            problem_text="已知 x + 3 = 5，求 x。",
            correct_answer="2",
            image_url="https://example.com/problem.png",
        )
    )
    await client.aclose()

    assert result.model == "solution-model"
    assert result.final_answer == "x = 2"
    assert result.level_classification == "middle-school"


@pytest.mark.asyncio
async def test_solution_vlm_client_accepts_fenced_json_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "```json\n{\"steps_markdown\":\"步骤\",\"final_answer\":\"42\",\"level_classification\":\"primary\"}\n```",
                        },
                    }
                ]
            },
        )

    client = _build_solution_client(handler)
    result = await client.generate_solution(
        SolutionVLMRequest(problem_text="题目", correct_answer="42", image_url="https://example.com/problem.png")
    )
    await client.aclose()

    assert result.steps_markdown == "步骤"
    assert result.final_answer == "42"


@pytest.mark.asyncio
async def test_solution_vlm_client_rejects_malformed_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant", "content": "not-json"}}]},
        )

    client = _build_solution_client(handler)

    with pytest.raises(SolutionCoachingVLMError) as exc_info:
        await client.generate_solution(
            SolutionVLMRequest(problem_text="题目", correct_answer="42", image_url="https://example.com/problem.png")
        )
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_solution_vlm_client_classifies_provider_failure_as_retryable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "overloaded"})

    client = _build_solution_client(handler)

    with pytest.raises(SolutionCoachingVLMError) as exc_info:
        await client.generate_solution(
            SolutionVLMRequest(problem_text="题目", correct_answer="42", image_url="https://example.com/problem.png")
        )
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_PROVIDER
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_coaching_vlm_client_builds_context_prompt_and_uses_coaching_config() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat/completions"
        assert request.headers["Authorization"] == "Bearer coaching-key"
        payload = json.loads(await request.aread())
        assert payload["model"] == "coaching-model"
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        system_prompt = payload["messages"][0]["content"]
        user_prompt = payload["messages"][1]["content"]
        assert "Write this student-facing tutoring reply in Simplified Chinese" in system_prompt
        assert "Be warm, encouraging, and patient" in system_prompt
        assert "canonicalSolutionSteps" in user_prompt
        assert "board.create('text', [x, y, 'label'], {anchorX:'middle', fontSize:12})" in system_prompt
        assert "Never write `board.create('text', [x, y, 'label', {options}])`" in system_prompt
        assert "student: 我想先看第一步" in user_prompt
        assert "coach: 先看已知条件" in user_prompt
        assert "可以给我一个提示吗？" in user_prompt
        assert "可以给我一个提示吗？" not in system_prompt
        assert "tracking" not in user_prompt
        assert "exposureCount" not in user_prompt
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
        CoachingVLMRequest(
            problem_text="已知 x + 3 = 5，求 x。",
            correct_answer="2",
            canonical_steps_markdown="1. 两边同时减 3。\n2. 得到 x = 2。",
            canonical_final_answer="x = 2",
            level_classification="middle-school",
            conversation_history=[
                CoachingMessage(role="student", text="我想先看第一步"),
                CoachingMessage(role="coach", text="先看已知条件"),
            ],
            new_message="可以给我一个提示吗？",
        )
    )
    await client.aclose()

    assert result.model == "coaching-model"
    assert result.text == "先看等式两边同时减 3。"


@pytest.mark.asyncio
async def test_coaching_vlm_client_parses_optional_whiteboard_dsl() -> None:
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
        CoachingVLMRequest(
            problem_text="题目",
            correct_answer="答案",
            canonical_steps_markdown="步骤",
            canonical_final_answer="答案",
            level_classification="primary",
            new_message="请画图",
        )
    )
    await client.aclose()

    assert result.whiteboard_dsl == "board.create('point', [0, 0]);"


@pytest.mark.asyncio
async def test_coaching_vlm_client_preserves_allowed_whiteboard_dsl() -> None:
    allowed_dsl = (
        "board.setBoundingBox([-1, 2, 6, -2]);"
        "var A = board.create('point', [0, 0], {name:'A'});"
        "var B = board.create('point', [5, 0], {name:'B'});"
        "board.create('segment', [A, B], {strokeWidth:2});"
        "board.create('text', [2.5, 0.3, '490米'], {anchorX:'middle', fontSize:12});"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"text": "看图示。", "whiteboard_dsl": allowed_dsl}),
                        },
                    }
                ]
            },
        )

    client = _build_coaching_client(handler)
    result = await client.send_message(
        CoachingVLMRequest(
            problem_text="题目",
            correct_answer="答案",
            canonical_steps_markdown="步骤",
            canonical_final_answer="答案",
            level_classification="primary",
            new_message="请画图",
        )
    )
    await client.aclose()

    assert result.whiteboard_dsl == allowed_dsl


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_dsl",
    [
        "fetch('/api/private'); board.create('point', [0, 0]);",
        "while (true) { board.create('point', [0, 0]); }",
        "window.location = 'https://example.com';",
        "new Function('return document.cookie')();",
        "board.constructor.constructor('return window')();",
        "board.create('functiongraph', [function(x) { return x; }, -1, 1]);",
        "board.create('point', [1 + 2, 0]);",
    ],
)
async def test_coaching_vlm_client_drops_unsafe_whiteboard_dsl(unsafe_dsl: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"text": "看图示。", "whiteboard_dsl": unsafe_dsl}),
                        },
                    }
                ]
            },
        )

    client = _build_coaching_client(handler)
    result = await client.send_message(
        CoachingVLMRequest(
            problem_text="题目",
            correct_answer="答案",
            canonical_steps_markdown="步骤",
            canonical_final_answer="答案",
            level_classification="primary",
            new_message="请画图",
        )
    )
    await client.aclose()

    assert result.whiteboard_dsl is None


def test_coaching_prompts_exclude_functiongraph_and_disallow_dynamic_javascript() -> None:
    for prompt in [MATH_COACHING_SYSTEM_PROMPT, ENGLISH_COACHING_SYSTEM_PROMPT]:
        assert "functiongraph" not in prompt
        assert "Do not use loops, functions, conditionals, arithmetic expressions" in prompt


@pytest.mark.asyncio
async def test_coaching_vlm_client_extracts_json_from_wrapped_markdown() -> None:
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
        CoachingVLMRequest(
            problem_text="题目",
            correct_answer="答案",
            canonical_steps_markdown="步骤",
            canonical_final_answer="答案",
            level_classification="primary",
            new_message="请提示一下",
        )
    )
    await client.aclose()

    assert result.text == "先想想已知条件。"
    assert result.whiteboard_dsl is None


@pytest.mark.asyncio
async def test_coaching_vlm_client_network_failure_is_catchable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _build_coaching_client(handler)

    with pytest.raises(SolutionCoachingVLMError) as exc_info:
        await client.send_message(
            CoachingVLMRequest(
                problem_text="题目",
                correct_answer="答案",
                canonical_steps_markdown="步骤",
                canonical_final_answer="答案",
                level_classification="primary",
                new_message="你好",
            )
        )
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_NETWORK
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_coaching_vlm_client_parses_reasoning_content() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"text": "答案是 x=2。"}),
                            "reasoning_content": "先分析等式，两边减3得到x=2。",
                        },
                    }
                ]
            },
        )

    client = _build_coaching_client(handler)
    result = await client.send_message(
        CoachingVLMRequest(
            problem_text="x + 3 = 5",
            correct_answer="2",
            canonical_steps_markdown="步骤",
            canonical_final_answer="2",
            level_classification="primary",
            new_message="怎么做？",
        )
    )
    await client.aclose()

    assert result.text == "答案是 x=2。"
    assert result.reasoning_content == "先分析等式，两边减3得到x=2。"


@pytest.mark.asyncio
async def test_coaching_vlm_client_reasoning_content_none_when_absent() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"text": "简单回答。"}),
                        },
                    }
                ]
            },
        )

    client = _build_coaching_client(handler)
    result = await client.send_message(
        CoachingVLMRequest(
            problem_text="题目",
            correct_answer="答案",
            canonical_steps_markdown="步骤",
            canonical_final_answer="答案",
            level_classification="primary",
            new_message="你好",
        )
    )
    await client.aclose()

    assert result.text == "简单回答。"
    assert result.reasoning_content is None


def test_solution_coaching_strip_json_code_fences_leaves_plain_json_unchanged() -> None:
    plain = '{"text":"hello"}'

    assert SolutionVLMClient._strip_json_code_fences(plain) == plain


def test_solution_coaching_strip_json_code_fences_strips_json_fence() -> None:
    fenced = '```json\n{"text":"hello"}\n```'

    assert SolutionVLMClient._strip_json_code_fences(fenced) == '{"text":"hello"}'


def test_solution_coaching_strip_json_code_fences_strips_plain_fence() -> None:
    fenced = '```\n{"text":"hello"}\n```'

    assert SolutionVLMClient._strip_json_code_fences(fenced) == '{"text":"hello"}'


def test_solution_coaching_strip_json_code_fences_strips_any_fence_language() -> None:
    fenced = '```python\n{"text":"hello"}\n```'

    assert SolutionVLMClient._strip_json_code_fences(fenced) == '{"text":"hello"}'


def test_solution_coaching_strip_json_code_fences_preserves_incomplete_closing_fence() -> None:
    fenced = '```json\n{"text":"hello"}'

    assert SolutionVLMClient._strip_json_code_fences(fenced) == fenced


def test_solution_coaching_strip_json_code_fences_preserves_single_line_fence() -> None:
    fenced = '```json {"text":"hello"} ```'

    assert SolutionVLMClient._strip_json_code_fences(fenced) == fenced


def test_solution_coaching_strip_json_code_fences_returns_empty_string_for_empty_input() -> None:
    assert SolutionVLMClient._strip_json_code_fences("") == ""


def test_solution_coaching_strip_json_code_fences_strips_fence_with_whitespace_in_opening() -> None:
    fenced = '``` json\n{"text":"hello"}\n```'

    assert SolutionVLMClient._strip_json_code_fences(fenced) == '{"text":"hello"}'


def test_solution_coaching_strip_json_code_fences_strips_multiline_json_content() -> None:
    fenced = '```json\n{\n  "text": "hello",\n  "value": 42\n}\n```'

    assert SolutionVLMClient._strip_json_code_fences(fenced) == '{\n  "text": "hello",\n  "value": 42\n}'
