from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest

from app.infrastructure.vlm.client import (
    FAILURE_CODE_INVALID_RESPONSE,
    FAILURE_CODE_NETWORK,
    FAILURE_CODE_PROVIDER,
    FAILURE_CODE_PROVIDER_REJECTED,
    FAILURE_CODE_STALE_PREVIEW,
    FAILURE_CODE_TIMEOUT,
    ClassificationResult,
    DetectionResult,
    ExtractionResult,
    GradingResult,
    VLMClient,
    VLMError,
)
from app.infrastructure.vlm.prompts import (
    ENGLISH_EXTRACTION_SYSTEM_PROMPT,
    MATH_EXTRACTION_SYSTEM_PROMPT,
)
from app.domain.state import recover_stale_preview

def _build_client(handler) -> VLMClient:
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


@pytest.mark.asyncio
async def test_vlm_extraction_happy_path() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat/completions"
        payload = await request.aread()
        assert b'"messages"' in payload
        assert b'"image_url"' in payload
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
                                    "text": "Solve x + 1 = 2",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                    "providerMetadata": {"provider": "demo"},
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)

    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert isinstance(result, ExtractionResult)
    assert result.request_type == "ingestion"
    assert result.text == "Solve x + 1 = 2"
    assert result.problem_type == "short-answer"
    assert result.raw_provider_response["providerMetadata"]["provider"] == "demo"


@pytest.mark.asyncio
async def test_vlm_extraction_prompt_includes_latex_spacing_guidance() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        prompt = payload["messages"][0]["content"]
        assert "Use `$...$` for inline math" in prompt
        assert "Put one ASCII space before and after every inline `$...$`" in prompt
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
                                    "text": "Find $x$ when $x+1=2$",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)

    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert result.text == "Find $x$ when $x+1=2$"


@pytest.mark.asyncio
async def test_vlm_extraction_prompt_includes_expected_response_schema() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        schema_prompt = payload["messages"][1]["content"][0]["text"]
        assert "Expected JSON schema:" in schema_prompt
        assert '"required": ["text", "problemType"]' in schema_prompt
        assert '"graphDsl": {"type": ["string", "null"]}' in schema_prompt
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
                                    "text": "Find $x$ when $x+1=2$",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)

    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert result.text == "Find $x$ when $x+1=2$"


def _build_english_client(handler) -> VLMClient:
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
        extraction_system_prompt=ENGLISH_EXTRACTION_SYSTEM_PROMPT,
        request_correct_answer=True,
    )


@pytest.mark.asyncio
async def test_vlm_extraction_math_does_not_request_correct_answer() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        captured["user_prompt"] = payload["messages"][1]["content"][0]["text"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {"text": "x", "problemType": "short-answer", "graphDsl": None}
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)
    await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert '"correctAnswer"' not in captured["user_prompt"]
    assert "nullable \"correctAnswer\"" not in captured["user_prompt"]


@pytest.mark.asyncio
async def test_vlm_extraction_english_requests_correct_answer() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        captured["user_prompt"] = payload["messages"][1]["content"][0]["text"]
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
                                    "text": "I go to school by ___.",
                                    "problemType": "fill-in-the-blank",
                                    "graphDsl": None,
                                    "correctAnswer": "bus",
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_english_client(handler)
    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert '"correctAnswer": {"type": ["string", "null"]}' in captured["user_prompt"]
    assert 'nullable "correctAnswer"' in captured["user_prompt"]
    assert result.correct_answer == "bus"


@pytest.mark.asyncio
async def test_vlm_extraction_parses_null_correct_answer() -> None:
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
                                {
                                    "text": "Write a sentence.",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                    "correctAnswer": None,
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_english_client(handler)
    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert result.correct_answer is None


@pytest.mark.asyncio
async def test_vlm_extraction_tolerates_omitted_correct_answer() -> None:
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
                                {
                                    "text": "Write a sentence.",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_english_client(handler)
    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert result.correct_answer is None


@pytest.mark.asyncio
async def test_vlm_extraction_prompt_includes_keepaspectratio_guidance() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        prompt = payload["messages"][0]["content"]
        assert "setBoundingBox([xMin, yMax, xMax, yMin], true)" in prompt
        assert "preserve the source diagram" in prompt
        assert "JXG.JSXGraph.initBoard" in prompt
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
                                    "text": "Triangle problem",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)
    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert result.text == "Triangle problem"


@pytest.mark.asyncio
async def test_vlm_grading_happy_path() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat/completions"
        payload = json.loads((await request.aread()).decode())
        assert "messages" in payload
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        assert payload["messages"][1]["content"][1]["type"] == "image_url"
        grading_context = payload["messages"][1]["content"][0]["text"]
        assert '"problemText": "What is 1 + 1?"' in grading_context
        assert '"userAnswer": "1"' in grading_context
        assert '"correctAnswer": "1"' in grading_context
        assert '"subject": "math"' in grading_context
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
                                    "isCorrect": True,
                                    "feedback": "Correct.",
                                    "providerMetadata": {"provider": "demo"},
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)

    result = await client.grade_short_answer(
        image_url="s3://bucket/key",
        problem_text="What is 1 + 1?",
        user_answer="1",
        correct_answer="1",
    )
    await client.aclose()

    assert isinstance(result, GradingResult)
    assert result.request_type == "short-answer-grading"
    assert result.is_correct is True
    assert result.feedback == "Correct."


