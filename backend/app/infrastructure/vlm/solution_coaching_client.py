from __future__ import annotations

import json
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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

_ALLOWED_DSL_ELEMENT_TYPES = {
    "point",
    "segment",
    "line",
    "arrow",
    "circle",
    "angle",
    "polygon",
    "text",
    "glider",
    "intersection",
    "midpoint",
    "perpendicular",
}

_ALLOWED_DSL_OPTION_KEYS = {
    "anchorX",
    "anchorY",
    "color",
    "dash",
    "face",
    "fillColor",
    "fillOpacity",
    "fixed",
    "fontSize",
    "highlight",
    "label",
    "name",
    "opacity",
    "radius",
    "showInfobox",
    "size",
    "strokeColor",
    "strokeOpacity",
    "strokeWidth",
    "visible",
    "withLabel",
}

_BLOCKED_DSL_TOKENS = {
    "constructor",
    "document",
    "eval",
    "fetch",
    "for",
    "function",
    "globalThis",
    "if",
    "import",
    "localStorage",
    "new",
    "prototype",
    "return",
    "sessionStorage",
    "setInterval",
    "setTimeout",
    "this",
    "while",
    "window",
    "XMLHttpRequest",
    "__proto__",
}


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
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            provider=provider,
            completion_fn=completion_fn,
            error_factory=SolutionCoachingVLMError,
        )

    async def _send_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_body = await super()._send_chat_completion(payload)
        return self._parse_chat_completion_response(raw_body)


class SolutionVLMClient(_BaseSolutionCoachingVLMClient):
    def __init__(
        self,
        settings: Settings | None = None,
        subject: str = "math",
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._subject = subject
        if subject == "english":
            endpoint = self._settings.english_solution_vlm_endpoint
            model = self._settings.english_solution_vlm_model
            api_key = self._settings.english_solution_vlm_api_key
            timeout_seconds = self._settings.english_solution_vlm_timeout_seconds
            provider = self._settings.english_solution_vlm_provider
        else:
            endpoint = self._settings.math_solution_vlm_endpoint
            model = self._settings.math_solution_vlm_model
            api_key = self._settings.math_solution_vlm_api_key
            timeout_seconds = self._settings.math_solution_vlm_timeout_seconds
            provider = self._settings.math_solution_vlm_provider
        super().__init__(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            provider=provider,
            completion_fn=completion_fn,
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


def _strip_quoted_strings(value: str) -> str:
    return re.sub(r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*" """.strip(), "", value)


def _split_top_level(value: str, delimiter: str) -> list[str] | None:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue

        if char in {"'", '"'}:
            quote = char
        elif char in {"[", "{", "("}:
            depth += 1
        elif char in {"]", "}", ")"}:
            depth -= 1
            if depth < 0:
                return None
        elif char == delimiter and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1

    if quote or depth != 0:
        return None

    last = value[start:].strip()
    if last:
        parts.append(last)
    return parts


def _is_js_string_literal(value: str) -> bool:
    return bool(re.fullmatch(r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*" """.strip(), value))


def _validate_dsl_object(value: str, declared_names: set[str]) -> bool:
    if not value.startswith("{") or not value.endswith("}"):
        return False
    inner = value[1:-1].strip()
    if not inner:
        return True
    entries = _split_top_level(inner, ",")
    if entries is None:
        return False
    for entry in entries:
        pair = _split_top_level(entry, ":")
        if pair is None or len(pair) != 2:
            return False
        key = pair[0].strip().strip("'\"")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key not in _ALLOWED_DSL_OPTION_KEYS:
            return False
        if not _validate_dsl_value(pair[1], declared_names):
            return False
    return True


def _validate_dsl_value(value: str, declared_names: set[str]) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
        return True
    if _is_js_string_literal(stripped):
        return True
    if stripped in {"true", "false", "null"}:
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
        return stripped in declared_names
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1].strip()
        if not inner:
            return True
        items = _split_top_level(inner, ",")
        return items is not None and all(_validate_dsl_value(item, declared_names) for item in items)
    if stripped.startswith("{") and stripped.endswith("}"):
        return _validate_dsl_object(stripped, declared_names)
    return False


def _validate_dsl_create_call(call: str, declared_names: set[str]) -> bool:
    if not call.startswith("board.create(") or not call.endswith(")"):
        return False
    args = _split_top_level(call[len("board.create(") : -1], ",")
    if args is None or len(args) < 2 or len(args) > 3:
        return False
    element_type_arg = args[0].strip()
    if not _is_js_string_literal(element_type_arg):
        return False
    element_type = element_type_arg[1:-1]
    if element_type not in _ALLOWED_DSL_ELEMENT_TYPES:
        return False
    if not _validate_dsl_value(args[1], declared_names):
        return False
    if len(args) == 3 and not _validate_dsl_object(args[2].strip(), declared_names):
        return False
    return True


def _is_allowed_graph_dsl(dsl: str) -> bool:
    if len(dsl) > 5000:
        return False
    unquoted = _strip_quoted_strings(dsl)
    if re.search(r"=>|`|//|/\*|\*/|\+\+|--", unquoted):
        return False
    for token in _BLOCKED_DSL_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", unquoted, re.IGNORECASE):
            return False

    statements = _split_top_level(dsl, ";")
    if statements is None:
        return False

    declared_names: set[str] = set()
    for statement in [part for part in statements if part]:
        bbox_match = re.fullmatch(r"board\.setBoundingBox\((.*)\)", statement)
        if bbox_match:
            value = bbox_match.group(1).strip()
            parts = (
                _split_top_level(value[1:-1], ",")
                if value.startswith("[") and value.endswith("]")
                else None
            )
            if parts is None or len(parts) != 4 or not all(re.fullmatch(r"-?\d+(?:\.\d+)?", part.strip()) for part in parts):
                return False
            continue

        declaration_match = re.fullmatch(
            r"var\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(board\.create\(.*\))",
            statement,
        )
        if declaration_match:
            name = declaration_match.group(1)
            if name in declared_names:
                return False
            if not _validate_dsl_create_call(declaration_match.group(2), declared_names):
                return False
            declared_names.add(name)
            continue

        if statement.startswith("board.create("):
            if not _validate_dsl_create_call(statement, declared_names):
                return False
            continue

        return False

    return True


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

    if stripped and not _is_allowed_graph_dsl(stripped):
        return None

    return stripped if stripped else None


class CoachingVLMClient(_BaseSolutionCoachingVLMClient):
    def __init__(
        self,
        settings: Settings | None = None,
        subject: str = "math",
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._subject = subject
        if subject == "english":
            endpoint = self._settings.english_coaching_vlm_endpoint
            model = self._settings.english_coaching_vlm_model
            api_key = self._settings.english_coaching_vlm_api_key
            timeout_seconds = self._settings.english_coaching_vlm_timeout_seconds
            provider = self._settings.english_coaching_vlm_provider
        else:
            endpoint = self._settings.math_coaching_vlm_endpoint
            model = self._settings.math_coaching_vlm_model
            api_key = self._settings.math_coaching_vlm_api_key
            timeout_seconds = self._settings.math_coaching_vlm_timeout_seconds
            provider = self._settings.math_coaching_vlm_provider
        super().__init__(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            provider=provider,
            completion_fn=completion_fn,
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
