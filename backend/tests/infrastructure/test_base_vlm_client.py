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
        # Raw-body override (#704): serve an exact byte payload (e.g. the
        # Photonmark keepalive-plus-JSON envelope) instead of a JSON fixture.
        self.raw_body: bytes | None = None
        self.content_type = "application/json"
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
                if outer.raw_body is not None:
                    encoded = outer.raw_body
                elif outer.status >= 400:
                    payload = {
                        "error": {
                            "message": "capture server rejection",
                            "type": "invalid_request_error",
                        }
                    }
                    encoded = json.dumps(payload).encode()
                elif self.path.endswith("/responses"):
                    encoded = json.dumps(_RESPONSES_PAYLOAD).encode()
                else:
                    encoded = json.dumps(_CHAT_PAYLOAD).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", outer.content_type)
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
    provider: str = "openai",
) -> BaseVLMClient:
    return BaseVLMClient(
        endpoint=server.url,
        model="deepseek-v4.1-flash",
        api_key="capture-key",
        timeout_seconds=30,
        provider=provider,
        api_mode=api_mode,  # type: ignore[arg-type]
        reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_real_sdk_unknown_alias_effort_reaches_wire(capture_server) -> None:
    """#689 acceptance criterion 1, end to end through the real SDK.

    Upstream BerriAI/litellm#39065 ("forward reasoning_effort for unknown model
    aliases instead of failing closed") ships in 1.104.2 via
    ``OpenAIUnknownModelConfig``, so an ``openai/<custom-alias>`` profile with a
    non-``none`` effort reaches the wire with the parameter preserved instead of
    raising ``UnsupportedParamsError`` before any HTTP request.

    No allowlist and no drop_params here on purpose: those would mask a
    regression if the forward-compatibility path is ever removed again.
    """
    client = _build_real_client(capture_server, reasoning_effort="high")

    await client._send_chat_completion(
        {"model": "demo", "messages": [{"role": "user", "content": "hi"}]}
    )

    assert len(capture_server.requests) == 1
    path, body = capture_server.requests[0]
    assert path == "/chat/completions"
    assert body["model"] == "deepseek-v4.1-flash"
    assert body["reasoning_effort"] == "high"


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
    # effort="high" here on purpose: the unknown-alias forward path must not
    # disturb message passthrough.
    image_url = "https://example.test/problem.png"
    chat_client = _build_real_client(capture_server, reasoning_effort="high")
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
    # effort="high": the request must still reach the wire and the remote 400
    # must not be mistaken for an SDK-local validation failure.
    client = _build_real_client(capture_server, reasoning_effort="high")

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion(
            {"model": "demo", "messages": [{"role": "user", "content": "hi"}]}
        )

    assert exc_info.value.code == FAILURE_CODE_PROVIDER_REJECTED
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code == 400


# --- Photonmark adapter (#704) ----------------------------------------------
#
# The explicit `photonmark` provider rides LiteLLM's OpenAI route and recovers
# the observed keepalive-plus-final-JSON Responses envelope. Configured
# identity (settings output, provenance) stays `photonmark`.

_PHOTONMARK_KEEPALIVE_FRAME = (
    "event: response.in_progress\n"
    'data: {"type":"response.in_progress","response":{"id":"resp_photonmark_keepalive",'
    '"object":"response","status":"in_progress","output":[]}}\n\n'
)


def _photonmark_final_payload(text: str, *, with_output_text_field: bool = False) -> dict:
    payload = {
        "id": "resp-photonmark-final",
        "object": "response",
        "created_at": 1700000000,
        "model": "deepseek-v4.1-flash",
        "status": "completed",
        "error": None,
        "output": [
            {
                "id": "msg-photonmark",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": text, "annotations": []}
                ],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    if with_output_text_field:
        payload["output_text"] = text
    return payload


def _photonmark_envelope_with_output_message(output_item: dict) -> bytes:
    """Envelope whose terminal object carries a raw (untyped) output item.

    The litellm response model's dict fallback keeps such items unparsed, so
    recovery itself must validate and refuse them (#704 B1).
    """
    payload = _photonmark_final_payload("x")
    payload["output"] = [output_item]
    return (_PHOTONMARK_KEEPALIVE_FRAME + json.dumps(payload) + "\n").encode()


def _mixed_envelope(
    final: dict,
    *,
    frames: int = 1,
    crlf: bool = False,
) -> bytes:
    body = _PHOTONMARK_KEEPALIVE_FRAME * frames + json.dumps(final) + "\n"
    if crlf:
        body = body.replace("\n", "\r\n")
    return body.encode()


def _serve_envelope(capture_server: _CaptureServer, body: bytes) -> None:
    capture_server.raw_body = body
    capture_server.content_type = "text/event-stream"


@pytest.mark.asyncio
async def test_base_vlm_photonmark_routes_via_openai_prefix_and_keeps_identity() -> None:
    """Configured identity stays `photonmark`; only the SDK prefix is derived."""
    chat_captured: dict[str, Any] = {}
    responses_captured: dict[str, Any] = {}

    async def completion_fn(**kwargs):
        chat_captured.update(kwargs)
        return _mock_response(content="{}")

    async def responses_fn(**kwargs):
        responses_captured.update(kwargs)
        return SimpleNamespace(output_text="{}")

    client = _build_client(
        completion_fn,
        responses_fn=responses_fn,
        provider="photonmark",
    )

    assert client._provider == "photonmark"

    await client._send_chat_completion({"model": "demo", "messages": []})
    await client._send_responses_request({"input": "Return your answer as JSON."})

    assert chat_captured["model"] == "openai/demo"
    assert responses_captured["model"] == "openai/demo"


@pytest.mark.asyncio
async def test_real_sdk_photonmark_recovers_keepalive_envelope(capture_server) -> None:
    """#704 AC2/AC3: keepalive envelope over the real SDK is recovered in one request."""
    _serve_envelope(
        capture_server,
        _mixed_envelope(_photonmark_final_payload(_RESPONSES_TEXT)),
    )
    client = _build_real_client(capture_server, api_mode="responses", provider="photonmark")

    result = await client._send_responses_request({"input": "Return your answer as JSON."})

    assert result == {"output_text": _RESPONSES_TEXT}
    # No second HTTP request: the already-received body is reused.
    assert len(capture_server.requests) == 1
    path, body = capture_server.requests[0]
    assert path == "/responses"
    # LiteLLM's OpenAI route with the configured wire model alias (#704 AC2).
    assert body["model"] == "deepseek-v4.1-flash"
    assert body["reasoning"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_real_sdk_photonmark_recovers_repeated_frames_crlf_and_plain_output(
    capture_server,
) -> None:
    """#704 AC4: repeated frames, CRLF, and no top-level output_text field."""
    _serve_envelope(
        capture_server,
        _mixed_envelope(
            _photonmark_final_payload(_RESPONSES_TEXT), frames=2, crlf=True
        ),
    )
    client = _build_real_client(capture_server, api_mode="responses", provider="photonmark")

    result = await client._send_responses_request({"input": "Return your answer as JSON."})

    assert result == {"output_text": _RESPONSES_TEXT}
    assert len(capture_server.requests) == 1


@pytest.mark.asyncio
async def test_real_sdk_photonmark_recovers_output_text_convenience_field(
    capture_server,
) -> None:
    """A gateway object that also carries the nonstandard top-level
    `output_text` field still recovers (#704 B2 missing case)."""
    _serve_envelope(
        capture_server,
        _mixed_envelope(
            _photonmark_final_payload(_RESPONSES_TEXT, with_output_text_field=True)
        ),
    )
    client = _build_real_client(capture_server, api_mode="responses", provider="photonmark")

    result = await client._send_responses_request({"input": "Return your answer as JSON."})

    assert result == {"output_text": _RESPONSES_TEXT}
    assert len(capture_server.requests) == 1


@pytest.mark.asyncio
async def test_real_sdk_photonmark_recovers_with_typed_reasoning_item(
    capture_server,
) -> None:
    """A typed non-message item (reasoning) must not block recovery; the
    untyped-dict refusal covers only entries the SDK cannot type (B1)."""
    final = _photonmark_final_payload(_RESPONSES_TEXT)
    final["output"] = [
        {"id": "r-1", "type": "reasoning", "summary": []},
        *final["output"],
    ]
    _serve_envelope(capture_server, _mixed_envelope(final))
    client = _build_real_client(capture_server, api_mode="responses", provider="photonmark")

    result = await client._send_responses_request({"input": "Return your answer as JSON."})

    assert result == {"output_text": _RESPONSES_TEXT}
    assert len(capture_server.requests) == 1


@pytest.mark.parametrize(
    "recovered_output_text",
    [
        "not json at all",
        # Valid JSON but failing the extraction schema (missing problemType).
        json.dumps({"text": "synthetic problem text"}),
    ],
)
@pytest.mark.asyncio
async def test_real_sdk_photonmark_recovered_invalid_extraction_fails_schema(
    capture_server, recovered_output_text: str
) -> None:
    """Recovery reuses the existing business validation: invalid extraction
    payloads never become apparent successes (#704 B2/AC6)."""
    from app.infrastructure.vlm.client import VLMClient, VLMError

    _serve_envelope(
        capture_server,
        _mixed_envelope(_photonmark_final_payload(recovered_output_text)),
    )
    client = VLMClient(
        endpoint=capture_server.url,
        model="deepseek-v4.1-flash",
        api_key="capture-key",
        timeout_seconds=30,
        provider="photonmark",
        api_mode="responses",
        reasoning_effort="high",
    )

    with pytest.raises(VLMError) as exc_info:
        await client.extract(image_base64="c3ludGhldGlj")

    assert exc_info.value.code == FAILURE_CODE_INVALID_RESPONSE
    assert exc_info.value.retryable is False
    assert len(capture_server.requests) == 1


@pytest.mark.asyncio
async def test_real_sdk_photonmark_standard_json_response_unaffected(capture_server) -> None:
    """A normal Responses JSON still works for the photonmark selection."""
    client = _build_real_client(capture_server, api_mode="responses", provider="photonmark")

    result = await client._send_responses_request({"input": "Return your answer as JSON."})

    assert result == {"output_text": _RESPONSES_TEXT}
    assert len(capture_server.requests) == 1


@pytest.mark.asyncio
async def test_real_sdk_non_photonmark_same_envelope_still_fails(capture_server) -> None:
    """#704 AC5: identical mixed body through `openai` keeps the old failure."""
    _serve_envelope(
        capture_server,
        _mixed_envelope(_photonmark_final_payload(_RESPONSES_TEXT)),
    )
    client = _build_real_client(capture_server, api_mode="responses", provider="openai")

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_responses_request({"input": "Return your answer as JSON."})

    assert exc_info.value.code == FAILURE_CODE_PROVIDER
    assert exc_info.value.retryable is True
    assert len(capture_server.requests) == 1


@pytest.mark.asyncio
async def test_real_sdk_photonmark_chat_mode_gets_no_recovery(capture_server) -> None:
    """#704 AC7: Photonmark chat does not acquire Responses recovery."""
    _serve_envelope(
        capture_server,
        _mixed_envelope(_photonmark_final_payload(_RESPONSES_TEXT)),
    )
    client = _build_real_client(capture_server, api_mode="chat", provider="photonmark")

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_chat_completion(
            {"model": "demo", "messages": [{"role": "user", "content": "hi"}]}
        )

    assert exc_info.value.code == FAILURE_CODE_PROVIDER
    assert len(capture_server.requests) == 1


@pytest.mark.parametrize(
    "case_name,envelope_body",
    [
        # Heartbeats without a terminal result.
        ("heartbeat-only", _PHOTONMARK_KEEPALIVE_FRAME.encode()),
        # Unknown response id in the frame.
        (
            "unknown-response-id",
            (
                "event: response.in_progress\n"
                'data: {"type":"response.in_progress","response":{"id":"resp_other",'
                '"object":"response","status":"in_progress","output":[]}}\n\n'
                + json.dumps(_photonmark_final_payload("x"))
                + "\n"
            ).encode(),
        ),
        # Unknown event name.
        (
            "unknown-event",
            (
                "event: response.created\n"
                'data: {"type":"response.in_progress","response":{"id":"resp_photonmark_keepalive",'
                '"object":"response","status":"in_progress","output":[]}}\n\n'
                + json.dumps(_photonmark_final_payload("x"))
                + "\n"
            ).encode(),
        ),
        # Malformed heartbeat data line.
        (
            "malformed-heartbeat",
            (
                "event: response.in_progress\ndata: {not-json}\n\n"
                + json.dumps(_photonmark_final_payload("x"))
                + "\n"
            ).encode(),
        ),
        # Malformed/truncated final JSON.
        (
            "truncated-final-json",
            (_PHOTONMARK_KEEPALIVE_FRAME + '{"id": "resp-x", "object": "resp').encode(),
        ),
        # More than one final object (trailing junk).
        (
            "two-final-objects",
            (
                _PHOTONMARK_KEEPALIVE_FRAME
                + json.dumps(_photonmark_final_payload("x"))
                + "\n"
                + json.dumps(_photonmark_final_payload("y"))
                + "\n"
            ).encode(),
        ),
        # Trailing non-whitespace junk after the final object.
        (
            "trailing-junk",
            (
                _PHOTONMARK_KEEPALIVE_FRAME
                + json.dumps(_photonmark_final_payload("x"))
                + "\nSSE-DONE\n"
            ).encode(),
        ),
        # Wrong terminal object shape: missing output (SDK model rejects).
        (
            "missing-output",
            (
                _PHOTONMARK_KEEPALIVE_FRAME
                + json.dumps(
                    {
                        k: v
                        for k, v in _photonmark_final_payload("x").items()
                        if k != "output"
                    }
                )
                + "\n"
            ).encode(),
        ),
        # Invalid output entries (SDK model rejects).
        (
            "garbage-output",
            (
                _PHOTONMARK_KEEPALIVE_FRAME
                + json.dumps(_photonmark_final_payload("x") | {"output": ["garbage"]})
                + "\n"
            ).encode(),
        ),
        # Untyped dict output entries: the SDK model's dict fallback keeps them,
        # so recovery itself must refuse them (#704 B1).
        (
            "untyped-output-dict",
            (
                _PHOTONMARK_KEEPALIVE_FRAME
                + json.dumps(
                    _photonmark_final_payload("x")
                    | {"output": [{"type": "weird_thing", "foo": "bar"}]}
                )
                + "\n"
            ).encode(),
        ),
        # Message item with the wrong role (untyped fallback dict).
        (
            "wrong-role-message",
            _photonmark_envelope_with_output_message(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "output_text", "text": "x", "annotations": []}],
                }
            ),
        ),
        # Message item missing required fields (id/status).
        (
            "message-missing-required-fields",
            _photonmark_envelope_with_output_message(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "x", "annotations": []}],
                }
            ),
        ),
        # Non-string output text (accessor raises TypeError -> must fail closed).
        (
            "non-string-text",
            _photonmark_envelope_with_output_message(
                {
                    "id": "msg-1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": 123, "annotations": []}],
                }
            ),
        ),
        # Non-list content.
        (
            "non-list-content",
            _photonmark_envelope_with_output_message(
                {
                    "id": "msg-1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": "not-a-list",
                }
            ),
        ),
        # Empty output: nothing usable even though structurally valid.
        (
            "empty-output",
            (
                _PHOTONMARK_KEEPALIVE_FRAME
                + json.dumps(_photonmark_final_payload("x") | {"output": []})
                + "\n"
            ).encode(),
        ),
        # `failed` terminal status.
        (
            "failed-status",
            (
                _PHOTONMARK_KEEPALIVE_FRAME
                + json.dumps(_photonmark_final_payload("x") | {"status": "failed"})
                + "\n"
            ).encode(),
        ),
        # `incomplete` terminal status.
        (
            "incomplete-status",
            (
                _PHOTONMARK_KEEPALIVE_FRAME
                + json.dumps(_photonmark_final_payload("x") | {"status": "incomplete"})
                + "\n"
            ).encode(),
        ),
        # Non-null provider error.
        (
            "provider-error",
            (
                _PHOTONMARK_KEEPALIVE_FRAME
                + json.dumps(
                    _photonmark_final_payload("x")
                    | {"error": {"code": "server_error", "message": "boom"}}
                )
                + "\n"
            ).encode(),
        ),
        # Wrong top-level object type.
        (
            "wrong-object-type",
            (
                _PHOTONMARK_KEEPALIVE_FRAME
                + json.dumps(_photonmark_final_payload("x") | {"object": "chat.completion"})
                + "\n"
            ).encode(),
        ),
    ],
)
@pytest.mark.asyncio
async def test_real_sdk_photonmark_negative_envelopes_fail_closed(
    capture_server, case_name: str, envelope_body: bytes
) -> None:
    """#704 AC6: every unrecognized/failing envelope keeps the existing failure."""
    assert isinstance(envelope_body, bytes)
    _serve_envelope(capture_server, envelope_body)
    client = _build_real_client(capture_server, api_mode="responses", provider="photonmark")

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_responses_request({"input": "Return your answer as JSON."})

    assert exc_info.value.code == FAILURE_CODE_PROVIDER
    assert exc_info.value.retryable is True
    # The failure came from the real HTTP-200 parse path, not a connection
    # error: the translated error's cause is the SDK's status-200 APIError.
    assert getattr(exc_info.value.__cause__, "status_code", None) == 200
    # No compatibility retry: the recovery never sends a second request.
    assert len(capture_server.requests) == 1


