from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.llm.prompts import (
    COACHING_PROMPT_VERSION,
    SOLUTION_PROMPT_VERSION,
    build_coaching_prompt,
    build_solution_prompt,
)

FAILURE_CODE_TIMEOUT = "llm-timeout"
FAILURE_CODE_NETWORK = "llm-network-error"
FAILURE_CODE_PROVIDER = "llm-provider-error"
FAILURE_CODE_PROVIDER_REJECTED = "llm-provider-rejected"
FAILURE_CODE_INVALID_RESPONSE = "llm-invalid-response"


class LLMClientError(Exception):
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


class SolutionLLMRequest(BaseModel):
    problem_text: str
    correct_answer: str
    graph_dsl: str | None = None
    image_url: str | None = None
    image_base64: str | None = None



class SolutionLLMResult(BaseModel):
    prompt_version: str
    model: str
    steps_markdown: str
    final_answer: str
    math_level_classification: str
    raw_provider_response: dict[str, Any]


class CoachingMessage(BaseModel):
    role: Literal["student", "coach"]
    text: str


class CoachingLLMRequest(BaseModel):
    problem_text: str
    correct_answer: str
    canonical_steps_markdown: str
    canonical_final_answer: str
    math_level_classification: str
    conversation_history: list[CoachingMessage] = Field(default_factory=list)
    new_message: str
    student_answer: str | None = None
    judgement: str | None = None


class CoachingLLMResult(BaseModel):
    prompt_version: str
    model: str
    text: str
    whiteboard_dsl: str | None = None
    raw_provider_response: dict[str, Any]


class _SolutionProviderPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    steps_markdown: str
    final_answer: str
    math_level_classification: str


class _CoachingProviderPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str
    whiteboard_dsl: str | None = None


