"""``app.main`` variation worker wiring: the optional second validator (#644).

Construction does no I/O, so real clients are built and only
``run_variation_worker`` is stubbed to capture the validators list.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from app.infrastructure.config.settings import Settings
from app.main import _run_variation_worker_with_logging

_MANDATORY_PROFILES: dict[str, Any] = {
    "variant_generator_vlm_endpoint": "https://variant-generator.test/api",
    "variant_generator_vlm_model": "gen-model",
    "variant_generator_vlm_api_key": "sk-gen",
    "variant_validator_vlm_endpoint": "https://variant-validator.test/api",
    "variant_validator_vlm_model": "val-model",
    "variant_validator_vlm_api_key": "sk-val",
    "helper_vlm_endpoint": "https://helper.test/api",
    "helper_vlm_model": "helper-model",
    "helper_vlm_api_key": "sk-helper",
}

_SECOND_PROFILE: dict[str, Any] = {
    "variant_validator2_vlm_endpoint": "https://variant-validator2.test/api",
    "variant_validator2_vlm_model": "val2-model",
    "variant_validator2_vlm_api_key": "sk-val2",
}


def _capture_run_variation_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_run_variation_worker(
        database: Any,
        settings: Settings,
        generator: Any,
        validators: list[Any],
        helper: Any,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        captured["validators"] = validators
        captured["generator"] = generator
        captured["helper"] = helper

    monkeypatch.setattr("app.main.run_variation_worker", fake_run_variation_worker)
    return captured


@pytest.mark.asyncio
async def test_worker_filters_unconfigured_second_validator(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unconfigured validator2 → one-element list; the worker still runs.

    Pins the None filter: a None slipping past raises AttributeError inside
    generate_and_validate, which only catches BaseVLMError, killing the worker
    task at the first work item.
    """
    captured = _capture_run_variation_worker(monkeypatch)

    with caplog.at_level(logging.INFO, logger="app.main"):
        await _run_variation_worker_with_logging(
            None, Settings(**_MANDATORY_PROFILES), asyncio.Event()
        )

    assert len(captured["validators"]) == 1
    assert captured["generator"] is not None
    assert captured["helper"] is not None
    messages = [record.getMessage() for record in caplog.records]
    assert not any("Variation worker disabled" in message for message in messages)
    assert any("val-model" in message for message in messages)


@pytest.mark.asyncio
async def test_worker_passes_two_validators_when_second_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_variation_worker(monkeypatch)
    await _run_variation_worker_with_logging(
        None, Settings(**{**_MANDATORY_PROFILES, **_SECOND_PROFILE}), asyncio.Event()
    )
    assert len(captured["validators"]) == 2
    models = [v.identity["model"] for v in captured["validators"]]
    assert models == ["val-model", "val2-model"]
    await captured["generator"].aclose()


@pytest.mark.asyncio
async def test_worker_disabled_when_mandatory_profile_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    captured = _capture_run_variation_worker(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        await _run_variation_worker_with_logging(None, Settings(), asyncio.Event())
    assert "validators" not in captured
    assert any(
        "Variation worker disabled" in record.getMessage() for record in caplog.records
    )