@pytest.mark.asyncio
async def test_vlm_grading_includes_subject_in_task_data() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        grading_context = payload["messages"][1]["content"][0]["text"]
        assert '"subject": "english"' in grading_context
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
                                    "isCorrect": False,
                                    "feedback": "Incorrect.",
                                    "providerMetadata": {"provider": "demo"},
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)

    result = await client.grade_short_answer(
        image_url="s3://bucket/key",
        problem_text="What is the capital of France?",
        user_answer="London",
        correct_answer="Paris",
        subject="english",
    )
    await client.aclose()

    assert result.is_correct is False


@pytest.mark.asyncio
async def test_vlm_extraction_accepts_fenced_json_content() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "```json\n{\n  \"text\": \"Solve x + 1 = 2\",\n  \"problemType\": \"short-answer\",\n  \"graphDsl\": null,\n  \"providerMetadata\": {\"provider\": \"demo\"}\n}\n```",
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)

    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert result.text == "Solve x + 1 = 2"
    assert result.problem_type == "short-answer"


@pytest.mark.asyncio
async def test_vlm_retries_only_retryable_failures() -> None:
    async def server_error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "overloaded"})

    client = _build_client(server_error_handler)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_PROVIDER
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_vlm_non_retryable_client_failure() -> None:
    async def client_error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "bad request"})

    client = _build_client(client_error_handler)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_PROVIDER_REJECTED
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_vlm_invalid_response_shape_is_rejected() -> None:
    async def invalid_shape_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": "not json"}}]})

    client = _build_client(invalid_shape_handler)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_vlm_parser_rejects_invalid_chat_completion_shape() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = _build_client(handler)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False
    assert str(exc_info.value) == "VLM provider response failed chat completion validation"
    assert exc_info.value.raw_provider_response == {"unexpected": "shape"}


@pytest.mark.asyncio
async def test_vlm_parser_rejects_empty_choices() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = _build_client(handler)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False
    assert str(exc_info.value) == "VLM provider returned no choices"
    assert exc_info.value.raw_provider_response == {"choices": []}


@pytest.mark.asyncio
async def test_vlm_parser_rejects_empty_content() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant", "content": None}}]},
        )

    client = _build_client(handler)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False
    assert str(exc_info.value) == "VLM provider response content was empty"
    assert exc_info.value.raw_provider_response == {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": None}}]
    }


def test_strip_json_code_fences_leaves_plain_json_unchanged() -> None:
    plain = '{"text":"hello"}'

    assert VLMClient._strip_json_code_fences(plain) == plain


def test_strip_json_code_fences_strips_json_fence() -> None:
    fenced = '```json\n{"text":"hello"}\n```'

    assert VLMClient._strip_json_code_fences(fenced) == '{"text":"hello"}'


def test_strip_json_code_fences_strips_plain_fence() -> None:
    fenced = '```\n{"text":"hello"}\n```'

    assert VLMClient._strip_json_code_fences(fenced) == '{"text":"hello"}'


def test_strip_json_code_fences_strips_non_json_fence_language() -> None:
    fenced = '```python\n{"text":"hello"}\n```'

    assert VLMClient._strip_json_code_fences(fenced) == '{"text":"hello"}'


def test_strip_json_code_fences_preserves_incomplete_opening_fence() -> None:
    fenced = '```json\n{"text":"hello"}'

    assert VLMClient._strip_json_code_fences(fenced) == fenced


def test_strip_json_code_fences_preserves_incomplete_closing_fence() -> None:
    fenced = '{"text":"hello"}\n```'

    assert VLMClient._strip_json_code_fences(fenced) == fenced


