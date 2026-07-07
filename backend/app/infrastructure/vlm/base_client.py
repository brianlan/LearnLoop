from __future__ import annotations

import json
import re
from typing import Any, Callable

import httpx
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
        http_client: httpx.AsyncClient | None = None,
        error_factory: Callable[..., BaseException] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._owns_client = http_client is None
        self._error_factory = error_factory or BaseVLMError

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
            raise self._make_error(
                "VLM request timed out",
                code=FAILURE_CODE_TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.NetworkError as exc:
            raise self._make_error(
                "VLM network request failed",
                code=FAILURE_CODE_NETWORK,
                retryable=True,
            ) from exc

        raw_body = self._decode_raw_response(response)

        if 500 <= response.status_code:
            raise self._make_error(
                f"VLM provider returned server error {response.status_code}",
                code=FAILURE_CODE_PROVIDER,
                retryable=True,
                status_code=response.status_code,
                raw_provider_response=raw_body,
            )

        if 400 <= response.status_code:
            raise self._make_error(
                f"VLM provider rejected request with status {response.status_code}",
                code=FAILURE_CODE_PROVIDER_REJECTED,
                retryable=False,
                status_code=response.status_code,
                raw_provider_response=raw_body,
            )

        if not isinstance(raw_body, dict):
            raise self._make_error(
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
