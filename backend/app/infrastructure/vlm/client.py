from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.infrastructure.vlm.base_client import (
    FAILURE_CODE_INVALID_RESPONSE,
    FAILURE_CODE_NETWORK,
    FAILURE_CODE_PROVIDER,
    FAILURE_CODE_PROVIDER_REJECTED,
    FAILURE_CODE_TIMEOUT,
    BaseVLMClient,
    BaseVLMError,
)
from app.infrastructure.vlm._models import (
    _ChatCompletionRequest,
    _ChatMessage,
    _ChatMessageContentImageUrl,
    _ChatMessageContentText,
    _ChatCompletionResponse,
)
from app.infrastructure.vlm.prompts import (
    ENGLISH_EXTRACTION_SYSTEM_PROMPT,
    GRADING_SYSTEM_PROMPT,
    HELPER_PROBLEM_DETECTION_SYSTEM_PROMPT,
    HELPER_SUBJECT_CLASSIFICATION_SYSTEM_PROMPT,
    MATH_EXTRACTION_SYSTEM_PROMPT,
    build_extraction_user_prompt,
    build_grading_user_prompt,
    build_problem_detection_user_prompt,
    build_subject_classification_user_prompt,
)

ProblemType = Literal["single-choice", "multi-choice", "fill-in-the-blank", "short-answer"]

FAILURE_CODE_STALE_PREVIEW = "vlm-stale-preview-timeout"


class VLMError(BaseVLMError):
    pass


class _RequestBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_type: str = Field(alias="requestType")
    model: str
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
    subject: str = "math"
    expected_response_schema: dict[str, Any] = Field(alias="expectedResponseSchema")


class ClassificationRequest(_RequestBase):
    request_type: Literal["subject-classification"] = Field(
        default="subject-classification",
        alias="requestType",
    )
    expected_response_schema: dict[str, Any] = Field(alias="expectedResponseSchema")


class DetectionRequest(_RequestBase):
    request_type: Literal["problem-box-detection"] = Field(
        default="problem-box-detection",
        alias="requestType",
    )
    expected_response_schema: dict[str, Any] = Field(alias="expectedResponseSchema")


class _ProviderMetadataModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class _ExtractionProviderPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str
    problem_type: ProblemType = Field(alias="problemType")
    graph_dsl: str | None = Field(default=None, alias="graphDsl")
    correct_answer: str | None = Field(default=None, alias="correctAnswer")
    provider_metadata: dict[str, Any] = Field(default_factory=dict, alias="providerMetadata")


class _ClassificationProviderPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    subject: Literal["math", "english"]
    confidence: float = Field(ge=0, le=1)
    reason: str
    provider_metadata: dict[str, Any] = Field(default_factory=dict, alias="providerMetadata")


