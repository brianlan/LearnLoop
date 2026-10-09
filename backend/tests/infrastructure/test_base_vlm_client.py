from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    UnsupportedParamsError,
)

from app.infrastructure.vlm.base_client import (
    FAILURE_CODE_INVALID_CONFIG,
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
    responses_fn: Any | None = None,
    **extra: Any,
) -> BaseVLMClient:
    return _TestClient(
        endpoint="https://vlm.example/api",
        model="demo",
        api_key="demo",
        timeout_seconds=5,
        provider=provider,
        completion_fn=completion_fn,
        responses_fn=responses_fn,
        error_factory=error_factory,
        **extra,
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


@pytest.mark.asyncio
async def test_base_vlm_chat_completion_validation_error_names_field() -> None:
    # A wrong-typed field stays invalid, so the detail is testable (issue #642).
    client = _build_client()

    with pytest.raises(BaseVLMError) as exc_info:
        client._parse_chat_completion_response({"choices": "not-a-list"})

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False
    message = str(exc_info.value)
    assert "VLM provider response failed chat completion validation" in message
    assert "choices" in message


@pytest.mark.asyncio
async def test_base_vlm_chat_send_passes_reasoning_effort_through() -> None:
    captured: dict[str, Any] = {}

    async def completion_fn(**kwargs):
        captured.update(kwargs)
        return _mock_response(content="{}")

    client = _build_client(completion_fn, reasoning_effort="high")

    await client._send_chat_completion({"model": "demo", "messages": []})

    assert captured["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_base_vlm_responses_send_passes_reasoning_effort() -> None:
    captured: dict[str, Any] = {}

    async def responses_fn(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_text="{}")

    client = _build_client(responses_fn=responses_fn, reasoning_effort="low")

    await client._send_responses_request({"input": "Return your answer as JSON."})

    assert captured["reasoning"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_base_vlm_reasoning_effort_none_omits_parameter_on_both_modes() -> None:
    chat_captured: dict[str, Any] = {}
    responses_captured: dict[str, Any] = {}

    async def completion_fn(**kwargs):
        chat_captured.update(kwargs)
        return _mock_response(content="{}")

    async def responses_fn(**kwargs):
        responses_captured.update(kwargs)
        return SimpleNamespace(output_text="{}")

    client = _build_client(
        completion_fn, responses_fn=responses_fn, reasoning_effort="none"
    )

    await client._send_chat_completion({"model": "demo", "messages": []})
    await client._send_responses_request({"input": "Return your answer as JSON."})

    assert "reasoning_effort" not in chat_captured
    assert "reasoning" not in responses_captured


@pytest.mark.asyncio
async def test_base_vlm_constructor_default_omits_reasoning_parameter() -> None:
    """Callers that do not pass reasoning_effort keep pre-change requests (#677)."""
    chat_captured: dict[str, Any] = {}
    responses_captured: dict[str, Any] = {}

    async def completion_fn(**kwargs):
        chat_captured.update(kwargs)
        return _mock_response(content="{}")

    async def responses_fn(**kwargs):
        responses_captured.update(kwargs)
        return SimpleNamespace(output_text="{}")

    client = _build_client(completion_fn, responses_fn=responses_fn)

    await client._send_chat_completion({"model": "demo", "messages": []})
    await client._send_responses_request({"input": "Return your answer as JSON."})

    assert "reasoning_effort" not in chat_captured
    assert "reasoning" not in responses_captured


@pytest.mark.asyncio
async def test_base_vlm_local_param_validation_maps_to_invalid_config() -> None:
    """SDK-local parameter validation is not a remote rejection (#689).

    litellm raises UnsupportedParamsError client-side (zero HTTP requests) and
    stamps a synthetic status_code on it; reporting it as "provider rejected
    request with status 400" misattributes a local config error to the gateway.
    """

    async def completion_fn(**kwargs):
        raise UnsupportedParamsError(
            status_code=400,
            message=(
                "openai does not support parameters: ['reasoning_effort'], "
                "for model=deepseek-v4.1-flash."
            ),
            model="demo",
            llm_provider="openai",
        )

    client = _build_client(completion_fn)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion({"model": "demo", "messages": []})

    assert exc_info.value.code == FAILURE_CODE_INVALID_CONFIG
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code is None
    message = str(exc_info.value)
    assert "local parameter validation" in message
    assert "rejected request with status" not in message


@pytest.mark.asyncio
async def test_base_vlm_responses_local_param_validation_maps_to_invalid_config() -> None:
    async def responses_fn(**kwargs):
        raise UnsupportedParamsError(
            status_code=500,
            message="openai does not support parameters: ['reasoning'], for model=demo.",
            model="demo",
            llm_provider="openai",
        )

    client = _build_client(responses_fn=responses_fn)

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_responses_request({"input": "Return your answer as JSON."})

    assert exc_info.value.code == FAILURE_CODE_INVALID_CONFIG
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code is None


def test_litellm_unsupported_params_error_stays_in_api_error_family() -> None:
    """Pins the catch-order dependency (#689).

    UnsupportedParamsError arrives inside the generic APIError family (via the
    openai SDK types litellm subclasses), so the local-validation branch must be
    declared before the generic handler. A future litellm rename turns this red
    instead of crashing app startup.
    """
    import openai

    from litellm.exceptions import APIError as LiteLLMAPIError

    assert issubclass(UnsupportedParamsError, (LiteLLMAPIError, openai.APIError))


# --- Real-SDK HTTP-boundary tests (#689) -----------------------------------
#
# These cross the real litellm SDK to a local capture server: no
# completion_fn/responses_fn injection, so litellm's own parameter validation
# and request transformation run for real against a wire payload.

_CHAT_PAYLOAD = {
    "id": "chatcmpl-capture",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "deepseek-v4.1-flash",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "OK"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
_RESPONSES_TEXT = "hello from the capture server"
_RESPONSES_PAYLOAD = {
    "id": "resp-capture",
    "object": "response",
    "created_at": 1700000000,
    "model": "deepseek-v4.1-flash",
    "status": "completed",
    "output": [
        {
            "id": "msg-capture",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": _RESPONSES_TEXT, "annotations": []}
            ],
        }
    ],
    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
}


class _CaptureServer:
    """Minimal capture server recording (path, json body) per POST."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.status = 200
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    body = {}
                outer.requests.append((self.path, body))
                if outer.status >= 400:
                    payload = {
                        "error": {
                            "message": "capture server rejection",
                            "type": "invalid_request_error",
                        }
                    }
                elif self.path.endswith("/responses"):
                    payload = _RESPONSES_PAYLOAD
                else:
                    payload = _CHAT_PAYLOAD
                encoded = json.dumps(payload).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *args: Any) -> None:
                pass  # keep pytest output clean

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture
def capture_server():
    server = _CaptureServer()
    server.start()
    yield server
    server.stop()


def _build_real_client(
    server: _CaptureServer,
    *,
    reasoning_effort: str = "high",
    api_mode: str = "chat",
) -> BaseVLMClient:
    return BaseVLMClient(
        endpoint=server.url,
        model="deepseek-v4.1-flash",
        api_key="capture-key",
        timeout_seconds=30,
        provider="openai",
        api_mode=api_mode,  # type: ignore[arg-type]
        reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_real_sdk_unknown_alias_effort_still_fails_closed(capture_server) -> None:
    """Pins litellm's current client-side rejection for unknown aliases (#689).

    Upstream BerriAI/litellm#39065 ("forward reasoning_effort for unknown model
    aliases instead of failing closed") is merged into
    ``litellm_internal_staging`` but is NOT in any PyPI release: 1.104.2, the
    latest at implementation time, still raises ``UnsupportedParamsError`` for
    ``openai/<custom-alias>`` + ``reasoning_effort`` before any HTTP request.
    Verified against the installed SDK source (``OpenAIUnknownModelConfig``
    does not exist) and against the wire below.

    No allowlist and no drop_params here on purpose: those would mask whether a
    future upgrade alone fixes the bug. Until a release carries the fix, the
    classification added in #689 is what keeps this legible — a local config
    error, not a remote 400. Flip these assertions when the fix ships.
    """
    client = _build_real_client(capture_server, reasoning_effort="high")

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion(
            {"model": "demo", "messages": [{"role": "user", "content": "hi"}]}
        )

    assert exc_info.value.code == FAILURE_CODE_INVALID_CONFIG
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code is None
    assert "rejected request with status" not in str(exc_info.value)
    # Zero requests left the process: the failure is SDK-local, not remote.
    assert capture_server.requests == []


@pytest.mark.asyncio
async def test_real_sdk_effort_none_omits_reasoning_on_both_paths(
    capture_server,
) -> None:
    chat_client = _build_real_client(capture_server, reasoning_effort="none")
    await chat_client._send_chat_completion(
        {"model": "demo", "messages": [{"role": "user", "content": "hi"}]}
    )

    responses_client = _build_real_client(
        capture_server, reasoning_effort="none", api_mode="responses"
    )
    await responses_client._send_responses_request({"input": "Return your answer as JSON."})

    paths = [path for path, _ in capture_server.requests]
    assert paths == ["/chat/completions", "/responses"]
    assert "reasoning_effort" not in capture_server.requests[0][1]
    assert "reasoning" not in capture_server.requests[1][1]


@pytest.mark.asyncio
async def test_real_sdk_preserves_image_block_and_responses_input(
    capture_server,
) -> None:
    # effort="none" on the chat path: litellm still rejects reasoning_effort for
    # unknown aliases (see test_real_sdk_unknown_alias_effort_still_fails_closed),
    # and message passthrough is what this test is about.
    image_url = "https://example.test/problem.png"
    chat_client = _build_real_client(capture_server, reasoning_effort="none")
    await chat_client._send_chat_completion(
        {
            "model": "demo",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "solve this"},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
        },
    )

    responses_client = _build_real_client(capture_server, api_mode="responses")
    await responses_client._send_responses_request({"input": "Reply with OK"})

    chat_body = capture_server.requests[0][1]
    assert image_url in json.dumps(chat_body)
    responses_body = capture_server.requests[1][1]
    assert "Reply with OK" in json.dumps(responses_body["input"])


@pytest.mark.asyncio
async def test_real_sdk_responses_output_text_round_trip(capture_server) -> None:
    """The output_text accessor must work on a real litellm responses object."""
    client = _build_real_client(capture_server, api_mode="responses")

    result = await client._send_responses_request({"input": "Return your answer as JSON."})

    assert result == {"output_text": _RESPONSES_TEXT}


@pytest.mark.asyncio
async def test_real_sdk_remote_400_maps_to_provider_rejected(capture_server) -> None:
    """A genuine remote 400 stays distinct from the local branch (#689)."""
    capture_server.status = 400
    # effort="none" so the request reaches the wire (see the fails-closed test).
    client = _build_real_client(capture_server, reasoning_effort="none")

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion(
            {"model": "demo", "messages": [{"role": "user", "content": "hi"}]}
        )

    assert exc_info.value.code == FAILURE_CODE_PROVIDER_REJECTED
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code == 400
