"""Characterization tests for VLMClient construction through role builders.

These tests pin the exact constructor kwargs and lifecycle ownership for every
production VLMClient construction path so the builder refactor is provably
behavior-preserving.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.infrastructure.config.settings import Settings
from app.infrastructure.vlm.client import (
    VLMClient,
    build_english_ingestion_vlm_client,
    build_grading_vlm_client,
    build_helper_vlm_client,
    build_math_ingestion_vlm_client,
)
from app.infrastructure.vlm.prompts import (
    ENGLISH_EXTRACTION_SYSTEM_PROMPT,
    MATH_EXTRACTION_SYSTEM_PROMPT,
)


def _settings(**overrides: Any) -> Settings:
    defaults = {
        "helper_vlm_endpoint": "https://helper.example/api",
        "helper_vlm_model": "helper-model",
        "helper_vlm_api_key": "helper-key",
        "helper_vlm_timeout_seconds": 30.0,
        "helper_vlm_provider": "openai",
        "helper_vlm_api_mode": "chat",
        "math_ingestion_vlm_endpoint": "https://math.example/api",
        "math_ingestion_vlm_model": "math-model",
        "math_ingestion_vlm_api_key": "math-key",
        "math_ingestion_vlm_timeout_seconds": 45.0,
        "math_ingestion_vlm_provider": "openai",
        "math_ingestion_vlm_api_mode": "chat",
        "english_ingestion_vlm_endpoint": "https://english.example/api",
        "english_ingestion_vlm_model": "english-model",
        "english_ingestion_vlm_api_key": "english-key",
        "english_ingestion_vlm_timeout_seconds": 60.0,
        "english_ingestion_vlm_provider": "openai",
        "english_ingestion_vlm_api_mode": "chat",
        "grading_vlm_endpoint": "https://grading.example/api",
        "grading_vlm_model": "grading-model",
        "grading_vlm_api_key": "grading-key",
        "grading_vlm_timeout_seconds": 90.0,
        "grading_vlm_provider": "openai",
        "grading_vlm_api_mode": "chat",
    }
    defaults.update(overrides)
    return Settings(**defaults)


class _RecordingVLMClient(VLMClient):
    def __init__(self, *, closed: list[bool], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._closed = closed

    async def aclose(self) -> None:
        self._closed.append(True)
        await super().aclose()


def _recording_build_client(closed: list[bool] | None = None, **kwargs: Any) -> _RecordingVLMClient:
    return _RecordingVLMClient(closed=closed or [], **kwargs)


def test_build_helper_vlm_client_passes_expected_kwargs(monkeypatch: Any) -> None:
    settings = _settings()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.infrastructure.vlm.client._build_vlm_client",
        lambda *, closed=None, **kwargs: (calls.append(kwargs) or _recording_build_client(closed=closed, **kwargs)),
    )

    client = build_helper_vlm_client(settings)

    assert len(calls) == 1
    assert client.model == "helper-model"
    assert calls[0] == {
        "endpoint": "https://helper.example/api",
        "model": "helper-model",
        "api_key": "helper-key",
        "timeout_seconds": 30.0,
        "provider": "openai",
        "api_mode": "chat",
    }


def test_build_math_ingestion_vlm_client_passes_expected_kwargs(monkeypatch: Any) -> None:
    settings = _settings()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.infrastructure.vlm.client._build_vlm_client",
        lambda *, closed=None, **kwargs: (calls.append(kwargs) or _recording_build_client(closed=closed, **kwargs)),
    )

    client = build_math_ingestion_vlm_client(settings)

    assert client.model == "math-model"
    assert calls[0] == {
        "endpoint": "https://math.example/api",
        "model": "math-model",
        "api_key": "math-key",
        "timeout_seconds": 45.0,
        "provider": "openai",
        "api_mode": "chat",
    }


def test_build_english_ingestion_vlm_client_passes_expected_kwargs(monkeypatch: Any) -> None:
    settings = _settings()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.infrastructure.vlm.client._build_vlm_client",
        lambda *, closed=None, **kwargs: (calls.append(kwargs) or _recording_build_client(closed=closed, **kwargs)),
    )

    client = build_english_ingestion_vlm_client(settings)

    assert client.model == "english-model"
    assert calls[0] == {
        "endpoint": "https://english.example/api",
        "model": "english-model",
        "api_key": "english-key",
        "timeout_seconds": 60.0,
        "provider": "openai",
        "api_mode": "chat",
        "extraction_system_prompt": ENGLISH_EXTRACTION_SYSTEM_PROMPT,
        "request_correct_answer": True,
    }


def test_build_grading_vlm_client_passes_expected_kwargs(monkeypatch: Any) -> None:
    settings = _settings()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.infrastructure.vlm.client._build_vlm_client",
        lambda *, closed=None, **kwargs: (calls.append(kwargs) or _recording_build_client(closed=closed, **kwargs)),
    )

    client = build_grading_vlm_client(settings)

    assert client.model == "grading-model"
    assert calls[0] == {
        "endpoint": "https://grading.example/api",
        "model": "grading-model",
        "api_key": "grading-key",
        "timeout_seconds": 90.0,
        "provider": "openai",
        "api_mode": "chat",
    }


def test_builders_honor_api_mode_override(monkeypatch: Any) -> None:
    settings = _settings(
        helper_vlm_api_mode="responses",
        math_ingestion_vlm_api_mode="responses",
        english_ingestion_vlm_api_mode="responses",
        grading_vlm_api_mode="responses",
    )
    monkeypatch.setattr(
        "app.infrastructure.vlm.client._build_vlm_client",
        lambda *, closed=None, **kwargs: _recording_build_client(closed=closed, **kwargs),
    )

    assert build_helper_vlm_client(settings)._api_mode == "responses"
    assert build_math_ingestion_vlm_client(settings)._api_mode == "responses"
    assert build_english_ingestion_vlm_client(settings)._api_mode == "responses"
    assert build_grading_vlm_client(settings)._api_mode == "responses"


@pytest.mark.asyncio
async def test_build_grading_vlm_client_produces_closable_client() -> None:
    settings = _settings()
    closed: list[bool] = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "app.infrastructure.vlm.client._build_vlm_client",
        lambda **kwargs: _RecordingVLMClient(closed=closed, **kwargs),
    )

    client = build_grading_vlm_client(settings)
    await client.aclose()

    assert closed == [True]
    monkeypatch.undo()
