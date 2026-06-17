from __future__ import annotations

import httpx
import pytest

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


def _build_client(
    handler,
    *,
    error_factory: Any | None = None,
) -> BaseVLMClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://vlm.example/api",
        timeout=5,
        headers={"Authorization": "Bearer demo", "Content-Type": "application/json"},
    )
    return _TestClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
        http_client=http_client,
        error_factory=error_factory,
    )


@pytest.mark.asyncio
async def test_base_vlm_timeout_maps_to_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = _build_client(handler)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo"})
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_TIMEOUT
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_base_vlm_network_error_maps_to_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _build_client(handler)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo"})
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_NETWORK
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_base_vlm_5xx_maps_to_retryable_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "overloaded"})

    client = _build_client(handler)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo"})
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_PROVIDER
    assert exc_info.value.retryable is True
    assert exc_info.value.status_code == 503
    assert exc_info.value.raw_provider_response == {"detail": "overloaded"}


@pytest.mark.asyncio
async def test_base_vlm_4xx_maps_to_non_retryable_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "bad request"})

    client = _build_client(handler)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo"})
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_PROVIDER_REJECTED
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code == 400
    assert exc_info.value.raw_provider_response == {"detail": "bad request"}


@pytest.mark.asyncio
async def test_base_vlm_non_dict_body_maps_to_invalid_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client = _build_client(handler)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo"})
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code == 200
    assert exc_info.value.raw_provider_response == "not json"


@pytest.mark.asyncio
async def test_base_vlm_returns_dict_on_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = _build_client(handler)

    result = await client._send_chat_completion({"model": "demo"})
    await client.aclose()

    assert result == {"choices": []}


@pytest.mark.asyncio
async def test_base_vlm_custom_error_factory() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _build_client(handler, error_factory=_CustomError)

    with pytest.raises(_CustomError) as exc_info:
        await client._send_chat_completion({"model": "demo"})
    await client.aclose()

    assert exc_info.value.code == FAILURE_CODE_NETWORK
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_base_vlm_close_owns_client() -> None:
    client = _TestClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
    )
    _ = client.http_client
    await client.aclose()
    assert client._http_client is None


@pytest.mark.asyncio
async def test_base_vlm_close_does_not_close_injected_client() -> None:
    injected = httpx.AsyncClient()
    client = _TestClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
        http_client=injected,
    )
    await client.aclose()
    assert client._http_client is injected
