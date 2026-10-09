"""Unit tests for the async VLM health probe (issue #654).

Fake transport via ``completion_fn``/``responses_fn`` injection and an
injected ``sleep`` — no network, no real backoff waits.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.infrastructure.config.profile_status import VLM_PROFILE_PREFIXES
from app.infrastructure.config.settings import Settings
from app.infrastructure.vlm import health
from app.infrastructure.vlm.base_client import BaseVLMClient, BaseVLMError

UNCONFIGURED_VALIDATOR2 = dict(
    variant_validator2_vlm_endpoint=(
        "https://example-variant-validator2-vlm-provider.invalid/api"
    ),
    variant_validator2_vlm_model="replace-me",
    variant_validator2_vlm_api_key="replace-me",
)


def _build_settings(**overrides) -> Settings:
    kwargs: dict = {}
    for prefix in VLM_PROFILE_PREFIXES:
        kwargs[f"{prefix}_endpoint"] = f"https://{prefix}.example/api"
        kwargs[f"{prefix}_model"] = f"{prefix}-model"
        kwargs[f"{prefix}_provider"] = "openai"
        kwargs[f"{prefix}_api_key"] = f"canary-{prefix}-key"
        kwargs[f"{prefix}_api_mode"] = "chat"
        kwargs[f"{prefix}_timeout_seconds"] = 10
    kwargs.update(overrides)
    return Settings(**kwargs)


def _ok_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                message=SimpleNamespace(
                    role="assistant",
                    content="OK",
                    reasoning_content=None,
                    provider_specific_fields=None,
                ),
            )
        ]
    )


class _ProbeHarness:
    """Fake transport: records calls, drives per-attempt behavior."""

    def __init__(self, behaviors: list[str] | None = None) -> None:
        self.chat_calls: list[dict] = []
        self.responses_calls: list[dict] = []
        self.created: list[BaseVLMClient] = []
        self.behaviors = list(behaviors) if behaviors is not None else None
        self.sleeps: list[float] = []

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def _next_behavior(self) -> str:
        if self.behaviors is None:
            return "ok"
        return self.behaviors.pop(0) if self.behaviors else "ok"

    async def _completion(self, **kwargs):
        self.chat_calls.append(kwargs)
        behavior = self._next_behavior()
        if behavior == "ok":
            return _ok_response()
        if behavior == "crash":
            # A bare exception (not a BaseVLMError): e.g. a factory/probe bug
            # or an unexpected SDK error type (#689).
            raise ValueError("probe crashed")
        raise BaseVLMError(
            "x" * 500 if behavior == "long" else f"provider down ({behavior})",
            code="vlm-network-error",
            retryable=behavior != "fatal",
        )

    async def _responses(self, **kwargs):
        self.responses_calls.append(kwargs)
        return SimpleNamespace(output_text="OK")

    def factory(self, **client_kwargs) -> BaseVLMClient:
        client = BaseVLMClient(
            completion_fn=self._completion,
            responses_fn=self._responses,
            **client_kwargs,
        )
        self.created.append(client)
        return client


@pytest.mark.asyncio
async def test_probe_reports_ok_for_configured_and_skips_unconfigured() -> None:
    settings = _build_settings(**UNCONFIGURED_VALIDATOR2)
    harness = _ProbeHarness()

    snap = await health.run_probe(
        settings=settings,
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    assert snap["started_at"] and snap["finished_at"]
    # One client per configured profile only; the unconfigured optional
    # profile never touches the network.
    assert len(harness.created) == 10
    for prefix in VLM_PROFILE_PREFIXES:
        entry = snap["profiles"][prefix]
        assert entry["checked_at"]
        if prefix == "variant_validator2_vlm":
            assert entry["status"] == "unconfigured"
        else:
            assert entry["status"] == "ok"
            assert entry["attempts"] == 1
    assert "reason" not in snap["profiles"]["helper_vlm"]


@pytest.mark.asyncio
async def test_probe_request_shape_per_api_mode() -> None:
    settings = _build_settings(
        variant_generator_vlm_api_mode="responses",
        **UNCONFIGURED_VALIDATOR2,
    )
    harness = _ProbeHarness()

    snap = await health.run_probe(
        settings=settings,
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    assert snap["profiles"]["variant_generator_vlm"]["status"] == "ok"
    assert harness.chat_calls and harness.responses_calls
    responses_call = harness.responses_calls[0]
    assert responses_call["input"].startswith("Reply with OK")
    assert responses_call["timeout"] == health.VLM_HEALTH_TIMEOUT_S
    chat_call = next(
        c for c in harness.chat_calls if c["api_base"] == "https://helper_vlm.example/api"
    )
    assert chat_call["messages"] == [{"role": "user", "content": "Reply with OK"}]
    assert chat_call["timeout"] == health.VLM_HEALTH_TIMEOUT_S


@pytest.mark.asyncio
async def test_retryable_failures_then_ok_records_attempts_and_backoff() -> None:
    settings = _build_settings(**UNCONFIGURED_VALIDATOR2)
    harness = _ProbeHarness(behaviors=["retryable", "retryable"])

    snap = await health.run_probe(
        settings=settings,
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    entry = snap["profiles"]["helper_vlm"]
    assert entry["status"] == "ok"
    assert entry["attempts"] == 3
    assert harness.sleeps == [2, 4]


@pytest.mark.asyncio
async def test_non_retryable_failure_fails_immediately() -> None:
    settings = _build_settings(**UNCONFIGURED_VALIDATOR2)
    harness = _ProbeHarness(behaviors=["fatal"])

    snap = await health.run_probe(
        settings=settings,
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    entry = snap["profiles"]["helper_vlm"]
    assert entry["status"] == "unavailable"
    assert entry["attempts"] == 1
    assert entry["reason"] == "provider down (fatal)"
    assert entry["code"] == "vlm-network-error"
    assert harness.sleeps == []


@pytest.mark.asyncio
async def test_non_retryable_after_retryable_records_real_attempts() -> None:
    settings = _build_settings(**UNCONFIGURED_VALIDATOR2)
    harness = _ProbeHarness(behaviors=["retryable", "fatal"])

    snap = await health.run_probe(
        settings=settings,
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    entry = snap["profiles"]["helper_vlm"]
    assert entry["status"] == "unavailable"
    # The fatal error happened on attempt 2, after one retryable retry.
    assert entry["attempts"] == 2
    assert harness.sleeps == [2]


@pytest.mark.asyncio
async def test_exhausted_retries_report_unavailable_with_full_reason() -> None:
    settings = _build_settings(**UNCONFIGURED_VALIDATOR2)
    harness = _ProbeHarness(behaviors=["long", "long", "long"])

    snap = await health.run_probe(
        settings=settings,
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    entry = snap["profiles"]["helper_vlm"]
    assert entry["status"] == "unavailable"
    assert entry["attempts"] == 3
    # No truncation: the diagnostic tail of a provider error must survive (#689).
    assert len(entry["reason"]) == 500
    assert harness.sleeps == [2, 4]


@pytest.mark.asyncio
async def test_factory_crash_isolated_to_single_profile() -> None:
    """One profile whose client factory raises must not abort the run (#689)."""
    settings = _build_settings(**UNCONFIGURED_VALIDATOR2)
    harness = _ProbeHarness()

    def exploding_factory(**client_kwargs) -> BaseVLMClient:
        if client_kwargs["endpoint"] == "https://helper_vlm.example/api":
            raise ValueError("bad client config")
        return harness.factory(**client_kwargs)

    snap = await health.run_probe(
        settings=settings,
        client_factory=exploding_factory,
        sleep=harness._sleep,
    )

    entry = snap["profiles"]["helper_vlm"]
    assert entry["status"] == "unavailable"
    assert "bad client config" in entry["reason"]
    assert entry["attempts"] == 1
    # Every other configured profile was still probed in this same run.
    assert snap["profiles"]["math_ingestion_vlm"]["status"] == "ok"
    assert snap["finished_at"]


@pytest.mark.asyncio
async def test_probe_bare_exception_isolated_to_single_profile() -> None:
    """A bare exception from the probe must not wipe the whole snapshot (#689)."""
    settings = _build_settings(**UNCONFIGURED_VALIDATOR2)
    harness = _ProbeHarness(behaviors=["crash"])

    snap = await health.run_probe(
        settings=settings,
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    entry = snap["profiles"]["helper_vlm"]
    assert entry["status"] == "unavailable"
    assert "probe crashed" in entry["reason"]
    assert entry["attempts"] == 1
    assert snap["profiles"]["math_ingestion_vlm"]["status"] == "ok"


@pytest.mark.asyncio
async def test_run_stored_probe_propagates_cancellation() -> None:
    """``except Exception`` must not swallow cancellation (#689)."""
    settings = _build_settings(**UNCONFIGURED_VALIDATOR2)
    started = asyncio.Event()

    async def hanging_completion(**kwargs):
        started.set()
        await asyncio.Event().wait()  # never completes
        raise AssertionError("unreachable")

    def hanging_factory(**client_kwargs) -> BaseVLMClient:
        return BaseVLMClient(completion_fn=hanging_completion, **client_kwargs)

    task = asyncio.create_task(
        health.run_stored_probe(settings, client_factory=hanging_factory)
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert health._running is False


@pytest.mark.asyncio
async def test_misconfigured_reported_without_network() -> None:
    settings = _build_settings(grading_vlm_api_key="replace-me")
    harness = _ProbeHarness()

    snap = await health.run_probe(
        settings=settings,
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    entry = snap["profiles"]["grading_vlm"]
    assert entry["status"] == "misconfigured"
    assert entry["checked_at"]
    assert len(harness.created) == 10


@pytest.mark.asyncio
async def test_probe_snapshot_never_contains_api_keys() -> None:
    settings = _build_settings(**UNCONFIGURED_VALIDATOR2)
    harness = _ProbeHarness()

    snap = await health.run_probe(
        settings=settings,
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    serialized = json.dumps(snap)
    for prefix in VLM_PROFILE_PREFIXES:
        assert f"canary-{prefix}-key" not in serialized

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert "api_key" not in key.lower(), key
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(snap)


def test_snapshot_idle_shape() -> None:
    assert health.snapshot() == {
        "running": False,
        "started_at": None,
        "finished_at": None,
        "profiles": {},
    }


@pytest.mark.asyncio
async def test_begin_run_guard_and_reset_after_run() -> None:
    assert health.begin_run() is True
    assert health.begin_run() is False

    harness = _ProbeHarness()
    await health.run_stored_probe(
        _build_settings(**UNCONFIGURED_VALIDATOR2),
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    assert health._running is False
    assert health.begin_run() is True
    health._running = False  # leave module state clean for other tests


@pytest.mark.asyncio
async def test_probe_request_carries_configured_reasoning_effort() -> None:
    """The health probe request carries each profile's configured effort (#677)."""
    settings = _build_settings(
        helper_vlm_reasoning_effort="low",
        **UNCONFIGURED_VALIDATOR2,
    )
    harness = _ProbeHarness()

    snap = await health.run_probe(
        settings=settings,
        client_factory=harness.factory,
        sleep=harness._sleep,
    )

    assert snap["profiles"]["helper_vlm"]["status"] == "ok"
    chat_call = next(
        c for c in harness.chat_calls if c["api_base"] == "https://helper_vlm.example/api"
    )
    assert chat_call["reasoning_effort"] == "low"
    # Profiles left at the default still send the default value.
    other_call = next(
        c
        for c in harness.chat_calls
        if c["api_base"] == "https://math_ingestion_vlm.example/api"
    )
    assert other_call["reasoning_effort"] == "high"
