from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.infrastructure.vlm.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SCHEMA_VERSION,
    GRADING_SYSTEM_PROMPT,
    GRADING_PROMPT_VERSION,
    GRADING_SCHEMA_VERSION,
    build_extraction_user_prompt,
    build_grading_user_prompt,
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
    problem_text: str = Field(alias="problemText")
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


class _ChatMessageContentText(BaseModel):
    type: Literal["text"]
    text: str


class _ChatMessageContentImageUrl(BaseModel):
    type: Literal["image_url"]
    image_url: dict[str, str]


class _ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: list[_ChatMessageContentText | _ChatMessageContentImageUrl] | str


class _ChatCompletionRequest(BaseModel):
    model: str
    messages: list[_ChatMessage]


class _ChatCompletionMessage(BaseModel):
    role: str
    content: str | None = None


class _ChatCompletionChoice(BaseModel):
    index: int
    message: _ChatCompletionMessage


class _ChatCompletionResponse(BaseModel):
    choices: list[_ChatCompletionChoice]


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
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._owns_client = http_client is None

    @property
    def http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._endpoint,
                timeout=self._timeout_seconds,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
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
            model=self._model,
            promptVersion=EXTRACTION_PROMPT_VERSION,
            schemaVersion=EXTRACTION_SCHEMA_VERSION,
            prompt=EXTRACTION_SYSTEM_PROMPT,
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
        payload = self._validate_response(raw_provider_response, _ExtractionProviderPayload)
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
        problem_text: str,
        user_answer: str,
        correct_answer: str,
    ) -> GradingResult:
        request = GradingRequest(
            model=self._model,
            promptVersion=GRADING_PROMPT_VERSION,
            schemaVersion=GRADING_SCHEMA_VERSION,
            prompt=GRADING_SYSTEM_PROMPT,
            imageUrl=image_url,
            imageBase64=image_base64,
            problemText=problem_text,
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
        payload = self._validate_response(raw_provider_response, _GradingProviderPayload)
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
        payload = self._build_chat_completion_payload(request)
        try:
            response = await self.http_client.post("/chat/completions", json=payload)
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

        return self._parse_chat_completion_response(raw_body)

    @staticmethod
    def _decode_raw_response(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text

    @staticmethod
    def _strip_json_code_fences(content: str) -> str:
        stripped = content.strip()
        if not stripped.startswith("```"):
            return stripped

        lines = stripped.splitlines()
        if not lines:
            return stripped

        first_line = lines[0].strip()
        if first_line not in {"```", "```json"}:
            return stripped

        if len(lines) < 2 or lines[-1].strip() != "```":
            return stripped

        return "\n".join(lines[1:-1]).strip()

    @staticmethod
    def _validate_response(
        raw_provider_response: dict[str, Any],
        model_class: type[_ExtractionProviderPayload | _GradingProviderPayload],
    ) -> _ExtractionProviderPayload | _GradingProviderPayload:
        try:
            return model_class.model_validate(raw_provider_response)
        except ValidationError as exc:
            raise VLMError(
                "VLM provider response failed schema validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_provider_response,
            ) from exc

    @staticmethod
    def _build_chat_completion_payload(request: _RequestBase) -> dict[str, Any]:
        if isinstance(request, GradingRequest):
            user_prompt = build_grading_user_prompt(
                problem_text=request.problem_text,
                user_answer=request.user_answer,
                correct_answer=request.correct_answer,
                expected_response_schema=request.expected_response_schema,
            )
        else:
            user_prompt = build_extraction_user_prompt(
                expected_response_schema=request.expected_response_schema,
            )

        content: list[_ChatMessageContentText | _ChatMessageContentImageUrl] = [
            _ChatMessageContentText(type="text", text=user_prompt)
        ]

        if request.image_base64:
            content.append(
                _ChatMessageContentImageUrl(
                    type="image_url",
                    image_url={"url": f"data:image/png;base64,{request.image_base64}"},
                )
            )
        elif request.image_url:
            content.append(
                _ChatMessageContentImageUrl(type="image_url", image_url={"url": request.image_url})
            )

        chat_request = _ChatCompletionRequest(
            model=request.model,
            messages=[
                _ChatMessage(role="system", content=request.prompt),
                _ChatMessage(role="user", content=content),
            ],
        )
        return chat_request.model_dump(exclude_none=True)

    @staticmethod
    def _parse_chat_completion_response(raw_body: dict[str, Any]) -> dict[str, Any]:
        try:
            completion = _ChatCompletionResponse.model_validate(raw_body)
        except ValidationError as exc:
            raise VLMError(
                "VLM provider response failed chat completion validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            ) from exc

        if not completion.choices:
            raise VLMError(
                "VLM provider returned no choices",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            )

        content = completion.choices[0].message.content
        if not content:
            raise VLMError(
                "VLM provider response content was empty",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            )

        try:
            parsed = json.loads(VLMClient._strip_json_code_fences(content))
        except json.JSONDecodeError as exc:
            raise VLMError(
                "VLM provider content was not valid JSON",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            ) from exc

        if not isinstance(parsed, dict):
            raise VLMError(
                "VLM provider content must decode to a JSON object",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            )

        return parsed
