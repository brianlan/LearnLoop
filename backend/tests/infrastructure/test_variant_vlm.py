"""Provider-input capture tests for the variant VLM clients (issue #612).

Uses injected completion/responses functions so no real provider is contacted.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from litellm.exceptions import APIConnectionError

from app.domain.ingestion.variation import ModelIdentity, ProblemContent
from app.infrastructure.config.settings import Settings
from app.infrastructure.vlm.base_client import FAILURE_CODE_INVALID_RESPONSE
from app.infrastructure.vlm.variant_client import (
    VariantGeneratorVLMClient,
    VariantVLMError,
    VariantHelperVLMClient,
    VariantValidatorVLMClient,
    build_variant_generator_vlm_client,
    build_variant_helper_vlm_client,
    build_variant_validator_vlm_client,
    generate_and_validate,
)
from tests.domain.test_variant_validation import PASSING_CATEGORIES, SOURCE

SOURCE_WITH_GRAPH = ProblemContent(
    text=SOURCE.text,
    problemType=SOURCE.problem_type,
    graphDsl="create('board', {boundingbox: [-1, 5, 9, -1]});",
    correctAnswer=SOURCE.correct_answer,
)


def _generator_json(
    text: str = "A train travels 180 km in 3 hours. What is its speed in km/h?",
    problem_type: str = "short-answer",
    graph_dsl: str | None = None,
    correct_answer: str = "60",
) -> str:
    return json.dumps(
        {
            "text": text,
            "problemType": problem_type,
            "graphDsl": graph_dsl,
            "correctAnswer": correct_answer,
        }
    )


def _validator_json(
    original_solved: str | None = "60",
    variant_solved: str | None = "60",
    graph_consistency: str = "not-applicable",
) -> str:  # noqa: ARG001 - kept for symmetry
    checks = {**PASSING_CATEGORIES, "graphConsistency": graph_consistency}
    return json.dumps(
        {
            "originalSolvedAnswer": original_solved,
            "variantSolvedAnswer": variant_solved,
            "originalSolutionSummary": "distance over time",
            "variantSolutionSummary": "distance over time",
            "checks": {
                name: {"category": category, "evidence": "clear"}
                for name, category in checks.items()
            },
        }
    )


def _helper_json(original: str = "equivalent", variant: str = "equivalent") -> str:
    return json.dumps(
        {
            "original": {"result": original, "evidence": "both 60"},
            "variant": {"result": variant, "evidence": "both 60"},
        }
    )


class _Recorder:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.payloads.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _chat_response(response)


class _ResponsesRecorder(_Recorder):
    async def __call__(self, **kwargs: Any) -> Any:
        self.payloads.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return type("R", (), {"output_text": response})()


def _chat_response(content: str) -> Any:
    return type(
        "Response",
        (),
        {
            "choices": [
                type(
                    "Choice",
                    (),
                    {
                        "index": 0,
                        "message": type(
                            "Message",
                            (),
                            {
                                "role": "assistant",
                                "content": content,
                                "reasoning_content": None,
                                "provider_specific_fields": None,
                            },
                        )(),
                    },
                )()
            ]
        },
    )()


def _settings() -> Settings:
    return Settings(
        variant_generator_vlm_endpoint="https://gen.example/api",
        variant_generator_vlm_model="gen-model",
        variant_generator_vlm_api_key="key",
        variant_validator_vlm_endpoint="https://val.example/api",
        variant_validator_vlm_model="val-model",
        variant_validator_vlm_api_key="key",
        variant_validator2_vlm_endpoint="https://val2.example/api",
        variant_validator2_vlm_model="val2-model",
        variant_validator2_vlm_api_key="key",
        helper_vlm_endpoint="https://helper.example/api",
        helper_vlm_model="helper-model",
        helper_vlm_api_key="key",
    )


def _generator_client(recorder: _Recorder) -> VariantGeneratorVLMClient:
    return build_variant_generator_vlm_client(_settings(), completion_fn=recorder)


def _validator_client(recorder: _Recorder, *, second: bool = False) -> VariantValidatorVLMClient:
    return build_variant_validator_vlm_client(_settings(), second=second, completion_fn=recorder)


def _helper_client(recorder: _Recorder) -> VariantHelperVLMClient:
    return build_variant_helper_vlm_client(_settings(), completion_fn=recorder)


@pytest.mark.asyncio
async def test_generator_chat_request_is_text_only_and_carries_source_data() -> None:
    recorder = _Recorder([_generator_json()])
    client = _generator_client(recorder)

    candidate = await client.generate_candidate(mode="data-only", source=SOURCE)

    assert candidate.text.startswith("A train travels 180 km")
    assert candidate.problem_type == "short-answer"
    assert candidate.correct_answer == "60"
    assert candidate.generator == ModelIdentity(provider="openai", model="gen-model")

    kwargs = recorder.payloads[0]
    messages = kwargs["messages"]
    assert messages[0]["role"] == "system"
    user_content = messages[1]["content"]
    assert all(part["type"] == "text" for part in user_content)
    task_text = user_content[0]["text"]
    assert "data-only" in task_text
    assert SOURCE.text in task_text
    assert "correctAnswer" in task_text
    assert "image" not in json.dumps(kwargs).lower()


@pytest.mark.asyncio
async def test_generator_responses_request_is_text_only() -> None:
    recorder = _ResponsesRecorder([_generator_json()])
    settings = _settings().model_copy(update={"variant_generator_vlm_api_mode": "responses"})
    client = VariantGeneratorVLMClient(
        endpoint=settings.variant_generator_vlm_endpoint,
        model=settings.variant_generator_vlm_model,
        api_key=settings.variant_generator_vlm_api_key,
        timeout_seconds=settings.variant_generator_vlm_timeout_seconds,
        provider=settings.variant_generator_vlm_provider,
        api_mode="responses",
        responses_fn=recorder,
        error_factory=VariantVLMError,
    )

    candidate = await client.generate_candidate(mode="data-only", source=SOURCE)

    assert candidate.correct_answer == "60"
    kwargs = recorder.payloads[0]
    input_items = kwargs["input"][0]["content"]
    assert all(item["type"] == "input_text" for item in input_items)
    assert "image" not in json.dumps(kwargs).lower()


@pytest.mark.asyncio
async def test_validator_payload_never_contains_expected_answers_or_images() -> None:
    candidate = await _generator_client(_Recorder([_generator_json()])).generate_candidate(
        mode="data-only", source=SOURCE
    )
    recorder = _Recorder([_validator_json()])
    client = _validator_client(recorder)
    report = await client.produce_report(mode="data-only", source=SOURCE, candidate=candidate)

    kwargs = recorder.payloads[0]
    dumped = json.dumps(kwargs)
    assert SOURCE.correct_answer not in dumped
    assert candidate.correct_answer not in dumped
    assert "image" not in dumped.lower()
    user_content = kwargs["messages"][1]["content"]
    assert all(part["type"] == "text" for part in user_content)
    task_text = user_content[0]["text"]
    assert SOURCE.text in task_text
    assert candidate.text in task_text

    assert report.original_solved_answer == "60"
    assert report.variant_solved_answer == "60"
    assert report.validator_model.model == "val-model"
    assert report.checks["coreKnowledge"].category == "preserved"


@pytest.mark.asyncio
async def test_helper_runs_after_validator_with_only_that_validators_answers() -> None:
    validator_recorder = _Recorder([_validator_json()])
    helper_recorder = _Recorder([_helper_json()])
    calls: list[str] = []

    validator = _validator_client(validator_recorder)
    helper = _helper_client(helper_recorder)

    original_produce = validator.produce_report
    original_compare = helper.compare_answer_pairs

    async def traced_produce(**kwargs: Any) -> Any:
        calls.append("validator")
        return await original_produce(**kwargs)

    async def traced_compare(**kwargs: Any) -> Any:
        calls.append("helper")
        assert "validator" in calls, "helper ran before the validator solved independently"
        return await original_compare(**kwargs)

    validator.produce_report = traced_produce  # type: ignore[method-assign]
    helper.compare_answer_pairs = traced_compare  # type: ignore[method-assign]

    candidate = await _generator_client(_Recorder([_generator_json()])).generate_candidate(
        mode="data-only", source=SOURCE
    )
    report = await validator.produce_report(mode="data-only", source=SOURCE, candidate=candidate)
    original_cmp, variant_cmp = await helper.compare_answer_pairs(
        source_context=SOURCE.text,
        candidate_context=candidate.text,
        source_expected_answer=SOURCE.correct_answer,
        source_solved_answer=report.original_solved_answer or "",
        variant_expected_answer=candidate.correct_answer,
        variant_solved_answer=report.variant_solved_answer or "",
    )

    assert calls == ["validator", "helper"]
    assert original_cmp.result == "equivalent"
    assert variant_cmp.result == "equivalent"

    helper_kwargs = helper_recorder.payloads[0]
    dumped = json.dumps(helper_kwargs)
    assert SOURCE.correct_answer in dumped
    assert candidate.correct_answer in dumped
    assert "60" in dumped
    # Task context separates the two problems.
    assert SOURCE.text in dumped
    assert candidate.text in dumped


@pytest.mark.asyncio
async def test_helper_receives_only_the_owning_validators_answers() -> None:
    first_recorder = _Recorder([_validator_json()])
    second_recorder = _Recorder([_validator_json(variant_solved="ninety-unique-solved-marker")])
    helper_recorder = _Recorder([_helper_json(), _helper_json()])
    first = _validator_client(first_recorder)
    second = _validator_client(second_recorder, second=True)
    helper = _helper_client(helper_recorder)

    candidate = await _generator_client(_Recorder([_generator_json()])).generate_candidate(
        mode="data-only", source=SOURCE
    )
    report1 = await first.produce_report(mode="data-only", source=SOURCE, candidate=candidate)
    report2 = await second.produce_report(mode="data-only", source=SOURCE, candidate=candidate)
    await helper.compare_answer_pairs(
        source_context=SOURCE.text,
        candidate_context=candidate.text,
        source_expected_answer=SOURCE.correct_answer,
        source_solved_answer=report1.original_solved_answer or "",
        variant_expected_answer=candidate.correct_answer,
        variant_solved_answer=report1.variant_solved_answer or "",
    )
    await helper.compare_answer_pairs(
        source_context=SOURCE.text,
        candidate_context=candidate.text,
        source_expected_answer=SOURCE.correct_answer,
        source_solved_answer=report2.original_solved_answer or "",
        variant_expected_answer=candidate.correct_answer,
        variant_solved_answer=report2.variant_solved_answer or "",
    )

    first_payload = json.dumps(helper_recorder.payloads[0])
    second_payload = json.dumps(helper_recorder.payloads[1])
    assert "ninety-unique-solved-marker" not in first_payload
    assert "ninety-unique-solved-marker" in second_payload


@pytest.mark.asyncio
async def test_malformed_json_raises_invalid_response_error() -> None:
    recorder = _Recorder(["not json at all"])
    client = _generator_client(recorder)
    with pytest.raises(VariantVLMError) as exc_info:
        await client.generate_candidate(mode="data-only", source=SOURCE)
    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_incomplete_candidate_short_circuits_before_validators() -> None:
    generator_recorder = _Recorder([_generator_json(problem_type="fill-in-the-blank")])
    validator_recorder = _Recorder([])
    helper_recorder = _Recorder([])

    assessment = await generate_and_validate(
        mode="data-only",
        source=SOURCE,
        generator=_generator_client(generator_recorder),
        validators=[_validator_client(validator_recorder)],
        helper=_helper_client(helper_recorder),
    )

    assert assessment.verdict == "fail"
    assert any(
        "problemType mismatch: source 'short-answer'" in f.evidence for f in assessment.failures
    )
    assert validator_recorder.payloads == []
    assert helper_recorder.payloads == []


@pytest.mark.asyncio
async def test_generator_provider_failure_fails_closed() -> None:
    generator_recorder = _Recorder(
        [APIConnectionError(message="boom", model="gen-model", llm_provider="openai")]
    )
    assessment = await generate_and_validate(
        mode="data-only",
        source=SOURCE,
        generator=_generator_client(generator_recorder),
        validators=[],
        helper=_helper_client(_Recorder([])),
    )
    assert assessment.verdict == "fail"
    assert assessment.failures[0].kind == "provider"
    assert "gen-model" in assessment.failures[0].evidence


@pytest.mark.asyncio
async def test_full_flow_single_validator_pass() -> None:
    assessment = await generate_and_validate(
        mode="data-only",
        source=SOURCE,
        generator=_generator_client(_Recorder([_generator_json()])),
        validators=[_validator_client(_Recorder([_validator_json()]))],
        helper=_helper_client(_Recorder([_helper_json()])),
    )
    assert assessment.verdict == "pass"
    assert assessment.failures == []


@pytest.mark.asyncio
async def test_full_flow_helper_uncertain_fails_closed() -> None:
    assessment = await generate_and_validate(
        mode="data-only",
        source=SOURCE,
        generator=_generator_client(_Recorder([_generator_json()])),
        validators=[_validator_client(_Recorder([_validator_json()]))],
        helper=_helper_client(_Recorder([_helper_json(variant="uncertain")])),
    )
    assert assessment.verdict == "fail"
    assert any(f.kind == "content" for f in assessment.failures)


@pytest.mark.asyncio
async def test_full_flow_two_validators_with_disagreement_fails() -> None:
    disagreeing = _validator_json()
    disagreeing = json.dumps(
        {
            **json.loads(disagreeing),
            "checks": {
                **json.loads(disagreeing)["checks"],
                "difficultyShift": {
                    "category": "materially-harder",
                    "evidence": "much larger numbers",
                },
            },
        }
    )
    assessment = await generate_and_validate(
        mode="data-only",
        source=SOURCE,
        generator=_generator_client(_Recorder([_generator_json()])),
        validators=[
            _validator_client(_Recorder([_validator_json()])),
            _validator_client(_Recorder([disagreeing]), second=True),
        ],
        helper=_helper_client(_Recorder([_helper_json(), _helper_json()])),
    )
    assert assessment.verdict == "fail"
    assert any(
        "validators disagree on difficultyShift" in f.evidence for f in assessment.failures
    )