def test_strip_json_code_fences_preserves_single_line_fence() -> None:
    fenced = '```json {"text":"hello"} ```'

    assert VLMClient._strip_json_code_fences(fenced) == fenced


def test_strip_json_code_fences_strips_fence_with_trailing_whitespace() -> None:
    fenced = '  ```json\n{"text":"hello"}\n```  '

    assert VLMClient._strip_json_code_fences(fenced) == '{"text":"hello"}'


def test_strip_json_code_fences_returns_empty_string_for_empty_input() -> None:
    assert VLMClient._strip_json_code_fences("") == ""


def test_strip_json_code_fences_strips_multiline_json_content() -> None:
    fenced = '```json\n{\n  "text": "hello",\n  "value": 42\n}\n```'

    assert VLMClient._strip_json_code_fences(fenced) == '{\n  "text": "hello",\n  "value": 42\n}'


@pytest.mark.asyncio
async def test_vlm_timeout_is_classified_explicitly() -> None:
    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = _build_client(timeout_handler)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_TIMEOUT
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_vlm_network_error_is_retryable() -> None:
    async def network_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _build_client(network_handler)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_NETWORK
    assert exc_info.value.retryable is True


def test_recover_stale_preview_transitions_to_vlm_failed() -> None:
    now = datetime.now(UTC)
    preview = {
        "status": "extracting",
        "createdAt": now - timedelta(seconds=120),
        "updatedAt": now - timedelta(seconds=120),
        "extraction": {
            "requestStartedAt": now - timedelta(seconds=120),
            "requestFinishedAt": None,
            "success": None,
            "failureCode": None,
            "failureMessage": None,
        },
    }

    recovered = recover_stale_preview(preview, now=now, extracting_window_seconds=30)

    assert recovered is not None
    assert recovered["status"] == "vlm-failed"
    assert recovered["extraction"]["failureCode"] == FAILURE_CODE_STALE_PREVIEW
    assert recovered["extraction"]["success"] is False


def test_recover_stale_preview_ignores_fresh_extractions() -> None:
    now = datetime.now(UTC)
    preview = {
        "status": "extracting",
        "createdAt": now - timedelta(seconds=10),
        "updatedAt": now - timedelta(seconds=10),
        "extraction": {"requestStartedAt": now - timedelta(seconds=10)},
    }

    assert recover_stale_preview(preview, now=now, extracting_window_seconds=30) is None


def _classification_handler(subject: str, confidence: float) -> VLMClient:
    """Build a mock VLM client that returns a classification response."""

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
                                {
                                    "subject": subject,
                                    "confidence": confidence,
                                    "reason": "test",
                                    "providerMetadata": {"provider": "demo"},
                                }
                            ),
                        },
                    }
                ],
            },
        )

    return _build_client(handler)


def _detection_handler(subject: str, boxes: list[dict[str, Any]]) -> VLMClient:
    """Build a mock VLM client that returns a problem-box detection response."""

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
                                {
                                    "subject": subject,
                                    "boxes": boxes,
                                    "providerMetadata": {"provider": "demo"},
                                }
                            ),
                        },
                    }
                ],
            },
        )

    return _build_client(handler)


def test_math_extraction_prompt_contains_math_guidance() -> None:
    assert "JSXGraph" in MATH_EXTRACTION_SYSTEM_PROMPT
    assert "LaTeX" in MATH_EXTRACTION_SYSTEM_PROMPT
    assert "$...$" in MATH_EXTRACTION_SYSTEM_PROMPT


def test_english_extraction_prompt_allows_diagram_dsl() -> None:
    assert "JSXGraph" in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "graphDsl: nullable string" in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "English problems do not use geometric diagrams" not in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "geometry or coordinate-style diagrams" in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "bar charts" in ENGLISH_EXTRACTION_SYSTEM_PROMPT


def test_english_extraction_prompt_contains_english_guidance() -> None:
    assert "multi-choice" in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "passage" in ENGLISH_EXTRACTION_SYSTEM_PROMPT.lower() or "text" in ENGLISH_EXTRACTION_SYSTEM_PROMPT.lower()


def test_english_extraction_prompt_documents_correct_answer() -> None:
    assert "correctAnswer" in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "nullable string" in ENGLISH_EXTRACTION_SYSTEM_PROMPT


def test_math_extraction_prompt_does_not_request_correct_answer() -> None:
    assert "correctAnswer" not in MATH_EXTRACTION_SYSTEM_PROMPT


