from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from litellm.exceptions import (
    APIConnectionError,
    BadRequestError,
    InternalServerError,
    Timeout,
)

from app.infrastructure.config.settings import Settings
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
from app.infrastructure.vlm.solution_coaching_client import (
    CoachingVLMClient,
    SolutionVLMClient,
)
from app.infrastructure.vlm.prompts import (
    ENGLISH_EXTRACTION_SYSTEM_PROMPT,
    MATH_EXTRACTION_SYSTEM_PROMPT,
)
from app.domain.state import recover_stale_preview


def _mock_response(content: str, reasoning_content: str | None = None) -> Any:
    message = SimpleNamespace(
        role="assistant",
        content=content,
        reasoning_content=reasoning_content,
        provider_specific_fields=None,
    )
    choice = SimpleNamespace(index=0, message=message)
    return SimpleNamespace(choices=[choice])


def _build_client(
    completion_fn: Callable[..., Any] | None = None,
    responses_fn: Callable[..., Any] | None = None,
    *,
    provider: str = "openai",
    api_mode: Literal["chat", "responses"] = "chat",
    **kwargs: Any,
) -> VLMClient:
    return VLMClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
        provider=provider,
        api_mode=api_mode,
        completion_fn=completion_fn,
        responses_fn=responses_fn,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_vlm_extraction_happy_path() -> None:
    async def completion_fn(**kwargs):
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert any(part.get("type") == "image_url" for part in messages[1]["content"])
        return _mock_response(
            json.dumps(
                {
                    "text": "Solve x + 1 = 2",
                    "problemType": "short-answer",
                    "graphDsl": None,
                    "providerMetadata": {"provider": "demo"},
                }
            )
        )

    client = _build_client(completion_fn)

    result = await client.extract(image_url="s3://bucket/key")

    assert isinstance(result, ExtractionResult)
    assert result.request_type == "ingestion"
    assert result.text == "Solve x + 1 = 2"
    assert result.problem_type == "short-answer"
    assert result.raw_provider_response["providerMetadata"]["provider"] == "demo"


@pytest.mark.asyncio
async def test_vlm_extraction_prompt_includes_latex_spacing_guidance() -> None:
    async def completion_fn(**kwargs):
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        prompt = messages[0]["content"]
        assert "Use `$...$` for inline math" in prompt
        assert "Put one ASCII space before and after every inline `$...$`" in prompt
        return _mock_response(
            json.dumps(
                {
                    "text": "Find $x$ when $x+1=2$",
                    "problemType": "short-answer",
                    "graphDsl": None,
                }
            )
        )

    client = _build_client(completion_fn)

    result = await client.extract(image_url="s3://bucket/key")

    assert result.text == "Find $x$ when $x+1=2$"


@pytest.mark.asyncio
async def test_vlm_extraction_prompt_includes_expected_response_schema() -> None:
    async def completion_fn(**kwargs):
        messages = kwargs["messages"]
        schema_prompt = messages[1]["content"][0]["text"]
        assert "Expected JSON schema:" in schema_prompt
        assert '"required": ["text", "problemType"]' in schema_prompt
        assert '"graphDsl": {"type": ["string", "null"]}' in schema_prompt
        return _mock_response(
            json.dumps(
                {
                    "text": "Find $x$ when $x+1=2$",
                    "problemType": "short-answer",
                    "graphDsl": None,
                }
            )
        )

    client = _build_client(completion_fn)

    result = await client.extract(image_url="s3://bucket/key")

    assert result.text == "Find $x$ when $x+1=2$"


def _build_english_client(completion_fn: Callable[..., Any]) -> VLMClient:
    return VLMClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
        completion_fn=completion_fn,
        extraction_system_prompt=ENGLISH_EXTRACTION_SYSTEM_PROMPT,
        request_correct_answer=True,
    )


@pytest.mark.asyncio
async def test_vlm_extraction_math_does_not_request_correct_answer() -> None:
    captured: dict = {}

    async def completion_fn(**kwargs):
        captured["user_prompt"] = kwargs["messages"][1]["content"][0]["text"]
        return _mock_response(
            json.dumps({"text": "x", "problemType": "short-answer", "graphDsl": None})
        )

    client = _build_client(completion_fn)
    await client.extract(image_url="s3://bucket/key")

    assert '"correctAnswer"' not in captured["user_prompt"]
    assert "nullable \"correctAnswer\"" not in captured["user_prompt"]