@pytest.mark.parametrize("status_code", [400, 401, 429, 500])
@pytest.mark.asyncio
async def test_real_sdk_photonmark_genuine_http_errors_keep_existing_paths(
    capture_server, status_code: int
) -> None:
    """#704 AC6: real HTTP errors are not recovery candidates."""
    capture_server.status = status_code
    client = _build_real_client(capture_server, api_mode="responses", provider="photonmark")

    with pytest.raises(BaseVLMError) as exc_info:
        await client._send_responses_request({"input": "Return your answer as JSON."})

    if status_code >= 500:
        assert exc_info.value.code == FAILURE_CODE_PROVIDER
        assert exc_info.value.retryable is True
    else:
        assert exc_info.value.code == FAILURE_CODE_PROVIDER_REJECTED
        assert exc_info.value.retryable is False
    assert exc_info.value.status_code == status_code
    assert len(capture_server.requests) == 1


@pytest.mark.asyncio
async def test_real_sdk_photonmark_recovery_feeds_extraction_schema(capture_server) -> None:
    """#704 AC4: a recovered body flows through the normal extraction validation."""
    extraction_json = json.dumps(
        {"text": "synthetic problem text", "problemType": "short-answer", "graphDsl": None}
    )
    _serve_envelope(
        capture_server,
        _mixed_envelope(_photonmark_final_payload(extraction_json)),
    )
    from app.infrastructure.vlm.client import VLMClient

    client = VLMClient(
        endpoint=capture_server.url,
        model="deepseek-v4.1-flash",
        api_key="capture-key",
        timeout_seconds=30,
        provider="photonmark",
        api_mode="responses",
        reasoning_effort="high",
    )

    result = await client.extract(image_base64="c3ludGhldGlj")

    assert result.text == "synthetic problem text"
    assert result.problem_type == "short-answer"
    assert result.graph_dsl is None
    assert len(capture_server.requests) == 1
