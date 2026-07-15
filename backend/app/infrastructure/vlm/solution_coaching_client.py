from __future__ import annotations

import json
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain.whiteboard import sanitize_whiteboard_dsl
from app.infrastructure.config.settings import Settings, get_settings
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
)

from app.infrastructure.vlm.solution_coaching_prompts import (
    ENGLISH_COACHING_SYSTEM_PROMPT,
    ENGLISH_SOLUTION_SYSTEM_PROMPT,
    MATH_COACHING_SYSTEM_PROMPT,
    MATH_SOLUTION_SYSTEM_PROMPT,
    build_coaching_user_prompt,
    build_solution_user_prompt,
)

class SolutionCoachingVLMError(BaseVLMError):
    pass


# Response models moved to _models.py


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


class _BaseSolutionCoachingVLMClient(BaseVLMClient):
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        provider: str = "openai",
        api_mode: Literal["chat", "responses"] = "chat",
        completion_fn: Callable[..., Any] | None = None,
        responses_fn: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            provider=provider,
            api_mode=api_mode,
            completion_fn=completion_fn,
            responses_fn=responses_fn,
            error_factory=SolutionCoachingVLMError,
        )

    async def _send_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_body = await super()._send_chat_completion(payload)
        return self._parse_chat_completion_response(raw_body)

    async def _send_responses_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_body = await super()._send_responses_request(payload)
        # Parse the output_text similar to chat completion
        output_text = raw_body.get("output_text")
        if not output_text:
            raise self._make_error(
                "VLM provider response output_text was empty",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            )
        stripped_content, extracted_reasoning = self._strip_thinking_content(output_text)
        parsed = self._load_json_content(stripped_content)
        if extracted_reasoning is not None:
            parsed["reasoning_content"] = extracted_reasoning
        return parsed


class SolutionVLMClient(_BaseSolutionCoachingVLMClient):
    def __init__(
        self,
        settings: Settings | None = None,
        subject: str = "math",
        completion_fn: Callable[..., Any] | None = None,
        responses_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._subject = subject
        if subject == "english":
            endpoint = self._settings.english_solution_vlm_endpoint
            model = self._settings.english_solution_vlm_model
            api_key = self._settings.english_solution_vlm_api_key
            timeout_seconds = self._settings.english_solution_vlm_timeout_seconds
            provider = self._settings.english_solution_vlm_provider
            api_mode = self._settings.english_solution_vlm_api_mode
        else:
            endpoint = self._settings.math_solution_vlm_endpoint
            model = self._settings.math_solution_vlm_model
            api_key = self._settings.math_solution_vlm_api_key
            timeout_seconds = self._settings.math_solution_vlm_timeout_seconds
            provider = self._settings.math_solution_vlm_provider
            api_mode = self._settings.math_solution_vlm_api_mode
        super().__init__(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            provider=provider,
            api_mode=api_mode,
            completion_fn=completion_fn,
            responses_fn=responses_fn,
        )

    async def generate_solution(self, request: SolutionVLMRequest) -> SolutionVLMResult:
        user_prompt = build_solution_user_prompt(
            problem_text=request.problem_text,
            correct_answer=request.correct_answer,
            graph_dsl=request.graph_dsl,
        )
        
        system_prompt = (
            ENGLISH_SOLUTION_SYSTEM_PROMPT
            if self._subject == "english"
            else MATH_SOLUTION_SYSTEM_PROMPT
        )

        if self._api_mode == "responses":
            payload = self._build_responses_payload(
                instructions=system_prompt,
                user_prompt=user_prompt,
                image_url=request.image_url,
                image_base64=request.image_base64,
            )
            raw_provider_response = await self._send_responses_request(payload)
            parsed = self._validate_response(raw_provider_response, _SolutionProviderPayload)
        else:
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

    def _build_responses_payload(
        self,
        *,
        instructions: str,
        user_prompt: str,
        image_url: str | None,
        image_base64: str | None,
    ) -> dict[str, Any]:
        input_items: list[dict[str, Any]] = [
            {"type": "input_text", "text": user_prompt}
        ]
        
        if image_base64:
            input_items.append(
                {"type": "input_image", "image_url": f"data:image/png;base64,{image_base64}"}
            )
        elif image_url:
            input_items.append(
                {"type": "input_image", "image_url": image_url}
            )

        return {
            "instructions": instructions,
            "input": [{"role": "user", "content": input_items}],
            "text": {
                "format": {
                    "type": "json_object",
                }
            },
        }

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




class CoachingVLMClient(_BaseSolutionCoachingVLMClient):
    def __init__(
        self,
        settings: Settings | None = None,
        subject: str = "math",
        completion_fn: Callable[..., Any] | None = None,
        responses_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._subject = subject
        if subject == "english":
            endpoint = self._settings.english_coaching_vlm_endpoint
            model = self._settings.english_coaching_vlm_model
            api_key = self._settings.english_coaching_vlm_api_key
            timeout_seconds = self._settings.english_coaching_vlm_timeout_seconds
            provider = self._settings.english_coaching_vlm_provider
            api_mode = self._settings.english_coaching_vlm_api_mode
        else:
            endpoint = self._settings.math_coaching_vlm_endpoint
            model = self._settings.math_coaching_vlm_model
            api_key = self._settings.math_coaching_vlm_api_key
            timeout_seconds = self._settings.math_coaching_vlm_timeout_seconds
            provider = self._settings.math_coaching_vlm_provider
            api_mode = self._settings.math_coaching_vlm_api_mode
        super().__init__(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            provider=provider,
            api_mode=api_mode,
            completion_fn=completion_fn,
            responses_fn=responses_fn,
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
            raw_provider_response = await self._send_responses_request(payload)
        else:
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
            whiteboard_dsl=sanitize_whiteboard_dsl(parsed.whiteboard_dsl),
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