@pytest.mark.asyncio
async def test_vlm_extraction_english_requests_correct_answer() -> None:
    captured: dict = {}

    async def completion_fn(**kwargs):
        captured["user_prompt"] = kwargs["messages"][1]["content"][0]["text"]
        return _mock_response(
            json.dumps(
                {
                    "text": "I go to school by ___.",
                    "problemType": "fill-in-the-blank",
                    "graphDsl": None,
                    "correctAnswer": "bus",
                }
            )
        )

    client = _build_english_client(completion_fn)
    result = await client.extract(image_url="s3://bucket/key")

    assert '"correctAnswer": {"type": ["string", "null"]}' in captured["user_prompt"]
    assert 'nullable "correctAnswer"' in captured["user_prompt"]
    assert result.correct_answer == "bus"


@pytest.mark.asyncio
async def test_vlm_extraction_parses_null_correct_answer() -> None:
    async def completion_fn(**kwargs):
        return _mock_response(
            json.dumps(
                {
                    "text": "Write a sentence.",
                    "problemType": "short-answer",
                    "graphDsl": None,
                    "correctAnswer": None,
                }
            )
        )

    client = _build_english_client(completion_fn)
    result = await client.extract(image_url="s3://bucket/key")

    assert result.correct_answer is None


@pytest.mark.asyncio
async def test_vlm_extraction_tolerates_omitted_correct_answer() -> None:
    async def completion_fn(**kwargs):
        return _mock_response(
            json.dumps(
                {
                    "text": "Write a sentence.",
                    "problemType": "short-answer",
                    "graphDsl": None,
                }
            )
        )

    client = _build_english_client(completion_fn)
    result = await client.extract(image_url="s3://bucket/key")

    assert result.correct_answer is None


