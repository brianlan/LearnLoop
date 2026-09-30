"""Text-only VLM clients for math variant generation and validation
(issue #612).

Reuses ``BaseVLMClient`` transports (chat + Responses), the existing error
taxonomy and the helper profile configuration. All requests are text-only:
no image is ever attached, and blind validators never receive expected
answers, images, other validators' reports or helper output.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Callable, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.ingestion.variation import (
    AnswerComparison,
    AssessmentFailure,
    ProblemContent,
    VariantAssessment,
    VariantCandidate,
    VariantGenerationResult,
    VariantMode,
    ValidatorReport,
    assess_variant,
    check_candidate,
)
from app.infrastructure.config.settings import Settings
from app.infrastructure.vlm._models import _ChatCompletionRequest, _ChatMessage
from app.infrastructure.vlm.base_client import (
    FAILURE_CODE_INVALID_RESPONSE,
    BaseVLMClient,
    BaseVLMError,
)
from app.infrastructure.vlm.variant_prompts import (
    VARIANT_GENERATOR_SYSTEM_PROMPT,
    VARIANT_HELPER_SYSTEM_PROMPT,
    VARIANT_VALIDATOR_SYSTEM_PROMPT,
    build_variant_generator_user_prompt,
    build_variant_helper_user_prompt,
    build_variant_validator_user_prompt,
)


class VariantVLMError(BaseVLMError):
    pass


# Non-transport failure code: the profile itself is misconfigured. Never
# retryable and never a substitute for a provider call.
FAILURE_CODE_PROFILE_INVALID = "vlm-profile-invalid"

# Placeholder used by the settings defaults and .env.example for unconfigured
# profiles; endpoints on the reserved .invalid TLD are equally non-configured.
_PROFILE_PLACEHOLDER = "replace-me"


def _profile_unconfigured(value: str | None) -> bool:
    cleaned = (value or "").strip()
    if not cleaned or cleaned == _PROFILE_PLACEHOLDER:
        return True
    # Endpoint defaults live on the reserved .invalid TLD but carry URL paths
    # (e.g. https://example-…-provider.invalid/api), so judge the hostname
    # label; non-URL values (model/api key) fall back to the raw suffix.
    host = urlparse(cleaned).hostname or ""
    if host:
        return host.lower().endswith(".invalid")
    return cleaned.lower().endswith(".invalid")


class _ProviderPayload(BaseModel):
    # ponytail: coerce_numbers_to_str lets models emit numeric JSON answers
    # (8100) where the schema requires strings; bool is never coerced.
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)


class _VariantCandidateProviderPayload(_ProviderPayload):
    text: str
    problem_type: str = Field(alias="problemType")
    graph_dsl: str | None = Field(default=None, alias="graphDsl")
    correct_answer: str = Field(alias="correctAnswer")


class _CheckProviderPayload(_ProviderPayload):
    category: str
    evidence: str = ""


class _ValidatorProviderPayload(_ProviderPayload):
    original_solved_answer: str | None = Field(default=None, alias="originalSolvedAnswer")
    variant_solved_answer: str | None = Field(default=None, alias="variantSolvedAnswer")
    original_solution_summary: str = Field(alias="originalSolutionSummary")
    variant_solution_summary: str = Field(alias="variantSolutionSummary")
    checks: dict[str, _CheckProviderPayload] = Field(default_factory=dict)


class _HelperPairProviderPayload(_ProviderPayload):
    result: Literal["equivalent", "different", "uncertain"]
    evidence: str = ""


class _HelperProviderPayload(_ProviderPayload):
    original: _HelperPairProviderPayload
    variant: _HelperPairProviderPayload


def _failure_kind(error: BaseVLMError) -> Literal["provider", "invalid-response"]:
    return (
        "invalid-response"
        if getattr(error, "code", None) == FAILURE_CODE_INVALID_RESPONSE
        else "provider"
    )


class _TextOnlyVLMClient(BaseVLMClient):
    """Shared text-only request plumbing for the three variant roles."""

    async def _send_text_request(
        self, *, system_prompt: str, user_prompt: str
    ) -> dict[str, Any]:
        if self._api_mode == "responses":
            payload = {
                "instructions": system_prompt,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_prompt}],
                    }
                ],
                "text": {"format": {"type": "json_object"}},
            }
            raw_body = await self._send_responses_request(payload)
            output_text = raw_body.get("output_text")
            if not output_text:
                raise self._make_error(
                    "VLM provider response output_text was empty",
                    code=FAILURE_CODE_INVALID_RESPONSE,
                    retryable=False,
                    raw_provider_response=raw_body,
                )
            content, _reasoning = self._strip_thinking_content(output_text)
            return self._load_json_content(content)
        else:
            chat_request = _ChatCompletionRequest(
                model=self._model,
                messages=[
                    _ChatMessage(role="system", content=system_prompt),
                    _ChatMessage(
                        role="user",
                        content=[{"type": "text", "text": user_prompt}],
                    ),
                ],
            )
            raw_body = await self._send_chat_completion(chat_request.model_dump(exclude_none=True))
            # The base parser validates the chat shape, extracts the content,
            # strips thinking blocks and parses the JSON payload.
            return self._parse_chat_completion_response(raw_body)

    def _validate_payload(
        self,
        raw_provider_response: dict[str, Any],
        model_class: type[_ProviderPayload],
    ) -> _ProviderPayload:
        try:
            return model_class.model_validate(raw_provider_response)
        except ValidationError as exc:
            raise VariantVLMError(
                f"Variant VLM response failed schema validation: {exc}",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_provider_response,
            ) from exc

    @property
    def identity(self) -> dict[str, str]:
        return {"provider": self._provider, "model": self._model}


class VariantGeneratorVLMClient(_TextOnlyVLMClient):
    async def generate_candidate(
        self,
        *,
        mode: VariantMode,
        source: ProblemContent,
    ) -> VariantCandidate:
        user_prompt = build_variant_generator_user_prompt(
            mode=mode,
            source_text=source.text,
            source_problem_type=source.problem_type,
            source_subject=source.subject,
            source_graph_dsl=source.graph_dsl,
            source_correct_answer=source.correct_answer,
        )
        parsed = await self._send_text_request(
            system_prompt=VARIANT_GENERATOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        payload = self._validate_payload(parsed, _VariantCandidateProviderPayload)
        # The subject is inherited server-side; the provider payload schema has
        # no subject field, so the model can never generate or change it.
        return VariantCandidate(
            text=payload.text,
            problemType=payload.problem_type,
            subject=source.subject,
            graphDsl=payload.graph_dsl,
            correctAnswer=payload.correct_answer,
            generator=self.identity,  # type: ignore[arg-type]
        )


class VariantValidatorVLMClient(_TextOnlyVLMClient):
    async def produce_report(
        self,
        *,
        mode: VariantMode,
        source: ProblemContent,
        candidate: VariantCandidate,
    ) -> ValidatorReport:
        user_prompt = build_variant_validator_user_prompt(
            mode=mode,
            source_text=source.text,
            source_problem_type=source.problem_type,
            source_graph_dsl=source.graph_dsl,
            candidate_text=candidate.text,
            candidate_problem_type=candidate.problem_type,
            candidate_graph_dsl=candidate.graph_dsl,
        )
        parsed = await self._send_text_request(
            system_prompt=VARIANT_VALIDATOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        payload = self._validate_payload(parsed, _ValidatorProviderPayload)
        return ValidatorReport(
            validatorModel=self.identity,  # type: ignore[arg-type]
            originalSolvedAnswer=payload.original_solved_answer,
            variantSolvedAnswer=payload.variant_solved_answer,
            originalSolutionSummary=payload.original_solution_summary,
            variantSolutionSummary=payload.variant_solution_summary,
            checks={
                name: {"category": check.category, "evidence": check.evidence}
                for name, check in payload.checks.items()
            },
        )


class VariantHelperVLMClient(_TextOnlyVLMClient):
    async def compare_answer_pairs(
        self,
        *,
        source_context: dict[str, Any],
        candidate_context: dict[str, Any],
        source_expected_answer: str,
        source_solved_answer: str,
        variant_expected_answer: str,
        variant_solved_answer: str,
    ) -> tuple[AnswerComparison, AnswerComparison]:
        user_prompt = build_variant_helper_user_prompt(
            source_context=source_context,
            candidate_context=candidate_context,
            source_expected_answer=source_expected_answer,
            source_solved_answer=source_solved_answer,
            variant_expected_answer=variant_expected_answer,
            variant_solved_answer=variant_solved_answer,
        )
        parsed = await self._send_text_request(
            system_prompt=VARIANT_HELPER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        payload = self._validate_payload(parsed, _HelperProviderPayload)
        return (
            AnswerComparison(result=payload.original.result, evidence=payload.original.evidence),
            AnswerComparison(result=payload.variant.result, evidence=payload.variant.evidence),
        )


def _require_profile_settings(
    name: str, *, endpoint: str | None, model: str | None, api_key: str | None
) -> None:
    """Fail clearly at construction time when a profile is not configured.

    Without this, a placeholder/default profile would surface as a confusing
    provider request failure (or silently fall back to another role's model)
    instead of the required configuration error.
    """
    missing = [
        field
        for field, value in (("endpoint", endpoint), ("model", model), ("api_key", api_key))
        if _profile_unconfigured(value)
    ]
    if missing:
        raise VariantVLMError(
            f"Variant VLM profile '{name}' is missing required configuration: "
            f"{', '.join(missing)}",
            code=FAILURE_CODE_PROFILE_INVALID,
            retryable=False,
        )


def build_variant_generator_vlm_client(
    settings: Settings,
    completion_fn: Callable[..., Any] | None = None,
    responses_fn: Callable[..., Any] | None = None,
) -> VariantGeneratorVLMClient:
    _require_profile_settings(
        "variant_generator_vlm_*",
        endpoint=settings.variant_generator_vlm_endpoint,
        model=settings.variant_generator_vlm_model,
        api_key=settings.variant_generator_vlm_api_key,
    )
    return VariantGeneratorVLMClient(
        endpoint=settings.variant_generator_vlm_endpoint,
        model=settings.variant_generator_vlm_model,
        api_key=settings.variant_generator_vlm_api_key,
        timeout_seconds=settings.variant_generator_vlm_timeout_seconds,
        provider=settings.variant_generator_vlm_provider,
        api_mode=settings.variant_generator_vlm_api_mode,
        completion_fn=completion_fn,
        responses_fn=responses_fn,
        error_factory=VariantVLMError,
    )


def build_variant_validator_vlm_client(
    settings: Settings,
    *,
    second: bool = False,
    completion_fn: Callable[..., Any] | None = None,
    responses_fn: Callable[..., Any] | None = None,
) -> VariantValidatorVLMClient | None:
    """Build a validator client; the second validator is optional.

    ``second=True`` returns ``None`` when the whole ``validator2`` profile is
    unconfigured (single-validator mode). A partially configured profile is a
    configuration error, not an intentional omission, and still raises so a
    typo can never silently drop the independent second check.
    """
    if second:
        if all(
            _profile_unconfigured(value)
            for value in (
                settings.variant_validator2_vlm_endpoint,
                settings.variant_validator2_vlm_model,
                settings.variant_validator2_vlm_api_key,
            )
        ):
            return None
        _require_profile_settings(
            "variant_validator2_vlm_*",
            endpoint=settings.variant_validator2_vlm_endpoint,
            model=settings.variant_validator2_vlm_model,
            api_key=settings.variant_validator2_vlm_api_key,
        )
        return VariantValidatorVLMClient(
            endpoint=settings.variant_validator2_vlm_endpoint,
            model=settings.variant_validator2_vlm_model,
            api_key=settings.variant_validator2_vlm_api_key,
            timeout_seconds=settings.variant_validator2_vlm_timeout_seconds,
            provider=settings.variant_validator2_vlm_provider,
            api_mode=settings.variant_validator2_vlm_api_mode,
            completion_fn=completion_fn,
            responses_fn=responses_fn,
            error_factory=VariantVLMError,
        )
    _require_profile_settings(
        "variant_validator_vlm_*",
        endpoint=settings.variant_validator_vlm_endpoint,
        model=settings.variant_validator_vlm_model,
        api_key=settings.variant_validator_vlm_api_key,
    )
    return VariantValidatorVLMClient(
        endpoint=settings.variant_validator_vlm_endpoint,
        model=settings.variant_validator_vlm_model,
        api_key=settings.variant_validator_vlm_api_key,
        timeout_seconds=settings.variant_validator_vlm_timeout_seconds,
        provider=settings.variant_validator_vlm_provider,
        api_mode=settings.variant_validator_vlm_api_mode,
        completion_fn=completion_fn,
        responses_fn=responses_fn,
        error_factory=VariantVLMError,
    )


def build_variant_helper_vlm_client(
    settings: Settings,
    completion_fn: Callable[..., Any] | None = None,
    responses_fn: Callable[..., Any] | None = None,
) -> VariantHelperVLMClient:
    _require_profile_settings(
        "helper_vlm_*",
        endpoint=settings.helper_vlm_endpoint,
        model=settings.helper_vlm_model,
        api_key=settings.helper_vlm_api_key,
    )
    return VariantHelperVLMClient(
        endpoint=settings.helper_vlm_endpoint,
        model=settings.helper_vlm_model,
        api_key=settings.helper_vlm_api_key,
        timeout_seconds=settings.helper_vlm_timeout_seconds,
        provider=settings.helper_vlm_provider,
        api_mode=settings.helper_vlm_api_mode,
        completion_fn=completion_fn,
        responses_fn=responses_fn,
        error_factory=VariantVLMError,
    )


def _problem_context(problem: ProblemContent | VariantCandidate) -> dict[str, Any]:
    """Full task context for helper comparisons (multi-part/diagram-aware)."""
    return {
        "text": problem.text,
        "problemType": problem.problem_type,
        "graphDsl": problem.graph_dsl,
    }


async def generate_and_validate(
    *,
    mode: VariantMode,
    source: ProblemContent,
    generator: VariantGeneratorVLMClient,
    validators: Sequence[VariantValidatorVLMClient],
    helper: VariantHelperVLMClient,
    candidate: VariantCandidate | None = None,
) -> VariantGenerationResult:
    """The callable interface the later worker crosses.

    Returns the generated candidate, every completed validator report (with
    model identity, independently solved answers and helper comparisons) and
    the deterministic assessment, so approval provenance stays inspectable
    even on failure. Fails closed: generator/validator/helper provider or
    invalid-response failures become non-content failures and the verdict is
    FAIL. Conclusively invalid candidates short-circuit before validator
    calls. A pre-built ``candidate`` skips generation entirely; this exists
    for the deterministic gate samples in scripts/check_variant_models.py.
    """
    if candidate is None:
        try:
            candidate = await generator.generate_candidate(mode=mode, source=source)
        except BaseVLMError as exc:
            return VariantGenerationResult(
                candidate=None,
                reports=[],
                assessment=VariantAssessment(
                    verdict="fail",
                    failures=[
                        AssessmentFailure(
                            kind=_failure_kind(exc),
                            evidence=(
                                f"generator {generator.identity['provider']}/{generator.identity['model']} "
                                f"failed: {exc}"
                            ),
                        )
                    ],
                ),
            )

    cheap_failures = check_candidate(mode, source, candidate)
    if cheap_failures:
        return VariantGenerationResult(
            candidate=candidate,
            reports=[],
            assessment=VariantAssessment(verdict="fail", failures=cheap_failures),
        )

    reports: list[ValidatorReport] = []
    extra_failures: list[AssessmentFailure] = []
    for validator in validators:
        try:
            report = await validator.produce_report(mode=mode, source=source, candidate=candidate)
        except BaseVLMError as exc:
            extra_failures.append(
                AssessmentFailure(
                    kind=_failure_kind(exc),
                    evidence=(
                        f"validator {validator.identity['provider']}/{validator.identity['model']} "
                        f"failed: {exc}"
                    ),
                )
            )
            continue
        if report.original_solved_answer is None or report.variant_solved_answer is None:
            extra_failures.append(
                AssessmentFailure(
                    kind="content",
                    evidence=(
                        f"validator {validator.identity['model']} could not solve one of the "
                        "problems; helper comparison skipped"
                    ),
                )
            )
            reports.append(report)
            continue
        try:
            original_cmp, variant_cmp = await helper.compare_answer_pairs(
                source_context=_problem_context(source),
                candidate_context=_problem_context(candidate),
                source_expected_answer=source.correct_answer,
                source_solved_answer=report.original_solved_answer,
                variant_expected_answer=candidate.correct_answer,
                variant_solved_answer=report.variant_solved_answer,
            )
        except BaseVLMError as exc:
            extra_failures.append(
                AssessmentFailure(
                    kind=_failure_kind(exc),
                    evidence=(
                        f"helper {helper.identity['provider']}/{helper.identity['model']} "
                        f"failed: {exc}"
                    ),
                )
            )
            reports.append(report)
            continue
        report.answer_comparison_original = original_cmp
        report.answer_comparison_variant = variant_cmp
        reports.append(report)

    assessment = assess_variant(
        mode=mode, source=source, candidate=candidate, reports=reports
    )
    if extra_failures:
        assessment = VariantAssessment(
            verdict="fail",
            failures=assessment.failures + extra_failures,
        )
    return VariantGenerationResult(
        candidate=candidate, reports=reports, assessment=assessment
    )
