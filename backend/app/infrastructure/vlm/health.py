"""Async post-launch VLM availability probing (issue #654).

Probes each *configured* VLM profile with one minimal real request and keeps
an in-memory snapshot surfaced at ``GET /settings/vlm-health``. Never blocks
startup and never raises into callers: a dead third-party provider must not
delay the site or take it down.

# ponytail: in-memory and single-process (bare uvicorn, no ``--workers``);
sequential probing (nothing waits on it; worst case ~12 min when every
provider is down). Persistence or a periodic interval can be added later —
``finished_at`` makes staleness visible meanwhile.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Callable

from app.infrastructure.config.profile_status import (
    PROFILE_STATUS_CONFIGURED,
    VLM_PROFILE_PREFIXES,
    profile_status,
)
from app.infrastructure.vlm.base_client import BaseVLMClient, BaseVLMError

logger = logging.getLogger(__name__)

VLM_HEALTH_ATTEMPTS = 3
# Independent of profile timeouts on purpose: math_ingestion runs real 1200s
# requests, but an availability ping must fail fast.
VLM_HEALTH_TIMEOUT_S = 20
VLM_HEALTH_BACKOFF_S = (2, 4)

VLM_HEALTH_OK = "ok"
VLM_HEALTH_UNAVAILABLE = "unavailable"

_snapshot: dict | None = None
_running = False
_task: asyncio.Task | None = None


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


async def _probe_once(client: BaseVLMClient) -> None:
    """Send the minimal availability ping via the profile's API mode."""
    if client._api_mode == "responses":
        await client._send_responses_request({"input": "Reply with OK"})
    else:
        await client._send_chat_completion(
            {"messages": [{"role": "user", "content": "Reply with OK"}]}
        )


async def _probe_with_retries(
    client: BaseVLMClient,
    *,
    attempts: int,
    backoff: tuple[float, ...],
    sleep: Callable[[float], Any],
) -> int:
    """Probe until success; returns the attempt number used.

    Retryable failures back off and retry; non-retryable ones raise
    immediately. When attempts are exhausted the last error is re-raised.
    """
    last_error: BaseVLMError | None = None
    for attempt in range(1, attempts + 1):
        try:
            await _probe_once(client)
            return attempt
        except BaseVLMError as exc:
            # Stamp the actual attempt so a non-retryable failure after
            # earlier retries reports the real attempt count.
            exc.attempt = attempt  # type: ignore[attr-defined]
            if not exc.retryable:
                raise
            last_error = exc
            if attempt < attempts:
                await sleep(backoff[min(attempt - 1, len(backoff) - 1)])
    assert last_error is not None
    raise last_error


async def run_probe(
    *,
    settings,
    attempts: int = VLM_HEALTH_ATTEMPTS,
    timeout_s: float = VLM_HEALTH_TIMEOUT_S,
    client_factory: Callable[..., BaseVLMClient] = BaseVLMClient,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> dict:
    """Probe every configured profile sequentially; returns the run snapshot.

    Unconfigured/misconfigured profiles get a snapshot entry without any
    network call (a partial config must not burn 3x20s on a deterministic
    error). Success is any transport-level 200 — no content parsing.
    """
    started_at = _utcnow()
    profiles: dict[str, dict] = {}
    for prefix in VLM_PROFILE_PREFIXES:
        endpoint = getattr(settings, f"{prefix}_endpoint")
        model = getattr(settings, f"{prefix}_model")
        provider = getattr(settings, f"{prefix}_provider")
        api_mode = getattr(settings, f"{prefix}_api_mode")
        api_key = getattr(settings, f"{prefix}_api_key")
        reasoning_effort = getattr(settings, f"{prefix}_reasoning_effort")
        status = profile_status(endpoint=endpoint, model=model, api_key=api_key)
        entry: dict[str, Any] = {"status": status, "checked_at": _utcnow()}
        if status == PROFILE_STATUS_CONFIGURED:
            client: BaseVLMClient | None = None
            try:
                client = client_factory(
                    endpoint=endpoint,
                    model=model,
                    api_key=api_key,
                    provider=provider,
                    api_mode=api_mode,
                    reasoning_effort=reasoning_effort,
                    timeout_seconds=timeout_s,
                )
                used = await _probe_with_retries(
                    client, attempts=attempts, backoff=VLM_HEALTH_BACKOFF_S, sleep=sleep
                )
                entry["status"] = VLM_HEALTH_OK
                entry["attempts"] = used
            except BaseVLMError as exc:
                entry["status"] = VLM_HEALTH_UNAVAILABLE
                entry["reason"] = str(exc)
                entry["code"] = exc.code
                entry["attempts"] = getattr(exc, "attempt", 1)
            except Exception as exc:
                # One misconfigured/crashing profile must not abort the run and
                # wipe the snapshot for every other profile (#689).
                entry["status"] = VLM_HEALTH_UNAVAILABLE
                entry["reason"] = str(exc)
                entry["attempts"] = 1
            finally:
                if client is not None:
                    await client.aclose()
        profiles[prefix] = entry
    return {"started_at": started_at, "finished_at": _utcnow(), "profiles": profiles}


def snapshot() -> dict:
    """Current in-memory health snapshot (idle-shaped before the first run)."""
    run = _snapshot or {"started_at": None, "finished_at": None, "profiles": {}}
    return {"running": _running, **run}


def begin_run() -> bool:
    """Claim a run synchronously, before any await; False if one is active."""
    global _running
    if _running:
        return False
    _running = True
    return True


def spawn(settings, **probe_kwargs) -> None:
    """Start a claimed run as a background task, holding the task reference
    so the event loop cannot garbage-collect it mid-flight."""
    global _task
    _task = asyncio.create_task(run_stored_probe(settings, **probe_kwargs))


async def run_stored_probe(settings, **probe_kwargs) -> None:
    """Run one probe pass and store the snapshot.

    The caller must have claimed the run via ``begin_run``. Errors are logged,
    never raised, so the probe can neither crash startup nor shutdown;
    cancellation still propagates (``except Exception``) and the running flag
    always resets.
    """
    global _snapshot, _running
    try:
        _snapshot = await run_probe(settings=settings, **probe_kwargs)
    except Exception:
        logger.exception("VLM health probe run failed")
    finally:
        _running = False
