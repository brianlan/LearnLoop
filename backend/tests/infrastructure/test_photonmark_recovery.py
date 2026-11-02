"""Unit tests for the private Photonmark envelope recovery module (#704).

Parser-level matrix for the exact acceptance/rejection rules; the real-SDK
transport behavior is pinned in ``test_base_vlm_client.py``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from litellm.exceptions import APIError

from app.infrastructure.vlm._photonmark import (
    API_ERROR_WRAPPER_PREFIX,
    KEEPALIVE_RESPONSE_ID,
    parse_keepalive_envelope,
    recover_responses_payload,
)

_WRAPPER = API_ERROR_WRAPPER_PREFIX
# litellm's APIError.__init__ stores `"litellm.APIError: " + message`, so a
# hand-built error carrying this inner prefix reproduces the exact stored
# message the real SDK produces for the observed failure.
_INNER_PREFIX = "APIError: OpenAIException - "

_FINAL = {
    "id": "resp-final",
    "object": "response",
    "created_at": 1700000000,
    "model": "demo-model",
    "status": "completed",
    "error": None,
    "output": [
        {
            "id": "msg-1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "out", "annotations": []}],
        }
    ],
    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
}

_FRAME = (
    f"event: response.in_progress\ndata: {{\"type\":\"response.in_progress\","
    f"\"response\":{{\"id\":\"{KEEPALIVE_RESPONSE_ID}\",\"object\":\"response\","
    f"\"status\":\"in_progress\",\"output\":[]}}}}\n\n"
)


def _api_error(message: str, *, status_code: int | None = 200) -> APIError:
    return APIError(
        status_code=status_code,
        message=message,
        model="demo",
        llm_provider="openai",
    )


def _wrapped(body: str, *, status_code: int | None = 200) -> APIError:
    return _api_error(_INNER_PREFIX + body, status_code=status_code)


class TestParseKeepaliveEnvelope:
    def test_plain_json_without_frames_is_the_final_object(self) -> None:
        assert parse_keepalive_envelope(json.dumps(_FINAL)) == _FINAL

    def test_one_frame_then_final(self) -> None:
        assert parse_keepalive_envelope(_FRAME + json.dumps(_FINAL) + "\n") == _FINAL

    def test_repeated_frames_lf(self) -> None:
        assert parse_keepalive_envelope(_FRAME * 3 + json.dumps(_FINAL)) == _FINAL

    def test_crlf_line_endings(self) -> None:
        body = (_FRAME * 2 + json.dumps(_FINAL) + "\n").replace("\n", "\r\n")
        assert parse_keepalive_envelope(body) == _FINAL

    def test_multiline_final_json_allowed(self) -> None:
        pretty = json.dumps(_FINAL, indent=2)
        assert parse_keepalive_envelope(_FRAME + pretty + "\n\n") == _FINAL

    @pytest.mark.parametrize(
        "body",
        [
            # Heartbeats only, no terminal object.
            _FRAME,
            # Unknown frame response id.
            _FRAME.replace(KEEPALIVE_RESPONSE_ID, "resp_other") + json.dumps(_FINAL),
            # Frame output not empty.
            _FRAME.replace('"output":[]', '"output":[{"id":"x"}]') + json.dumps(_FINAL),
            # Frame carries a provider error.
            _FRAME.replace(
                '"status":"in_progress"', '"status":"in_progress","error":{"code":"x"}'
            )
            + json.dumps(_FINAL),
            # Data.type mismatch.
            _FRAME.replace('"type":"response.in_progress"', '"type":"response.created"', 1)
            + json.dumps(_FINAL),
            # Unknown event field.
            _FRAME.replace("event: response.in_progress", "event: ping", 1)
            + json.dumps(_FINAL),
            # Malformed heartbeat JSON.
            "event: response.in_progress\ndata: {oops}\n\n" + json.dumps(_FINAL),
            # Missing blank separator after the frame.
            _FRAME.rstrip("\n") + json.dumps(_FINAL),
            # Missing event line before data.
            _FRAME.split("\n", 1)[1] + json.dumps(_FINAL),
            # Truncated final JSON.
            _FRAME + '{"id": "resp-x", "object"',
            # Two final objects.
            _FRAME + json.dumps(_FINAL) + "\n" + json.dumps(_FINAL),
            # Trailing junk after the final object.
            _FRAME + json.dumps(_FINAL) + "\nEND\n",
            # Final body is not an object.
            _FRAME + "[1, 2, 3]",
        ],
    )
    def test_rejected_bodies_return_none(self, body: str) -> None:
        assert parse_keepalive_envelope(body) is None


class TestRecoverResponsesPayload:
    def test_valid_envelope_returns_output_text(self) -> None:
        exc = _wrapped(_FRAME + json.dumps(_FINAL) + "\n")
        assert recover_responses_payload(exc) == {"output_text": "out"}

    def test_final_without_output_text_convenience_field(self) -> None:
        body = _FRAME + json.dumps({k: v for k, v in _FINAL.items() if k != "output_text"})
        assert recover_responses_payload(_wrapped(body)) == {"output_text": "out"}

    def test_non_200_status_is_not_a_recovery_candidate(self) -> None:
        exc = _wrapped(_FRAME + json.dumps(_FINAL), status_code=500)
        assert recover_responses_payload(exc) is None

    def test_missing_status_code_is_not_a_recovery_candidate(self) -> None:
        exc = ValueError(_WRAPPER + _FRAME + json.dumps(_FINAL))
        assert recover_responses_payload(exc) is None

    def test_message_without_verified_wrapper_is_ignored(self) -> None:
        exc = _api_error("some unrelated failure " + json.dumps(_FINAL))
        assert recover_responses_payload(exc) is None

    def test_message_with_partial_wrapper_is_ignored(self) -> None:
        exc = _api_error("litellm.APIError: " + _FRAME + json.dumps(_FINAL))
        assert recover_responses_payload(exc) is None

    @pytest.mark.parametrize(
        "final_override",
        [
            {"status": "failed"},
            {"status": "incomplete"},
            {"error": {"code": "server_error", "message": "boom"}},
            {"object": "other.object"},
            {"output": []},
            {"output": ["garbage"]},
            # Untyped dict entries (the SDK model's dict fallback keeps them).
            {"output": [{"type": "weird_thing", "foo": "bar"}]},
            # Message-shaped dicts the SDK cannot fully type (B1 repros).
            {
                "output": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "output_text", "text": "x", "annotations": []}
                        ],
                    }
                ]
            },
            {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "x", "annotations": []}
                        ],
                    }
                ]
            },
            {
                "output": [
                    {
                        "id": "m",
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": 123, "annotations": []}
                        ],
                    }
                ]
            },
            {
                "output": [
                    {
                        "id": "m",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": "not-a-list",
                    }
                ]
            },
        ],
    )
    def test_invalid_terminal_objects_fail_closed(self, final_override: dict[str, Any]) -> None:
        body = _FRAME + json.dumps(_FINAL | final_override)
        assert recover_responses_payload(_wrapped(body)) is None

    def test_reasoning_only_output_has_no_recoverable_text(self) -> None:
        final = _FINAL | {
            "output": [{"id": "r", "type": "reasoning", "summary": []}],
        }
        body = _FRAME + json.dumps(final)
        assert recover_responses_payload(_wrapped(body)) is None

    def test_typed_reasoning_item_does_not_block_recovery(self) -> None:
        final = _FINAL | {
            "output": [
                {"id": "r", "type": "reasoning", "summary": []},
                *_FINAL["output"],
            ],
        }
        body = _FRAME + json.dumps(final)
        assert recover_responses_payload(_wrapped(body)) == {"output_text": "out"}

    def test_unparseable_body_falls_back_to_none(self) -> None:
        assert recover_responses_payload(_wrapped("total nonsense")) is None


def test_solution_coaching_client_photonmark_routing_and_identity() -> None:
    """#704 AC8: every client family shares the same provider interpretation."""
    from app.infrastructure.config.settings import Settings
    from app.infrastructure.vlm.solution_coaching_client import SolutionVLMClient

    settings = Settings(
        math_solution_vlm_endpoint="https://coach.example/api",
        math_solution_vlm_model="coach-model",
        math_solution_vlm_api_key="key",
        math_solution_vlm_timeout_seconds=30,
        math_solution_vlm_provider="photonmark",
    )
    client = SolutionVLMClient(settings=settings)

    assert client._provider == "photonmark"
    assert client._effective_model == "openai/coach-model"
