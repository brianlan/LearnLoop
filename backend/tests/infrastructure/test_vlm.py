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
    ExtractionResult,
    GradingResult,
    VLMClient,
    VLMError,
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
        assert "Put whitespace around inline `$...$`" in prompt
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


@pytest.mark.asyncio
async def test_vlm_extraction_prompt_includes_keepaspectratio_guidance() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((await request.aread()).decode())
        prompt = payload["messages"][0]["content"]
        assert "keepaspectratio: true" in prompt
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


def test_strip_json_code_fences_leaves_plain_json_unchanged() -> None:
    plain = '{"text":"hello"}'

    assert VLMClient._strip_json_code_fences(plain) == plain


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