class ProblemBox(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class _DetectionProviderPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    subject: Literal["math", "english"]
    boxes: list[ProblemBox]
    provider_metadata: dict[str, Any] = Field(default_factory=dict, alias="providerMetadata")


class _GradingProviderPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    is_correct: bool = Field(alias="isCorrect")
    feedback: str
    provider_metadata: dict[str, Any] = Field(default_factory=dict, alias="providerMetadata")


# Response models moved to _models.py


class ExtractionResult(BaseModel):
    request_type: Literal["ingestion"]
    model: str
    text: str
    problem_type: ProblemType | None
    graph_dsl: str | None
    correct_answer: str | None = None
    provider_metadata: dict[str, Any]
    raw_provider_response: dict[str, Any]


class ClassificationResult(BaseModel):
    request_type: Literal["subject-classification"]
    model: str
    subject: Literal["math", "english"]
    confidence: float = Field(ge=0, le=1)
    reason: str
    provider_metadata: dict[str, Any]
    raw_provider_response: dict[str, Any]


class DetectionResult(BaseModel):
    request_type: Literal["problem-box-detection"]
    model: str
    subject: Literal["math", "english"]
    boxes: list[ProblemBox]
    provider_metadata: dict[str, Any]
    raw_provider_response: dict[str, Any]


class GradingResult(BaseModel):
    request_type: Literal["short-answer-grading"]
    model: str
    is_correct: bool
    feedback: str
    provider_metadata: dict[str, Any]
    raw_provider_response: dict[str, Any]


class VLMClient(BaseVLMClient):
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
        extraction_system_prompt: str = MATH_EXTRACTION_SYSTEM_PROMPT,
        request_correct_answer: bool = False,
    ) -> None:
        super().__init__(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            http_client=http_client,
            error_factory=VLMError,
        )
        self._extraction_system_prompt = extraction_system_prompt
        self._request_correct_answer = request_correct_answer

    @property
    def model(self) -> str:
        return self._model

    async def extract(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
    ) -> ExtractionResult:
        properties: dict[str, Any] = {
            "text": {"type": "string"},
            "problemType": {"type": "string"},
            "graphDsl": {"type": ["string", "null"]},
            "providerMetadata": {"type": "object"},
        }
        if self._request_correct_answer:
            properties["correctAnswer"] = {"type": ["string", "null"]}
        request = ExtractionRequest(
            model=self._model,
            prompt=self._extraction_system_prompt,
            imageUrl=image_url,
            imageBase64=image_base64,
            expectedResponseSchema={
                "type": "object",
                "required": ["text", "problemType"],
                "properties": properties,
            },
        )
        raw_provider_response = await self._send_request(request)
        payload = self._validate_response(raw_provider_response, _ExtractionProviderPayload)
        return ExtractionResult(
            request_type=request.request_type,
            model=request.model,
            text=payload.text,
            problem_type=payload.problem_type,
            graph_dsl=payload.graph_dsl,
            correct_answer=payload.correct_answer,
            provider_metadata=payload.provider_metadata,
            raw_provider_response=raw_provider_response,
        )

    async def classify_subject(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
    ) -> ClassificationResult:
        request = ClassificationRequest(
            model=self._model,
            prompt=HELPER_SUBJECT_CLASSIFICATION_SYSTEM_PROMPT,
            imageUrl=image_url,
            imageBase64=image_base64,
            expectedResponseSchema={
                "type": "object",
                "required": ["subject", "confidence", "reason"],
                "properties": {
                    "subject": {"type": "string", "enum": ["math", "english"]},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "providerMetadata": {"type": "object"},
                },
            },
        )
        raw_provider_response = await self._send_request(request)
        payload = self._validate_response(raw_provider_response, _ClassificationProviderPayload)
        return ClassificationResult(
            request_type=request.request_type,
            model=request.model,
            subject=payload.subject,
            confidence=payload.confidence,
            reason=payload.reason,
            provider_metadata=payload.provider_metadata,
            raw_provider_response=raw_provider_response,
        )

    async def detect_problem_boxes(
        self,
        *,
        image_url: str | None = None,
        image_base64: str | None = None,
    ) -> DetectionResult:
        request = DetectionRequest(
            model=self._model,
            prompt=HELPER_PROBLEM_DETECTION_SYSTEM_PROMPT,
            imageUrl=image_url,
            imageBase64=image_base64,
            expectedResponseSchema={
                "type": "object",
                "required": ["subject", "boxes"],
                "properties": {
                    "subject": {"type": "string", "enum": ["math", "english"]},
                    "boxes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["x", "y", "width", "height"],
                            "properties": {
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                                "width": {"type": "number"},
                                "height": {"type": "number"},
                            },
                        },
                    },
                    "providerMetadata": {"type": "object"},
                },
            },
        )
        raw_provider_response = await self._send_request(request)
        payload = self._validate_response(raw_provider_response, _DetectionProviderPayload)
        return DetectionResult(
            request_type=request.request_type,
            model=request.model,
            subject=payload.subject,
            boxes=payload.boxes,
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
        subject: str = "math",
    ) -> GradingResult:
        request = GradingRequest(
            model=self._model,
            prompt=GRADING_SYSTEM_PROMPT,
            imageUrl=image_url,
            imageBase64=image_base64,
            problemText=problem_text,
            userAnswer=user_answer,
            correctAnswer=correct_answer,
            subject=subject,
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
            is_correct=payload.is_correct,
            feedback=payload.feedback,
            provider_metadata=payload.provider_metadata,
            raw_provider_response=raw_provider_response,
        )

    async def _send_request(self, request: _RequestBase) -> dict[str, Any]:
        payload = self._build_chat_completion_payload(request)
        raw_body = await self._send_chat_completion(payload)
        return self._parse_chat_completion_response(raw_body)

    # _strip_json_code_fences inherited from BaseVLMClient

    @staticmethod
    def _validate_response(
        raw_provider_response: dict[str, Any],
        model_class: type[BaseModel],
    ) -> BaseModel:
        try:
            return model_class.model_validate(raw_provider_response)
        except ValidationError as exc:
            raise VLMError(
                "VLM provider response failed schema validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_provider_response,
            ) from exc

    def _build_chat_completion_payload(self, request: _RequestBase) -> dict[str, Any]:
        if isinstance(request, GradingRequest):
            user_prompt = build_grading_user_prompt(
                problem_text=request.problem_text,
                user_answer=request.user_answer,
                correct_answer=request.correct_answer,
                subject=request.subject,
                expected_response_schema=request.expected_response_schema,
            )
        elif isinstance(request, ClassificationRequest):
            user_prompt = build_subject_classification_user_prompt(
                expected_response_schema=request.expected_response_schema,
            )
        elif isinstance(request, DetectionRequest):
            user_prompt = build_problem_detection_user_prompt(
                expected_response_schema=request.expected_response_schema,
            )
        else:
            user_prompt = build_extraction_user_prompt(
                expected_response_schema=request.expected_response_schema,
                include_correct_answer=self._request_correct_answer,
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

    def _parse_chat_completion_response(self, raw_body: dict[str, Any]) -> dict[str, Any]:
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

        message = completion.choices[0].message
        content = message.content
        if not content:
            raise VLMError(
                "VLM provider response content was empty",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            )

        stripped_content, extracted_reasoning = self._strip_thinking_content(content)
        parsed = self._load_json_content(stripped_content)
        reasoning_content = message.reasoning_content
        if reasoning_content is None:
            reasoning_content = extracted_reasoning
        if reasoning_content is not None:
            parsed["reasoning_content"] = reasoning_content
        return parsed
