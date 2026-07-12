from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from litellm.exceptions import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    Timeout,
)

from app.infrastructure.vlm.base_client import (
    FAILURE_CODE_INVALID_RESPONSE,
    FAILURE_CODE_NETWORK,
    FAILURE_CODE_PROVIDER,
    FAILURE_CODE_PROVIDER_REJECTED,
    FAILURE_CODE_TIMEOUT,
    BaseVLMClient,
    BaseVLMError,
)


class _TestClient(BaseVLMClient):
    pass


class _CustomError(Exception):
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


def _mock_response(content: str = "{}", reasoning_content: str | None = None) -> Any:
    message = SimpleNamespace(
        role="assistant",
        content=content,
        reasoning_content=reasoning_content,
        provider_specific_fields=None,
    )
    choice = SimpleNamespace(index=0, message=message)
    return SimpleNamespace(choices=[choice])


def _build_client(
    completion_fn: Any | None = None,
    *,
    error_factory: Any | None = None,
    provider: str = "openai",
) -> BaseVLMClient:
    return _TestClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
        provider=provider,
        completion_fn=completion_fn,
        error_factory=error_factory,
    )


@pytest.mark.asyncio
async def test_base_vlm_timeout_maps_to_error() -> None:
    async def completion_fn(**kwargs):
        raise Timeout(message="timed out", model="demo", llm_provider="openai")

    client = _build_client(completion_fn)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo", "messages": []})

    assert exc_info.value.code == FAILURE_CODE_TIMEOUT
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_base_vlm_network_error_maps_to_error() -> None:
    async def completion_fn(**kwargs):
        raise APIConnectionError(message="boom", model="demo", llm_provider="openai")

    client = _build_client(completion_fn)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo", "messages": []})

    assert exc_info.value.code == FAILURE_CODE_NETWORK
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_base_vlm_5xx_maps_to_retryable_error() -> None:
    async def completion_fn(**kwargs):
        raise InternalServerError(
            message="overloaded", model="demo", llm_provider="openai"
        )

    client = _build_client(completion_fn)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo", "messages": []})

    assert exc_info.value.code == FAILURE_CODE_PROVIDER
    assert exc_info.value.retryable is True
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_base_vlm_4xx_maps_to_non_retryable_error() -> None:
    async def completion_fn(**kwargs):
        raise BadRequestError(
            message="bad request", model="demo", llm_provider="openai"
        )

    client = _build_client(completion_fn)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo", "messages": []})

    assert exc_info.value.code == FAILURE_CODE_PROVIDER_REJECTED
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_base_vlm_authentication_error_maps_to_non_retryable() -> None:
    async def completion_fn(**kwargs):
        raise AuthenticationError(
            message="invalid key", model="demo", llm_provider="openai"
        )

    client = _build_client(completion_fn)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo", "messages": []})

    assert exc_info.value.code == FAILURE_CODE_PROVIDER_REJECTED
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_base_vlm_generic_api_error_maps_to_retryable_provider_error() -> None:
    async def completion_fn(**kwargs):
        raise APIError(
            status_code=500,
            message="unknown",
            model="demo",
            llm_provider="openai",
        )

    client = _build_client(completion_fn)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo", "messages": []})

    assert exc_info.value.code == FAILURE_CODE_PROVIDER
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_base_vlm_returns_dict_on_success() -> None:
    async def completion_fn(**kwargs):
        return _mock_response(content='{"choices": []}')

    client = _build_client(completion_fn)

    result = await client._send_chat_completion({"model": "demo", "messages": []})

    assert isinstance(result, dict)
    assert "choices" in result


@pytest.mark.asyncio
async def test_base_vlm_custom_error_factory() -> None:
    async def completion_fn(**kwargs):
        raise APIConnectionError(message="boom", model="demo", llm_provider="openai")

    client = _build_client(completion_fn, error_factory=_CustomError)

    with pytest.raises(_CustomError) as exc_info:
        await client._send_chat_completion({"model": "demo", "messages": []})

    assert exc_info.value.code == FAILURE_CODE_NETWORK
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_base_vlm_forwards_provider_qualified_model_and_api_base() -> None:
    captured: dict[str, Any] = {}

    async def completion_fn(**kwargs):
        captured.update(kwargs)
        return _mock_response(content="{}")

    client = _build_client(completion_fn, provider="ollama")

    await client._send_chat_completion({"model": "demo", "messages": []})

    assert captured["model"] == "ollama/demo"
    assert captured["api_base"] == "https://vlm.example/api"
    assert captured["api_key"] == "demo"
    assert captured["timeout"] == 5


@pytest.mark.asyncio
async def test_base_vlm_default_provider_quals_model_once() -> None:
    captured: dict[str, Any] = {}

    async def completion_fn(**kwargs):
        captured.update(kwargs)
        return _mock_response(content="{}")

    client = _build_client(completion_fn)

    await client._send_chat_completion({"model": "demo", "messages": []})

    assert captured["model"] == "openai/demo"


@pytest.mark.asyncio
async def test_base_vlm_aclose_is_callable() -> None:
    client = _build_client()
    await client.aclose()
