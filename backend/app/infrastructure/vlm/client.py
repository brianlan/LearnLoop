from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.vlm.prompts import (
    EXTRACTION_PROMPT_TEMPLATE,
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SCHEMA_VERSION,
    GRADING_PROMPT_TEMPLATE,
    GRADING_PROMPT_VERSION,
    GRADING_SCHEMA_VERSION,
)

ProblemType = Literal["single-choice", "multi-choice", "fill-in-the-blank", "short-answer"]

FAILURE_CODE_TIMEOUT = "vlm-timeout"
FAILURE_CODE_NETWORK = "vlm-network-error"
FAILURE_CODE_PROVIDER = "vlm-provider-error"
FAILURE_CODE_PROVIDER_REJECTED = "vlm-provider-rejected"
FAILURE_CODE_INVALID_RESPONSE = "vlm-invalid-response"
FAILURE_CODE_STALE_PREVIEW = "vlm-stale-preview-timeout"


class VLMError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        status_code: int | None = None,
        raw_provider_response: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.raw_provider_response = raw_provider_response


class _RequestBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_type: str = Field(alias="requestType")
    model: str
    prompt_version: str = Field(alias="promptVersion")
    schema_version: str = Field(alias="schemaVersion")
    prompt: str
    image_url: str | None = Field(default=None, alias="imageUrl")
    image_base64: str | None = Field(default=None, alias="imageBase64")

    @model_validator(mode="after")
    def validate_image_reference(self) -> _RequestBase:
        if not self.image_url and not self.image_base64:
            raise ValueError("either imageUrl or imageBase64 is required")
        return self


class ExtractionRequest(_RequestBase):
    request_type: Literal["ingestion"] = Field(default="ingestion", alias="requestType")
    expected_response_schema: dict[str, Any] = Field(alias="expectedResponseSchema")


class GradingRequest(_RequestBase):
    request_type: Literal["short-answer-grading"] = Field(
        default="short-answer-grading",
        alias="requestType",
    )
    user_answer: str = Field(alias="userAnswer")
    correct_answer: str = Field(alias="correctAnswer")
    expected_response_schema: dict[str, Any] = Field(alias="expectedResponseSchema")


class _ProviderMetadataModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class _ExtractionProviderPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str
    problem_type: ProblemType = Field(alias="problemType")
    graph_dsl: str | None = Field(default=None, alias="graphDsl")
    provider_metadata: dict[str, Any] = Field(default_factory=dict, alias="providerMetadata")


class _GradingProviderPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    is_correct: bool = Field(alias="isCorrect")
    feedback: str
    provider_metadata: dict[str, Any] = Field(default_factory=dict, alias="providerMetadata")


class ExtractionResult(BaseModel):
    request_type: Literal["ingestion"]
    model: str
    prompt_version: str
    schema_version: str
    text: str
    problem_type: ProblemType | None
    graph_dsl: str | None
    provider_metadata: dict[str, Any]
    raw_provider_response: dict[str, Any]


class GradingResult(BaseModel):
    request_type: Literal["short-answer-grading"]
    model: str
    prompt_version: str
    schema_version: str
    is_correct: bool
    feedback: str
    provider_metadata: dict[str, Any]
    raw_provider_response: dict[str, Any]


