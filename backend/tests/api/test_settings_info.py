from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.infrastructure.config.settings import Settings
from app.main import create_app
from app.presentation import settings as settings_presentation


# Distinctive per-profile api_key values: the canary test proves none of them
# leaks into the serialized settings payload (nor any api_key-named key).
CANARY_KEYS = {
    "helper_vlm": "canary-helper-vlm-key",
    "math_ingestion_vlm": "canary-math-ingestion-vlm-key",
    "english_ingestion_vlm": "canary-english-ingestion-vlm-key",
    "grading_vlm": "canary-grading-vlm-key",
    "math_solution_vlm": "canary-math-solution-vlm-key",
    "english_solution_vlm": "canary-english-solution-vlm-key",
    "math_coaching_vlm": "canary-math-coaching-vlm-key",
    "english_coaching_vlm": "canary-english-coaching-vlm-key",
    "variant_generator_vlm": "canary-variant-generator-vlm-key",
    "variant_validator_vlm": "canary-variant-validator-vlm-key",
    # validator2 stays fully unconfigured (placeholder defaults): its status
    # must compute "unconfigured", which is normal for the optional profile.
}


def _build_settings() -> Settings:
    kwargs: dict = {}
    for prefix, canary in CANARY_KEYS.items():
        kwargs[f"{prefix}_endpoint"] = f"https://{prefix}.example/api"
        kwargs[f"{prefix}_model"] = f"{prefix}-model"
        kwargs[f"{prefix}_provider"] = "openai"
        kwargs[f"{prefix}_api_key"] = canary
        kwargs[f"{prefix}_api_mode"] = "chat"
        kwargs[f"{prefix}_timeout_seconds"] = 10
    return Settings(
        **kwargs,
        preview_extracting_window_seconds=18,
    )


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(
        settings_presentation,
        "get_settings",
        _build_settings,
    )
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


ALL_VLM_PROFILES = [
    "helper_vlm",
    "math_ingestion_vlm",
    "english_ingestion_vlm",
    "grading_vlm",
    "math_solution_vlm",
    "english_solution_vlm",
    "math_coaching_vlm",
    "english_coaching_vlm",
    "variant_generator_vlm",
    "variant_validator_vlm",
    "variant_validator2_vlm",
]


def _expected_profile(prefix: str, *, status: str) -> dict:
    if prefix == "variant_validator2_vlm":
        return {
            "endpoint": "https://example-variant-validator2-vlm-provider.invalid/api",
            "model": "replace-me",
            "provider": "openai",
            "api_mode": "chat",
            "timeout_seconds": 120.0,
            "status": status,
        }
    return {
        "endpoint": f"https://{prefix}.example/api",
        "model": f"{prefix}-model",
        "provider": "openai",
        "api_mode": "chat",
        "timeout_seconds": 10,
        "status": status,
    }


@pytest.mark.asyncio
async def test_settings_info_exposes_explicit_ai_profiles(client: AsyncClient) -> None:
    response = await client.get("/api/v1/settings")

    assert response.status_code == 200
    payload = response.json()
    for prefix in ALL_VLM_PROFILES:
        if prefix == "variant_validator2_vlm":
            continue
        assert payload[prefix] == _expected_profile(prefix, status="configured"), prefix
    # The optional validator2 profile is left at its placeholder defaults:
    # fully unconfigured is its normal state (#644 semantics).
    assert payload["variant_validator2_vlm"] == _expected_profile(
        "variant_validator2_vlm", status="unconfigured"
    )
    assert len(ALL_VLM_PROFILES) == 11
    assert "vlm" not in payload
    assert payload["preview_extracting_window_seconds"] == 18
    assert payload["problem_selection"] == {
        "cooldown_days": 7,
        "last_wrong_weight": 1.0,
        "failure_rate_weight": 1.0,
        "recency_weight": 1.0,
        "min_problem_age_days": 3,
    }
    assert "practice" not in payload


@pytest.mark.asyncio
async def test_settings_payload_leaks_no_api_key(client: AsyncClient) -> None:
    response = await client.get("/api/v1/settings")

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload)
    for canary in CANARY_KEYS.values():
        assert canary not in serialized

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert "api_key" not in key.lower(), key
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
