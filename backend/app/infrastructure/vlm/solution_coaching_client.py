from __future__ import annotations

import json
import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.vlm.client import (
    FAILURE_CODE_INVALID_RESPONSE,
    FAILURE_CODE_NETWORK,
    FAILURE_CODE_PROVIDER,
    FAILURE_CODE_PROVIDER_REJECTED,
    FAILURE_CODE_TIMEOUT,
)
from app.infrastructure.vlm.solution_coaching_prompts import (
    ENGLISH_COACHING_SYSTEM_PROMPT,
    ENGLISH_SOLUTION_SYSTEM_PROMPT,
    MATH_COACHING_SYSTEM_PROMPT,
    MATH_SOLUTION_SYSTEM_PROMPT,
    build_coaching_user_prompt,
    build_solution_user_prompt,
)


class SolutionCoachingVLMError(Exception):
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
    reasoning_content: str | None = None


class _ChatCompletionChoice(BaseModel):
    index: int
    message: _ChatCompletionMessage


class _ChatCompletionResponse(BaseModel):
    choices: list[_ChatCompletionChoice]


class SolutionVLMRequest(BaseModel):
    problem_text: str
    correct_answer: str
    graph_dsl: str | None = None
    image_url: str | None = None
    image_base64: str | None = None



class SolutionVLMResult(BaseModel):
    model: str
    steps_markdown: str
    final_answer: str
    level_classification: str
    raw_provider_response: dict[str, Any]


class CoachingMessage(BaseModel):
    role: Literal["student", "coach"]
    text: str


class CoachingVLMRequest(BaseModel):
    problem_text: str
    correct_answer: str
    canonical_steps_markdown: str
    canonical_final_answer: str
    level_classification: str
    conversation_history: list[CoachingMessage] = Field(default_factory=list)
    new_message: str


class CoachingVLMResult(BaseModel):
    model: str
    text: str
    whiteboard_dsl: str | None = None
    reasoning_content: str | None = None
    raw_provider_response: dict[str, Any]


class _SolutionProviderPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    steps_markdown: str
    final_answer: str
    level_classification: str = Field(alias="level_classification")

    @model_validator(mode="before")
    @classmethod
    def _migrate_math_level_classification(cls, data: Any) -> Any:
        if isinstance(data, dict) and "math_level_classification" in data and "level_classification" not in data:
            data = dict(data)
            data["level_classification"] = data.pop("math_level_classification")
        return data


class _CoachingProviderPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str
    whiteboard_dsl: str | None = None
    reasoning_content: str | None = None