@pytest.mark.asyncio
async def test_vlm_extraction_prompt_includes_keepaspectratio_guidance() -> None:
    async def completion_fn(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        assert "setBoundingBox([xMin, yMax, xMax, yMin], true)" in prompt
        assert "preserve the source diagram" in prompt
        assert "JXG.JSXGraph.initBoard" in prompt
        return _mock_response(
            json.dumps(
                {
                    "text": "Triangle problem",
                    "problemType": "short-answer",
                    "graphDsl": None,
                }
            )
        )

    client = _build_client(completion_fn)
    result = await client.extract(image_url="s3://bucket/key")

    assert result.text == "Triangle problem"


@pytest.mark.asyncio
async def test_vlm_grading_happy_path() -> None:
    async def completion_fn(**kwargs):
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"][1]["type"] == "image_url"
        grading_context = messages[1]["content"][0]["text"]
        assert '"problemText": "What is 1 + 1?"' in grading_context
        assert '"userAnswer": "1"' in grading_context
        assert '"correctAnswer": "1"' in grading_context
        assert '"subject": "math"' in grading_context
        return _mock_response(
            json.dumps(
                {
                    "isCorrect": True,
                    "feedback": "Correct.",
                    "providerMetadata": {"provider": "demo"},
                }
            )
        )

    client = _build_client(completion_fn)

    result = await client.grade_short_answer(
        image_url="s3://bucket/key",
        problem_text="What is 1 + 1?",
        user_answer="1",
        correct_answer="1",
    )

    assert isinstance(result, GradingResult)
    assert result.request_type == "short-answer-grading"
    assert result.is_correct is True
    assert result.feedback == "Correct."


@pytest.mark.asyncio
async def test_vlm_grading_includes_subject_in_task_data() -> None:
    async def completion_fn(**kwargs):
        grading_context = kwargs["messages"][1]["content"][0]["text"]
        assert '"subject": "english"' in grading_context
        return _mock_response(
            json.dumps(
                {
                    "isCorrect": False,
                    "feedback": "Incorrect.",
                    "providerMetadata": {"provider": "demo"},
                }
            )
        )

    client = _build_client(completion_fn)

    result = await client.grade_short_answer(
        image_url="s3://bucket/key",
        problem_text="What is the capital of France?",
        user_answer="London",
        correct_answer="Paris",
        subject="english",
    )

    assert result.is_correct is False


@pytest.mark.asyncio
async def test_vlm_extraction_accepts_fenced_json_content() -> None:
    async def completion_fn(**kwargs):
        return _mock_response(
            "```json\n{\n  \"text\": \"Solve x + 1 = 2\",\n  \"problemType\": \"short-answer\",\n  \"graphDsl\": null,\n  \"providerMetadata\": {\"provider\": \"demo\"}\n}\n```"
        )

    client = _build_client(completion_fn)

    result = await client.extract(image_url="s3://bucket/key")

    assert result.text == "Solve x + 1 = 2"
    assert result.problem_type == "short-answer"


@pytest.mark.asyncio
async def test_vlm_retries_only_retryable_failures() -> None:
    async def completion_fn(**kwargs):
        raise InternalServerError(
            message="overloaded", model="demo", llm_provider="openai"
        )

    client = _build_client(completion_fn)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_PROVIDER
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_vlm_non_retryable_client_failure() -> None:
    async def completion_fn(**kwargs):
        raise BadRequestError(
            message="bad request", model="demo", llm_provider="openai"
        )

    client = _build_client(completion_fn)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_PROVIDER_REJECTED
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_vlm_invalid_response_shape_is_rejected() -> None:
    async def completion_fn(**kwargs):
        return _mock_response("not json")

    client = _build_client(completion_fn)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_vlm_parser_rejects_empty_content() -> None:
    async def completion_fn(**kwargs):
        return _mock_response("")

    client = _build_client(completion_fn)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False
    assert str(exc_info.value) == "VLM provider response content was empty"


@pytest.mark.asyncio
async def test_vlm_parser_rejects_empty_choices() -> None:
    async def completion_fn(**kwargs):
        return SimpleNamespace(choices=[])

    client = _build_client(completion_fn)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False
    assert str(exc_info.value) == "VLM provider returned no choices"


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
    async def completion_fn(**kwargs):
        raise Timeout(message="timed out", model="demo", llm_provider="openai")

    client = _build_client(completion_fn)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_TIMEOUT
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_vlm_network_error_is_retryable() -> None:
    async def completion_fn(**kwargs):
        raise APIConnectionError(message="boom", model="demo", llm_provider="openai")

    client = _build_client(completion_fn)

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")

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


def _classification_completion(subject: str, confidence: float) -> Callable[..., Any]:
    async def completion_fn(**kwargs):
        return _mock_response(
            json.dumps(
                {
                    "subject": subject,
                    "confidence": confidence,
                    "reason": "test",
                    "providerMetadata": {"provider": "demo"},
                }
            )
        )

    return completion_fn


def _detection_completion(subject: str, boxes: list[dict[str, Any]]) -> Callable[..., Any]:
    async def completion_fn(**kwargs):
        return _mock_response(
            json.dumps(
                {
                    "subject": subject,
                    "boxes": boxes,
                    "providerMetadata": {"provider": "demo"},
                }
            )
        )

    return completion_fn


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
    assert "solving or answering" in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "Do not solve the problem or infer an answer that is not shown" not in ENGLISH_EXTRACTION_SYSTEM_PROMPT


def test_english_extraction_prompt_instructs_labels_only_for_choices() -> None:
    assert "label only" in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "comma-separated list" in ENGLISH_EXTRACTION_SYSTEM_PROMPT


def test_english_extraction_prompt_instructs_model_answer_for_open_ended() -> None:
    assert "model answer" in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "open-ended" in ENGLISH_EXTRACTION_SYSTEM_PROMPT


def test_english_extraction_prompt_instructs_best_guess_for_uncertain() -> None:
    assert "best guess" in ENGLISH_EXTRACTION_SYSTEM_PROMPT


def test_english_extraction_prompt_reserves_null_for_impossible() -> None:
    assert "too incomplete or incoherent" in ENGLISH_EXTRACTION_SYSTEM_PROMPT


def test_english_extraction_prompt_scopes_no_solve_to_text_field() -> None:
    assert "Do not solve the problem in the `text` field" in ENGLISH_EXTRACTION_SYSTEM_PROMPT
    assert "solve or answer the visible problem to generate it" in ENGLISH_EXTRACTION_SYSTEM_PROMPT


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

    async def completion_fn(**kwargs):
        assert kwargs["messages"][0]["content"] == custom_prompt
        return _mock_response(
            json.dumps(
                {
                    "text": "Test problem",
                    "problemType": "short-answer",
                    "graphDsl": None,
                }
            )
        )

    client = VLMClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
        completion_fn=completion_fn,
        extraction_system_prompt=custom_prompt,
    )

    result = await client.extract(image_url="s3://bucket/key")

    assert result.text == "Test problem"