def test_math_extraction_prompt_scopes_javascript_to_graph_dsl_field() -> None:
    assert "`graphDsl` JSON field" in MATH_EXTRACTION_SYSTEM_PROMPT
    assert "Output ONLY the JavaScript code" not in MATH_EXTRACTION_SYSTEM_PROMPT
    assert "Return only valid JSON" in MATH_EXTRACTION_SYSTEM_PROMPT


def test_math_extraction_prompt_instructs_options_on_own_line() -> None:
    assert "each option" in MATH_EXTRACTION_SYSTEM_PROMPT.lower() or "each option" in MATH_EXTRACTION_SYSTEM_PROMPT
    assert "own line" in MATH_EXTRACTION_SYSTEM_PROMPT.lower() or "own line" in MATH_EXTRACTION_SYSTEM_PROMPT


def test_math_extraction_prompt_spaces_cjk_punctuation_next_to_inline_latex() -> None:
    assert "Put one ASCII space before and after every inline `$...$`" in MATH_EXTRACTION_SYSTEM_PROMPT
    assert "Chinese-style punctuation" in MATH_EXTRACTION_SYSTEM_PROMPT
    assert "已知 $x+1=2$ ，求 $x$ 的值。" in MATH_EXTRACTION_SYSTEM_PROMPT
    assert "已知$x+1=2$，求$x$的值。" in MATH_EXTRACTION_SYSTEM_PROMPT


def test_english_extraction_prompt_instructs_options_on_own_line() -> None:
    assert "each option" in ENGLISH_EXTRACTION_SYSTEM_PROMPT.lower() or "each option" in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "own line" in ENGLISH_EXTRACTION_SYSTEM_PROMPT.lower() or "own line" in ENGLISH_EXTRACTION_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_vlm_client_uses_custom_extraction_prompt() -> None:
    custom_prompt = "Custom extraction prompt for testing"

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        assert payload["messages"][0]["content"] == custom_prompt
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
                                    "text": "Test problem",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                }
                            ),
                        },
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://vlm.example/api",
        timeout=5,
        headers={"Authorization": "Bearer demo", "Content-Type": "application/json"},
    )
    client = VLMClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
        http_client=http_client,
        extraction_system_prompt=custom_prompt,
    )

    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert result.text == "Test problem"


@pytest.mark.asyncio
async def test_vlm_client_defaults_to_math_extraction_prompt() -> None:
    captured_prompt = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_prompt
        payload = json.loads((await request.aread()).decode())
        captured_prompt = payload["messages"][0]["content"]
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
                                    "text": "Test",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)
    await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert captured_prompt == MATH_EXTRACTION_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_vlm_classification_happy_path_math() -> None:
    client = _classification_handler("math", 0.95)
    result = await client.classify_subject(image_url="s3://bucket/key")
    await client.aclose()

    assert isinstance(result, ClassificationResult)
    assert result.subject == "math"
    assert result.confidence == 0.95


@pytest.mark.asyncio
async def test_vlm_classification_happy_path_english() -> None:
    client = _classification_handler("english", 0.8)
    result = await client.classify_subject(image_url="s3://bucket/key")
    await client.aclose()

    assert isinstance(result, ClassificationResult)
    assert result.subject == "english"
    assert result.confidence == 0.8


