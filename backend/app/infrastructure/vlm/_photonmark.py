"""Bounded Photonmark Responses keepalive-envelope recovery (#704).

Photonmark's gateway answers non-streaming ``/responses`` requests with HTTP
200, ``Content-Type: text/event-stream``, a synthetic ``response.in_progress``
keepalive frame (response id ``resp_photonmark_keepalive``) and then a bare,
complete Responses JSON object instead of a properly framed terminal SSE
event. The real SDK tries ``raw_response.json()``, fails, and raises
``litellm.exceptions.APIError`` with ``status_code=200`` while the full mixed
body survives only inside the exception message behind the verified wrapper
``litellm.APIError: APIError: OpenAIException - ``.

This module recovers that already-received successful result without a second
HTTP request. Every check is fail-closed: a body that does not match the
observed envelope exactly keeps the existing failure path. Only an explicit
``provider=photonmark`` selection reaches this code (see ``BaseVLMClient``).
"""

from __future__ import annotations

import json
from typing import Any

from litellm.types.llms.openai import ResponsesAPIResponse
from pydantic import ValidationError

# The exact message wrapper litellm 1.104.2 puts in front of the raw response
# text for this failure shape. If an SDK change alters it, recovery stops
# happening (fail closed) instead of misparsing unrelated messages.
API_ERROR_WRAPPER_PREFIX = "litellm.APIError: APIError: OpenAIException - "

# Synthetic response id Photonmark uses for keepalive-only frames.
KEEPALIVE_RESPONSE_ID = "resp_photonmark_keepalive"

_KEEPALIVE_EVENT = "response.in_progress"


def _is_recognized_keepalive_frame(event_field: str, data: Any) -> bool:
    """Only the known heartbeat: matching event/type, keepalive id,
    empty output, and no provider error."""
    if event_field != _KEEPALIVE_EVENT or not isinstance(data, dict):
        return False
    if data.get("type") != _KEEPALIVE_EVENT or data.get("error") is not None:
        return False
    response = data.get("response")
    if not isinstance(response, dict):
        return False
    return (
        response.get("id") == KEEPALIVE_RESPONSE_ID
        and response.get("output") == []
        and response.get("error") is None
    )


def parse_keepalive_envelope(raw: str) -> dict[str, Any] | None:
    """Parse known keepalive frames plus exactly one final Responses object.

    Returns the final object, or None when the body does not match the known
    envelope (the caller keeps the existing failure path). No brace-searching
    and no fragment splicing: recognized frames are consumed line by line and
    everything after the last frame's blank line must be one complete JSON
    object with only trailing whitespace.
    """
    lines = [line.rstrip("\r") for line in raw.split("\n")]
    i = 0
    while i < len(lines):
        event_line = lines[i]
        if not event_line.startswith("event:"):
            break
        event_field = event_line[len("event:") :].strip()
        i += 1
        if i >= len(lines) or not lines[i].startswith("data:"):
            return None
        try:
            data = json.loads(lines[i][len("data:") :].strip())
        except json.JSONDecodeError:
            return None
        if not _is_recognized_keepalive_frame(event_field, data):
            return None
        i += 1
        if i >= len(lines) or lines[i] != "":
            return None
        i += 1
    try:
        final = json.loads("\n".join(lines[i:]))
    except json.JSONDecodeError:
        return None
    return final if isinstance(final, dict) else None


def recover_responses_payload(exc: BaseException) -> dict[str, Any] | None:
    """Return the recovered ``{"output_text": ...}`` payload, or None.

    Requires the observed HTTP-200 parse-failure shape: an exception carrying
    ``status_code=200`` whose message is the verified wrapper followed by the
    full mixed body, whose leading keepalive frames are all recognized, and
    whose single terminal object is a structurally valid completed response
    with non-empty output text. Anything else returns None (fail closed).
    """
    status_code = getattr(exc, "status_code", None)
    message = getattr(exc, "message", None) or str(exc)
    if status_code != 200 or not message.startswith(API_ERROR_WRAPPER_PREFIX):
        return None
    final = parse_keepalive_envelope(message[len(API_ERROR_WRAPPER_PREFIX) :])
    if (
        final is None
        or final.get("object") != "response"
        or final.get("status") != "completed"
        or final.get("error") is not None
    ):
        return None
    try:
        response = ResponsesAPIResponse.model_validate(final)
    except ValidationError:
        return None
    if not response.output_text:
        return None
    return {"output_text": response.output_text}