@pytest.mark.asyncio
async def test_vlm_client_defaults_to_math_extraction_prompt() -> None:
    captured_prompt = None

    async def completion_fn(**kwargs):
        nonlocal captured_prompt
        captured_prompt = kwargs["messages"][0]["content"]
        return _mock_response(
            json.dumps(
                {
                    "text": "Test",
                    "problemType": "short-answer",
                    "graphDsl": None,
                }
            )
        )

    client = _build_client(completion_fn)
    await client.extract(image_url="s3://bucket/key")

    assert captured_prompt == MATH_EXTRACTION_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_vlm_classification_happy_path_math() -> None:
    client = _build_client(_classification_completion("math", 0.95))
    result = await client.classify_subject(image_url="s3://bucket/key")

    assert isinstance(result, ClassificationResult)
    assert result.subject == "math"
    assert result.confidence == 0.95


@pytest.mark.asyncio
async def test_vlm_classification_happy_path_english() -> None:
    client = _build_client(_classification_completion("english", 0.8))
    result = await client.classify_subject(image_url="s3://bucket/key")

    assert isinstance(result, ClassificationResult)
    assert result.subject == "english"
    assert result.confidence == 0.8


@pytest.mark.asyncio
async def test_vlm_classification_rejects_invalid_subject() -> None:
    client = _build_client(_classification_completion("science", 0.9))

    with pytest.raises(VLMError) as exc_info:
        await client.classify_subject(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_vlm_classification_rejects_confidence_below_zero() -> None:
    client = _build_client(_classification_completion("math", -0.1))

    with pytest.raises(VLMError) as exc_info:
        await client.classify_subject(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_vlm_classification_rejects_confidence_above_one() -> None:
    client = _build_client(_classification_completion("english", 1.5))

    with pytest.raises(VLMError) as exc_info:
        await client.classify_subject(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_vlm_extract_normalizes_leading_think_block() -> None:
    async def completion_fn(**kwargs):
        return _mock_response(
            "<think>internal reasoning</think>\n"
            + json.dumps(
                {
                    "text": "Solve x + 1 = 2",
                    "problemType": "short-answer",
                    "graphDsl": None,
                }
            )
        )

    client = _build_client(completion_fn)
    result = await client.extract(image_url="s3://bucket/key")

    assert result.text == "Solve x + 1 = 2"
    assert result.raw_provider_response["reasoning_content"] == "internal reasoning"


@pytest.mark.asyncio
async def test_vlm_extract_preserves_explicit_reasoning_content() -> None:
    async def completion_fn(**kwargs):
        return _mock_response(
            json.dumps(
                {
                    "text": "Find x",
                    "problemType": "short-answer",
                    "graphDsl": None,
                }
            ),
            reasoning_content="explicit reasoning",
        )

    client = _build_client(completion_fn)
    result = await client.extract(image_url="s3://bucket/key")

    assert result.raw_provider_response["reasoning_content"] == "explicit reasoning"


@pytest.mark.asyncio
async def test_vlm_extract_explicit_reasoning_wins_over_think_block() -> None:
    async def completion_fn(**kwargs):
        return _mock_response(
            "<think>think reasoning</think>"
            + json.dumps(
                {
                    "text": "Find x",
                    "problemType": "short-answer",
                    "graphDsl": None,
                }
            ),
            reasoning_content="explicit reasoning",
        )

    client = _build_client(completion_fn)
    result = await client.extract(image_url="s3://bucket/key")

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
    client = _build_client(
        _detection_completion(
            "math",
            [
                {"x": 10, "y": 20, "width": 100, "height": 50},
                {"x": 10, "y": 80, "width": 100, "height": 50},
            ],
        )
    )

    result = await client.detect_problem_boxes(image_url="s3://bucket/key")

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
    client = _build_client(_detection_completion("english", []))

    result = await client.detect_problem_boxes(image_url="s3://bucket/key")

    assert result.subject == "english"
    assert result.boxes == []


@pytest.mark.asyncio
async def test_vlm_detection_rejects_invalid_subject() -> None:
    client = _build_client(
        _detection_completion("science", [{"x": 0, "y": 0, "width": 10, "height": 10}])
    )

    with pytest.raises(VLMError) as exc_info:
        await client.detect_problem_boxes(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.raw_provider_response is not None


@pytest.mark.asyncio
async def test_vlm_detection_rejects_missing_box_coordinate() -> None:
    client = _build_client(
        _detection_completion("math", [{"x": 0, "y": 0, "width": 10}])
    )

    with pytest.raises(VLMError) as exc_info:
        await client.detect_problem_boxes(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_vlm_detection_rejects_negative_box_coordinate() -> None:
    client = _build_client(
        _detection_completion("math", [{"x": -1, "y": 0, "width": 10, "height": 10}])
    )

    with pytest.raises(VLMError) as exc_info:
        await client.detect_problem_boxes(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_vlm_detection_rejects_zero_box_area() -> None:
    client = _build_client(
        _detection_completion("math", [{"x": 0, "y": 0, "width": 0, "height": 10}])
    )

    with pytest.raises(VLMError) as exc_info:
        await client.detect_problem_boxes(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_vlm_detection_prompt_includes_subject_and_boxes_schema() -> None:
    async def completion_fn(**kwargs):
        messages = kwargs["messages"]
        system_prompt = messages[0]["content"]
        user_prompt = messages[1]["content"][0]["text"]
        assert "boxes" in system_prompt
        assert "subject" in system_prompt
        assert "natural-image pixel coordinates" in user_prompt
        assert '"subject": {"type": "string", "enum": ["math", "english"]}' in user_prompt
        assert '"boxes":' in user_prompt
        return _mock_response(
            json.dumps(
                {
                    "subject": "math",
                    "boxes": [{"x": 0, "y": 0, "width": 10, "height": 10}],
                }
            )
        )

    client = _build_client(completion_fn)
    result = await client.detect_problem_boxes(image_url="s3://bucket/key")

    assert result.subject == "math"


# Tests for Responses API mode

def _mock_responses_response(content: str) -> Any:
    """Mock a Responses API response using the SDK's output_text accessor."""
    return SimpleNamespace(output_text=content)


@pytest.mark.asyncio
async def test_vlm_responses_mode_extraction_happy_path() -> None:
    """Responses mode should use Responses API contract with input_image."""
    async def responses_fn(**kwargs):
        # Verify Responses API structure
        assert "instructions" in kwargs
        assert "input" in kwargs
        assert kwargs["input"][0]["type"] == "input_text"
        assert kwargs["input"][1]["type"] == "input_image"
        assert "image_url" in kwargs["input"][1]
        
        return _mock_responses_response(
            json.dumps(
                {
                    "text": "Solve x + 1 = 2",
                    "problemType": "short-answer",
                    "graphDsl": None,
                    "providerMetadata": {"provider": "demo"},
                }
            )
        )

    client = _build_client(
        responses_fn=responses_fn,
        api_mode="responses",
    )

    result = await client.extract(image_url="s3://bucket/key")

    assert isinstance(result, ExtractionResult)
    assert result.request_type == "ingestion"
    assert result.text == "Solve x + 1 = 2"
    assert result.problem_type == "short-answer"


@pytest.mark.asyncio
async def test_vlm_responses_mode_with_base64_image() -> None:
    """Responses mode with base64 image should use input_image with data URL."""
    async def responses_fn(**kwargs):
        input_items = kwargs["input"]
        assert input_items[1]["type"] == "input_image"
        assert input_items[1]["image_url"].startswith("data:image/png;base64,")
        
        return _mock_responses_response(
            json.dumps(
                {
                    "text": "Test problem",
                    "problemType": "single-choice",
                    "graphDsl": None,
                    "providerMetadata": {},
                }
            )
        )

    client = _build_client(
        responses_fn=responses_fn,
        api_mode="responses",
    )

    result = await client.extract(image_base64="YWJjMTIz")  # dummy base64

    assert result.text == "Test problem"
    assert result.problem_type == "single-choice"


@pytest.mark.asyncio
async def test_vlm_responses_mode_timeout_error() -> None:
    """Responses mode timeout should map to VLMError."""
    async def responses_fn(**kwargs):
        raise Timeout(message="timed out", model="demo", llm_provider="openai")

    client = _build_client(
        responses_fn=responses_fn,
        api_mode="responses",
    )

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_TIMEOUT
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_vlm_responses_mode_invalid_json() -> None:
    """Responses mode should handle invalid JSON in output_text."""
    async def responses_fn(**kwargs):
        return _mock_responses_response("not valid json")

    client = _build_client(
        responses_fn=responses_fn,
        api_mode="responses",
    )

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_url="s3://bucket/key")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_vlm_chat_mode_unchanged_behavior() -> None:
    """Chat mode should retain existing behavior (default)."""
    async def completion_fn(**kwargs):
        # Verify chat completion structure
        assert "messages" in kwargs
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert any(part.get("type") == "image_url" for part in messages[1]["content"])
        
        return _mock_response(
            json.dumps(
                {
                    "text": "Chat mode problem",
                    "problemType": "fill-in-the-blank",
                    "graphDsl": None,
                    "providerMetadata": {},
                }
            )
        )

    # Default api_mode is "chat"
    client = _build_client(completion_fn)

    result = await client.extract(image_url="s3://bucket/key")

    assert result.text == "Chat mode problem"
    assert result.problem_type == "fill-in-the-blank"


# Integration construction tests

def test_vlm_client_factory_passes_api_mode_chat() -> None:
    """Dependency provider should pass chat api_mode from settings."""
    from app.presentation.deps import create_math_ingestion_vlm_client
    
    settings = Settings(
        math_ingestion_vlm_endpoint="https://test.example/api",
        math_ingestion_vlm_model="test-model",
        math_ingestion_vlm_api_key="test-key",
        math_ingestion_vlm_timeout_seconds=60,
        math_ingestion_vlm_provider="openai",
        math_ingestion_vlm_api_mode="chat",
    )
    
    client = create_math_ingestion_vlm_client(settings)
    
    assert client._api_mode == "chat"


def test_vlm_client_factory_passes_api_mode_responses() -> None:
    """Dependency provider should pass responses api_mode from settings."""
    from app.presentation.deps import get_grading_vlm_client
    
    settings = Settings(
        grading_vlm_endpoint="https://grading.example/api",
        grading_vlm_model="grading-model",
        grading_vlm_api_key="grading-key",
        grading_vlm_timeout_seconds=60,
        grading_vlm_provider="openai",
        grading_vlm_api_mode="responses",
    )
    
    # Note: get_grading_vlm_client is an async generator, so we need to handle it
    import asyncio
    
    async def get_client():
        gen = get_grading_vlm_client(settings)
        client = await gen.__anext__()
        try:
            return client
        finally:
            await gen.aclose()
    
    client = asyncio.run(get_client())
    
    assert client._api_mode == "responses"


def test_solution_client_reads_api_mode_from_settings() -> None:
    """SolutionVLMClient should read api_mode from settings."""
    settings = Settings(
        math_solution_vlm_endpoint="https://solution.example/api",
        math_solution_vlm_model="solution-model",
        math_solution_vlm_api_key="solution-key",
        math_solution_vlm_timeout_seconds=120,
        math_solution_vlm_provider="openai",
        math_solution_vlm_api_mode="responses",
    )
    
    client = SolutionVLMClient(settings=settings)
    
    assert client._api_mode == "responses"


def test_coaching_client_reads_api_mode_from_settings() -> None:
    """CoachingVLMClient should read api_mode from settings."""
    settings = Settings(
        english_coaching_vlm_endpoint="https://coaching.example/api",
        english_coaching_vlm_model="coaching-model",
        english_coaching_vlm_api_key="coaching-key",
        english_coaching_vlm_timeout_seconds=60,
        english_coaching_vlm_provider="openai",
        english_coaching_vlm_api_mode="responses",
    )
    
    client = CoachingVLMClient(settings=settings, subject="english")
    
    assert client._api_mode == "responses"