@pytest.mark.asyncio
async def test_vlm_classification_rejects_invalid_subject() -> None:
    client = _classification_handler("science", 0.9)

    with pytest.raises(VLMError) as exc_info:
        await client.classify_subject(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_vlm_classification_rejects_confidence_below_zero() -> None:
    client = _classification_handler("math", -0.1)

    with pytest.raises(VLMError) as exc_info:
        await client.classify_subject(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_vlm_classification_rejects_confidence_above_one() -> None:
    client = _classification_handler("english", 1.5)

    with pytest.raises(VLMError) as exc_info:
        await client.classify_subject(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_vlm_extract_normalizes_leading_think_block() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "<think>internal reasoning</think>\n"
                            + json.dumps(
                                {
                                    "text": "Solve x + 1 = 2",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)
    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert result.text == "Solve x + 1 = 2"
    assert result.raw_provider_response["reasoning_content"] == "internal reasoning"


@pytest.mark.asyncio
async def test_vlm_extract_preserves_explicit_reasoning_content() -> None:
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
                                {
                                    "text": "Find x",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                }
                            ),
                            "reasoning_content": "explicit reasoning",
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)
    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert result.raw_provider_response["reasoning_content"] == "explicit reasoning"


@pytest.mark.asyncio
async def test_vlm_extract_explicit_reasoning_wins_over_think_block() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "<think>think reasoning</think>"
                            + json.dumps(
                                {
                                    "text": "Find x",
                                    "problemType": "short-answer",
                                    "graphDsl": None,
                                }
                            ),
                            "reasoning_content": "explicit reasoning",
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)
    result = await client.extract(image_url="s3://bucket/key")
    await client.aclose()

    assert result.raw_provider_response["reasoning_content"] == "explicit reasoning"


def test_strip_thinking_content_extracts_leading_block() -> None:
    content, reasoning = VLMClient._strip_thinking_content(
        "  <think>reasoning</think>{\"text\":\"hello\"}"
    )

    assert content == '{"text":"hello"}'
    assert reasoning == "reasoning"


def test_strip_thinking_content_leaves_content_unchanged_when_no_block() -> None:
    content, reasoning = VLMClient._strip_thinking_content('{"text":"hello"}')

    assert content == '{"text":"hello"}'
    assert reasoning is None


def test_strip_thinking_content_leaves_incomplete_block_unchanged() -> None:
    content, reasoning = VLMClient._strip_thinking_content("<think>incomplete")

    assert content == "<think>incomplete"
    assert reasoning is None


@pytest.mark.asyncio
async def test_vlm_detection_happy_path_with_multiple_boxes() -> None:
    client = _detection_handler(
        "math",
        [
            {"x": 10, "y": 20, "width": 100, "height": 50},
            {"x": 10, "y": 80, "width": 100, "height": 50},
        ],
    )

    result = await client.detect_problem_boxes(image_url="s3://bucket/key")
    await client.aclose()

    assert isinstance(result, DetectionResult)
    assert result.request_type == "problem-box-detection"
    assert result.subject == "math"
    assert len(result.boxes) == 2
    assert result.boxes[0].x == 10
    assert result.boxes[0].y == 20
    assert result.boxes[0].width == 100
    assert result.boxes[0].height == 50
    assert result.raw_provider_response["boxes"] == [
        {"x": 10, "y": 20, "width": 100, "height": 50},
        {"x": 10, "y": 80, "width": 100, "height": 50},
    ]


@pytest.mark.asyncio
async def test_vlm_detection_accepts_zero_boxes() -> None:
    client = _detection_handler("english", [])

    result = await client.detect_problem_boxes(image_url="s3://bucket/key")
    await client.aclose()

    assert result.subject == "english"
    assert result.boxes == []


@pytest.mark.asyncio
async def test_vlm_detection_rejects_invalid_subject() -> None:
    client = _detection_handler("science", [{"x": 0, "y": 0, "width": 10, "height": 10}])

    with pytest.raises(VLMError) as exc_info:
        await client.detect_problem_boxes(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.raw_provider_response is not None


@pytest.mark.asyncio
async def test_vlm_detection_rejects_missing_box_coordinate() -> None:
    client = _detection_handler("math", [{"x": 0, "y": 0, "width": 10}])

    with pytest.raises(VLMError) as exc_info:
        await client.detect_problem_boxes(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_vlm_detection_rejects_negative_box_coordinate() -> None:
    client = _detection_handler("math", [{"x": -1, "y": 0, "width": 10, "height": 10}])

    with pytest.raises(VLMError) as exc_info:
        await client.detect_problem_boxes(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_vlm_detection_rejects_zero_box_area() -> None:
    client = _detection_handler("math", [{"x": 0, "y": 0, "width": 0, "height": 10}])

    with pytest.raises(VLMError) as exc_info:
        await client.detect_problem_boxes(image_url="s3://bucket/key")
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_vlm_detection_prompt_includes_subject_and_boxes_schema() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        system_prompt = payload["messages"][0]["content"]
        user_prompt = payload["messages"][1]["content"][0]["text"]
        assert "boxes" in system_prompt
        assert "subject" in system_prompt
        assert "natural-image pixel coordinates" in user_prompt
        assert '"subject": {"type": "string", "enum": ["math", "english"]}' in user_prompt
        assert '"boxes":' in user_prompt
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
                                    "subject": "math",
                                    "boxes": [{"x": 0, "y": 0, "width": 10, "height": 10}],
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = _build_client(handler)
    result = await client.detect_problem_boxes(image_url="s3://bucket/key")
    await client.aclose()

    assert result.subject == "math"