class VLMClient:
    def __init__(self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_settings()
        self._http_client = http_client
        self._owns_client = http_client is None

    @property
    def http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._settings.vlm_endpoint,
                timeout=self._settings.vlm_timeout_seconds,
                headers={
                    "Authorization": f"Bearer {self._settings.vlm_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._http_client

    async def aclose(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def extract(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
    ) -> ExtractionResult:
        request = ExtractionRequest(
            model=self._settings.vlm_model,
            promptVersion=EXTRACTION_PROMPT_VERSION,
            schemaVersion=EXTRACTION_SCHEMA_VERSION,
            prompt=EXTRACTION_PROMPT_TEMPLATE,
            imageUrl=image_url,
            imageBase64=image_base64,
            expectedResponseSchema={
                "type": "object",
                "required": ["text", "problemType"],
                "properties": {
                    "text": {"type": "string"},
                    "problemType": {"type": "string"},
                    "graphDsl": {"type": ["string", "null"]},
                    "providerMetadata": {"type": "object"},
                },
            },
        )
        raw_provider_response = await self._send_request(request)
        payload = self._validate_extraction_response(raw_provider_response)
        return ExtractionResult(
            request_type=request.request_type,
            model=request.model,
            prompt_version=request.prompt_version,
            schema_version=request.schema_version,
            text=payload.text,
            problem_type=payload.problem_type,
            graph_dsl=payload.graph_dsl,
            provider_metadata=payload.provider_metadata,
            raw_provider_response=raw_provider_response,
        )

    async def grade_short_answer(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
        user_answer: str,
        correct_answer: str,
    ) -> GradingResult:
        request = GradingRequest(
            model=self._settings.vlm_model,
            promptVersion=GRADING_PROMPT_VERSION,
            schemaVersion=GRADING_SCHEMA_VERSION,
            prompt=GRADING_PROMPT_TEMPLATE,
            imageUrl=image_url,
            imageBase64=image_base64,
            userAnswer=user_answer,
            correctAnswer=correct_answer,
            expectedResponseSchema={
                "type": "object",
                "required": ["isCorrect", "feedback"],
                "properties": {
                    "isCorrect": {"type": "boolean"},
                    "feedback": {"type": "string"},
                    "providerMetadata": {"type": "object"},
                },
            },
        )
        raw_provider_response = await self._send_request(request)
        payload = self._validate_grading_response(raw_provider_response)
        return GradingResult(
            request_type=request.request_type,
            model=request.model,
            prompt_version=request.prompt_version,
            schema_version=request.schema_version,
            is_correct=payload.is_correct,
            feedback=payload.feedback,
            provider_metadata=payload.provider_metadata,
            raw_provider_response=raw_provider_response,
        )

    async def _send_request(self, request: _RequestBase) -> dict[str, Any]:
        try:
            response = await self.http_client.post("", json=request.model_dump(by_alias=True, exclude_none=True))
        except httpx.TimeoutException as exc:
            raise VLMError(
                "VLM request timed out",
                code=FAILURE_CODE_TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.NetworkError as exc:
            raise VLMError(
                "VLM network request failed",
                code=FAILURE_CODE_NETWORK,
                retryable=True,
            ) from exc

        raw_body = self._decode_raw_response(response)

        if 500 <= response.status_code:
            raise VLMError(
                f"VLM provider returned server error {response.status_code}",
                code=FAILURE_CODE_PROVIDER,
                retryable=True,
                status_code=response.status_code,
                raw_provider_response=raw_body,
            )

        if 400 <= response.status_code:
            raise VLMError(
                f"VLM provider rejected request with status {response.status_code}",
                code=FAILURE_CODE_PROVIDER_REJECTED,
                retryable=False,
                status_code=response.status_code,
                raw_provider_response=raw_body,
            )

        if not isinstance(raw_body, dict):
            raise VLMError(
                "VLM provider response must be a JSON object",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                status_code=response.status_code,
                raw_provider_response=raw_body,
            )

        return raw_body

    @staticmethod
    def _decode_raw_response(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text

    @staticmethod
    def _validate_extraction_response(
        raw_provider_response: dict[str, Any],
    ) -> _ExtractionProviderPayload:
        try:
            return _ExtractionProviderPayload.model_validate(raw_provider_response)
        except ValidationError as exc:
            raise VLMError(
                "VLM provider response failed schema validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_provider_response,
            ) from exc

    @staticmethod
    def _validate_grading_response(
        raw_provider_response: dict[str, Any],
    ) -> _GradingProviderPayload:
        try:
            return _GradingProviderPayload.model_validate(raw_provider_response)
        except ValidationError as exc:
            raise VLMError(
                "VLM provider response failed schema validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_provider_response,
            ) from exc


def recover_stale_preview(
    preview: Mapping[str, Any],
    *,
    now: datetime | None = None,
    extracting_window_seconds: float | None = None,
) -> dict[str, Any] | None:
    status = preview.get("status")
    if status != "extracting":
        return None

    current_time = now or datetime.now(UTC)
    extraction = deepcopy(dict(preview.get("extraction", {})))
    started_at = extraction.get("requestStartedAt") or preview.get("updatedAt") or preview.get("createdAt")
    if not isinstance(started_at, datetime):
        return None

    window = timedelta(seconds=extracting_window_seconds if extracting_window_seconds is not None else get_settings().vlm_timeout_seconds)
    if current_time - started_at <= window:
        return None

    recovered_preview = deepcopy(dict(preview))
    recovered_extraction = deepcopy(dict(recovered_preview.get("extraction", {})))
    recovered_extraction.update(
        {
            "success": False,
            "requestFinishedAt": current_time,
            "failureCode": FAILURE_CODE_STALE_PREVIEW,
            "failureMessage": "Preview extraction exceeded the configured extracting window.",
        }
    )
    recovered_preview["status"] = "vlm-failed"
    recovered_preview["extraction"] = recovered_extraction
    recovered_preview["updatedAt"] = current_time
    return recovered_preview