class _BaseLLMClient:
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

    async def _send_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.http_client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMClientError(
                "LLM request timed out",
                code=FAILURE_CODE_TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.NetworkError as exc:
            raise LLMClientError(
                "LLM network request failed",
                code=FAILURE_CODE_NETWORK,
                retryable=True,
            ) from exc

        raw_body = self._decode_raw_response(response)

        if 500 <= response.status_code:
            raise LLMClientError(
                f"LLM provider returned server error {response.status_code}",
                code=FAILURE_CODE_PROVIDER,
                retryable=True,
                status_code=response.status_code,
                raw_provider_response=raw_body,
            )

        if 400 <= response.status_code:
            raise LLMClientError(
                f"LLM provider rejected request with status {response.status_code}",
                code=FAILURE_CODE_PROVIDER_REJECTED,
                retryable=False,
                status_code=response.status_code,
                raw_provider_response=raw_body,
            )

        if not isinstance(raw_body, dict):
            raise LLMClientError(
                "LLM provider response must be a JSON object",
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
        if len(lines) < 2:
            return stripped

        if not lines[0].strip().startswith("```") or lines[-1].strip() != "```":
            return stripped

        return "\n".join(lines[1:-1]).strip()

    @classmethod
    def _load_json_content(cls, content: str) -> dict[str, Any]:
        candidates = [cls._strip_json_code_fences(content), content.strip()]
        raw = content.strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(raw[start : end + 1])

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        raise LLMClientError(
            "LLM provider response content was not valid JSON",
            code=FAILURE_CODE_INVALID_RESPONSE,
            retryable=False,
            raw_provider_response=content,
        )

    @classmethod
    def _parse_chat_completion_response(cls, raw_body: dict[str, Any]) -> dict[str, Any]:
        try:
            completion = _ChatCompletionResponse.model_validate(raw_body)
        except ValidationError as exc:
            raise LLMClientError(
                "LLM provider response failed chat completion validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            ) from exc

        if not completion.choices:
            raise LLMClientError(
                "LLM provider returned no choices",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            )

        content = completion.choices[0].message.content
        if not content:
            raise LLMClientError(
                "LLM provider response content was empty",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            )

        return cls._load_json_content(content)


class SolutionLLMClient(_BaseLLMClient):
    def __init__(self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_settings()
        super().__init__(
            endpoint=self._settings.solution_llm_endpoint,
            model=self._settings.solution_llm_model,
            api_key=self._settings.solution_llm_api_key,
            timeout_seconds=self._settings.vlm_timeout_seconds,
            http_client=http_client,
        )

    async def generate_solution(self, request: SolutionLLMRequest) -> SolutionLLMResult:
        prompt = build_solution_prompt(
            problem_text=request.problem_text,
            correct_answer=request.correct_answer,
            graph_dsl=request.graph_dsl,
        )
        payload = self._build_payload(
            prompt=prompt,
            image_url=request.image_url,
            image_base64=request.image_base64,
        )
        raw_provider_response = await self._send_chat_completion(payload)
        parsed = self._validate_response(raw_provider_response, _SolutionProviderPayload)
        return SolutionLLMResult(
            prompt_version=SOLUTION_PROMPT_VERSION,
            model=self._model,
            steps_markdown=parsed.steps_markdown,
            final_answer=parsed.final_answer,
            math_level_classification=parsed.math_level_classification,
            raw_provider_response=raw_provider_response,
        )

    def _build_payload(
        self,
        *,
        prompt: str,
        image_url: str | None,
        image_base64: str | None,
    ) -> dict[str, Any]:
        content: list[_ChatMessageContentText | _ChatMessageContentImageUrl] = [
            _ChatMessageContentText(type="text", text=prompt)
        ]
        if image_base64:
            content.append(
                _ChatMessageContentImageUrl(
                    type="image_url",
                    image_url={"url": f"data:image/png;base64,{image_base64}"},
                )
            )
        elif image_url:
            content.append(
                _ChatMessageContentImageUrl(type="image_url", image_url={"url": image_url})
            )

        request = _ChatCompletionRequest(
            model=self._model,
            messages=[_ChatMessage(role="user", content=content)],
        )
        return request.model_dump(exclude_none=True)

    @staticmethod
    def _validate_response(
        raw_provider_response: dict[str, Any],
        model_class: type[_SolutionProviderPayload],
    ) -> _SolutionProviderPayload:
        try:
            return model_class.model_validate(raw_provider_response)
        except ValidationError as exc:
            raise LLMClientError(
                "Solution LLM response failed schema validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_provider_response,
            ) from exc


class CoachingLLMClient(_BaseLLMClient):
    def __init__(self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_settings()
        super().__init__(
            endpoint=self._settings.coaching_llm_endpoint,
            model=self._settings.coaching_llm_model,
            api_key=self._settings.coaching_llm_api_key,
            timeout_seconds=self._settings.vlm_timeout_seconds,
            http_client=http_client,
        )

    async def send_message(self, request: CoachingLLMRequest) -> CoachingLLMResult:
        history = self._format_history(request.conversation_history)
        prompt = build_coaching_prompt(
            problem_text=request.problem_text,
            correct_answer=request.correct_answer,
            canonical_steps_markdown=request.canonical_steps_markdown,
            canonical_final_answer=request.canonical_final_answer,
            math_level_classification=request.math_level_classification,
            student_answer=request.student_answer,
            judgement=request.judgement,
            conversation_history=history,
            new_message=request.new_message,
        )
        payload = _ChatCompletionRequest(
            model=self._model,
            messages=[_ChatMessage(role="user", content=prompt)],
        ).model_dump(exclude_none=True)
        raw_provider_response = await self._send_chat_completion(payload)
        parsed = self._validate_response(raw_provider_response, _CoachingProviderPayload)
        return CoachingLLMResult(
            prompt_version=COACHING_PROMPT_VERSION,
            model=self._model,
            text=parsed.text,
            whiteboard_dsl=parsed.whiteboard_dsl,
            raw_provider_response=raw_provider_response,
        )

    @staticmethod
    def _format_history(history: list[CoachingMessage]) -> str:
        if not history:
            return "无历史对话。"
        return "\n".join(f"{message.role}: {message.text}" for message in history)

    @staticmethod
    def _validate_response(
        raw_provider_response: dict[str, Any],
        model_class: type[_CoachingProviderPayload],
    ) -> _CoachingProviderPayload:
        try:
            return model_class.model_validate(raw_provider_response)
        except ValidationError as exc:
            raise LLMClientError(
                "Coaching LLM response failed schema validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_provider_response,
            ) from exc