class _BaseSolutionCoachingVLMClient:
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
            raise SolutionCoachingVLMError(
                "VLM request timed out",
                code=FAILURE_CODE_TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.NetworkError as exc:
            raise SolutionCoachingVLMError(
                "VLM network request failed",
                code=FAILURE_CODE_NETWORK,
                retryable=True,
            ) from exc

        raw_body = self._decode_raw_response(response)

        if 500 <= response.status_code:
            raise SolutionCoachingVLMError(
                f"VLM provider returned server error {response.status_code}",
                code=FAILURE_CODE_PROVIDER,
                retryable=True,
                status_code=response.status_code,
                raw_provider_response=raw_body,
            )

        if 400 <= response.status_code:
            raise SolutionCoachingVLMError(
                f"VLM provider rejected request with status {response.status_code}",
                code=FAILURE_CODE_PROVIDER_REJECTED,
                retryable=False,
                status_code=response.status_code,
                raw_provider_response=raw_body,
            )

        if not isinstance(raw_body, dict):
            raise SolutionCoachingVLMError(
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

        raise SolutionCoachingVLMError(
            "VLM provider response content was not valid JSON",
            code=FAILURE_CODE_INVALID_RESPONSE,
            retryable=False,
            raw_provider_response=content,
        )

    @classmethod
    def _parse_chat_completion_response(cls, raw_body: dict[str, Any]) -> dict[str, Any]:
        try:
            completion = _ChatCompletionResponse.model_validate(raw_body)
        except ValidationError as exc:
            raise SolutionCoachingVLMError(
                "VLM provider response failed chat completion validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            ) from exc

        if not completion.choices:
            raise SolutionCoachingVLMError(
                "VLM provider returned no choices",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            )

        message = completion.choices[0].message
        content = message.content
        if not content:
            raise SolutionCoachingVLMError(
                "VLM provider response content was empty",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            )

        parsed = cls._load_json_content(content)
        if message.reasoning_content is not None:
            parsed["reasoning_content"] = message.reasoning_content
        return parsed


class SolutionVLMClient(_BaseSolutionCoachingVLMClient):
    def __init__(
        self,
        settings: Settings | None = None,
        subject: str = "math",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._subject = subject
        if subject == "english":
            endpoint = self._settings.english_solution_vlm_endpoint
            model = self._settings.english_solution_vlm_model
            api_key = self._settings.english_solution_vlm_api_key
            timeout_seconds = self._settings.english_solution_vlm_timeout_seconds
        else:
            endpoint = self._settings.math_solution_vlm_endpoint
            model = self._settings.math_solution_vlm_model
            api_key = self._settings.math_solution_vlm_api_key
            timeout_seconds = self._settings.math_solution_vlm_timeout_seconds
        super().__init__(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            http_client=http_client,
        )

    async def generate_solution(self, request: SolutionVLMRequest) -> SolutionVLMResult:
        user_prompt = build_solution_user_prompt(
            problem_text=request.problem_text,
            correct_answer=request.correct_answer,
            graph_dsl=request.graph_dsl,
        )
        payload = self._build_payload(
            user_prompt=user_prompt,
            image_url=request.image_url,
            image_base64=request.image_base64,
        )
        raw_provider_response = await self._send_chat_completion(payload)
        parsed = self._validate_response(raw_provider_response, _SolutionProviderPayload)
        return SolutionVLMResult(
            model=self._model,
            steps_markdown=parsed.steps_markdown,
            final_answer=parsed.final_answer,
            level_classification=parsed.level_classification,
            raw_provider_response=raw_provider_response,
        )

    def _build_payload(
        self,
        *,
        user_prompt: str,
        image_url: str | None,
        image_base64: str | None,
    ) -> dict[str, Any]:
        content: list[_ChatMessageContentText | _ChatMessageContentImageUrl] = [
            _ChatMessageContentText(type="text", text=user_prompt)
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

        system_prompt = (
            ENGLISH_SOLUTION_SYSTEM_PROMPT
            if self._subject == "english"
            else MATH_SOLUTION_SYSTEM_PROMPT
        )
        request = _ChatCompletionRequest(
            model=self._model,
            messages=[
                _ChatMessage(role="system", content=system_prompt),
                _ChatMessage(role="user", content=content),
            ],
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
            raise SolutionCoachingVLMError(
                "Solution VLM response failed schema validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_provider_response,
            ) from exc


def _sanitize_whiteboard_dsl(dsl: str | None) -> str | None:
    if dsl is None:
        return None

    stripped = dsl.strip()
    if not stripped:
        return None

    # Remove markdown code fences: ```js ... ``` or ``` ... ```
    fence_match = re.match(r"^```(?:\w*)\n?(.*?)\n?```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()

    # Strip initBoard calls — board already exists in the sandbox
    stripped = re.sub(r"var\s+\w+\s*=\s*JXG\.JSXGraph\.initBoard\([^)]*\)\s*;?", "", stripped)
    stripped = stripped.strip()

    # Basic validation: must contain board.create or be empty
    if stripped and "board.create" not in stripped:
        return None

    return stripped if stripped else None


class CoachingVLMClient(_BaseSolutionCoachingVLMClient):
    def __init__(
        self,
        settings: Settings | None = None,
        subject: str = "math",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._subject = subject
        if subject == "english":
            endpoint = self._settings.english_coaching_vlm_endpoint
            model = self._settings.english_coaching_vlm_model
            api_key = self._settings.english_coaching_vlm_api_key
            timeout_seconds = self._settings.english_coaching_vlm_timeout_seconds
        else:
            endpoint = self._settings.math_coaching_vlm_endpoint
            model = self._settings.math_coaching_vlm_model
            api_key = self._settings.math_coaching_vlm_api_key
            timeout_seconds = self._settings.math_coaching_vlm_timeout_seconds
        super().__init__(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            http_client=http_client,
        )

    async def send_message(self, request: CoachingVLMRequest) -> CoachingVLMResult:
        history = self._format_history(request.conversation_history)
        user_prompt = build_coaching_user_prompt(
            problem_text=request.problem_text,
            correct_answer=request.correct_answer,
            canonical_steps_markdown=request.canonical_steps_markdown,
            canonical_final_answer=request.canonical_final_answer,
            level_classification=request.level_classification,
            conversation_history=history,
            new_message=request.new_message,
        )
        system_prompt = (
            ENGLISH_COACHING_SYSTEM_PROMPT
            if self._subject == "english"
            else MATH_COACHING_SYSTEM_PROMPT
        )
        payload = _ChatCompletionRequest(
            model=self._model,
            messages=[
                _ChatMessage(role="system", content=system_prompt),
                _ChatMessage(role="user", content=user_prompt),
            ],
        ).model_dump(exclude_none=True)
        raw_provider_response = await self._send_chat_completion(payload)
        parsed = self._validate_response(raw_provider_response, _CoachingProviderPayload)
        return CoachingVLMResult(
            model=self._model,
            text=parsed.text,
            whiteboard_dsl=_sanitize_whiteboard_dsl(parsed.whiteboard_dsl),
            reasoning_content=parsed.reasoning_content,
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
            raise SolutionCoachingVLMError(
                "Coaching VLM response failed schema validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_provider_response,
            ) from exc
