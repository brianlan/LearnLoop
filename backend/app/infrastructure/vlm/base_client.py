from __future__ import annotations

import json
import re
from typing import Any, Callable, Literal

import litellm
import openai
from litellm.exceptions import APIError as LiteLLMAPIError
from litellm.exceptions import APIConnectionError, Timeout
from pydantic import ValidationError

from app.infrastructure.vlm._models import _ChatCompletionResponse

FAILURE_CODE_TIMEOUT = "vlm-timeout"
FAILURE_CODE_NETWORK = "vlm-network-error"
FAILURE_CODE_PROVIDER = "vlm-provider-error"
FAILURE_CODE_PROVIDER_REJECTED = "vlm-provider-rejected"
FAILURE_CODE_INVALID_RESPONSE = "vlm-invalid-response"


class BaseVLMError(Exception):
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


class BaseVLMClient:
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
        error_factory: Callable[..., BaseException] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._provider = provider
        self._api_mode = api_mode
        self._effective_model = f"{provider}/{model}"
        self._completion_fn = completion_fn or litellm.acompletion
        self._responses_fn = responses_fn or litellm.aresponses
        self._error_factory = error_factory or BaseVLMError

    async def aclose(self) -> None:
        pass

    async def _send_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._completion_fn(
                model=self._effective_model,
                messages=payload["messages"],
                api_base=self._endpoint,
                api_key=self._api_key,
                timeout=self._timeout_seconds,
                num_retries=0,
            )
        except Timeout as exc:
            raise self._make_error(
                "VLM request timed out",
                code=FAILURE_CODE_TIMEOUT,
                retryable=True,
            ) from exc
        except APIConnectionError as exc:
            raise self._make_error(
                "VLM network request failed",
                code=FAILURE_CODE_NETWORK,
                retryable=True,
            ) from exc
        except (LiteLLMAPIError, openai.APIError) as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code is not None and 500 <= status_code:
                raise self._make_error(
                    f"VLM provider returned server error {status_code}",
                    code=FAILURE_CODE_PROVIDER,
                    retryable=True,
                    status_code=status_code,
                ) from exc
            if status_code is not None and 400 <= status_code:
                raise self._make_error(
                    f"VLM provider rejected request with status {status_code}",
                    code=FAILURE_CODE_PROVIDER_REJECTED,
                    retryable=False,
                    status_code=status_code,
                ) from exc
            raise self._make_error(
                "VLM provider error",
                code=FAILURE_CODE_PROVIDER,
                retryable=True,
            ) from exc

        return self._completion_to_dict(response)

    async def _send_responses_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._responses_fn(
                model=self._effective_model,
                instructions=payload.get("instructions"),
                input=payload.get("input"),
                api_base=self._endpoint,
                api_key=self._api_key,
                timeout=self._timeout_seconds,
                num_retries=0,
            )
        except Timeout as exc:
            raise self._make_error(
                "VLM request timed out",
                code=FAILURE_CODE_TIMEOUT,
                retryable=True,
            ) from exc
        except APIConnectionError as exc:
            raise self._make_error(
                "VLM network request failed",
                code=FAILURE_CODE_NETWORK,
                retryable=True,
            ) from exc
        except (LiteLLMAPIError, openai.APIError) as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code is not None and 500 <= status_code:
                raise self._make_error(
                    f"VLM provider returned server error {status_code}",
                    code=FAILURE_CODE_PROVIDER,
                    retryable=True,
                    status_code=status_code,
                ) from exc
            if status_code is not None and 400 <= status_code:
                raise self._make_error(
                    f"VLM provider rejected request with status {status_code}",
                    code=FAILURE_CODE_PROVIDER_REJECTED,
                    retryable=False,
                    status_code=status_code,
                ) from exc
            raise self._make_error(
                "VLM provider error",
                code=FAILURE_CODE_PROVIDER,
                retryable=True,
            ) from exc

        return self._responses_to_dict(response)

    @staticmethod
    def _responses_to_dict(response: Any) -> dict[str, Any]:
        # Responses API returns a single output text in the response
        # Extract it and structure it similar to chat completion for compatibility
        output = getattr(response, "output", None)
        if output is None:
            # Fallback: some implementations may use different structure
            return {"output_text": str(response)}
        
        # Extract text from output
        output_text = getattr(output, "text", None)
        if output_text is None and hasattr(output, "__iter__"):
            # output might be a list of content items
            for item in output:
                if hasattr(item, "text"):
                    output_text = item.text
                    break
        
        if output_text is None:
            output_text = str(output)
        
        return {"output_text": output_text}

    @staticmethod
    def _completion_to_dict(response: Any) -> dict[str, Any]:
        choices = []
        for choice in response.choices:
            message = choice.message
            msg: dict[str, Any] = {
                "role": message.role,
                "content": message.content,
            }
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning is None:
                psf = getattr(message, "provider_specific_fields", None)
                if isinstance(psf, dict):
                    reasoning = psf.get("reasoning_content")
            if reasoning is not None:
                msg["reasoning_content"] = reasoning
            choices.append({"index": choice.index, "message": msg})
        return {"choices": choices}

    def _make_error(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        status_code: int | None = None,
        raw_provider_response: Any | None = None,
    ) -> BaseException:
        return self._error_factory(
            message,
            code=code,
            retryable=retryable,
            status_code=status_code,
            raw_provider_response=raw_provider_response,
        )

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

    @staticmethod
    def _strip_thinking_content(content: str) -> tuple[str, str | None]:
        """Strip a single leading <think>...</think> block and return the rest plus reasoning.

        Only a complete leading block (allowing leading whitespace) is removed.
        Returns (content_with_block_removed, extracted_reasoning_or_none).
        """
        match = re.match(r"^\s*<think>(.*?)</think>", content, re.DOTALL)
        if not match:
            return content, None
        reasoning = match.group(1).strip()
        remaining = content[match.end() :].strip()
        return remaining, reasoning or None

    def _load_json_content(self, content: str) -> dict[str, Any]:
        candidates = [self._strip_json_code_fences(content), content.strip()]
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

        raise self._make_error(
            "VLM provider response content was not valid JSON",
            code=FAILURE_CODE_INVALID_RESPONSE,
            retryable=False,
            raw_provider_response=content,
        )

    def _parse_chat_completion_response(self, raw_body: dict[str, Any]) -> dict[str, Any]:
        try:
            completion = _ChatCompletionResponse.model_validate(raw_body)
        except ValidationError as exc:
            raise self._make_error(
                "VLM provider response failed chat completion validation",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            ) from exc

        if not completion.choices:
            raise self._make_error(
                "VLM provider returned no choices",
                code=FAILURE_CODE_INVALID_RESPONSE,
                retryable=False,
                raw_provider_response=raw_body,
            )

        message = completion.choices[0].message
        content = message.content
        if not content:
            raise self._make_error(
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
