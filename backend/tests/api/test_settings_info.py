from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.infrastructure.config.profile_status import VLM_PROFILE_PREFIXES
from app.infrastructure.config.settings import Settings
from app.infrastructure.vlm import health as vlm_health
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
            "reasoning_effort": "high",
            "timeout_seconds": 120.0,
            "status": status,
        }
    return {
        "endpoint": f"https://{prefix}.example/api",
        "model": f"{prefix}-model",
        "provider": "openai",
        "api_mode": "chat",
        "reasoning_effort": "high",
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
async def test_settings_info_exposes_configured_reasoning_effort(monkeypatch) -> None:
    """The payload surfaces each profile's configured reasoning effort (#677)."""
    settings = _build_settings()
    for prefix in ALL_VLM_PROFILES:
        setattr(
            settings,
            f"{prefix}_reasoning_effort",
            "none" if prefix == "helper_vlm" else "xhigh",
        )
    monkeypatch.setattr(settings_presentation, "get_settings", lambda: settings)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/api/v1/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["helper_vlm"]["reasoning_effort"] == "none"
    for prefix in ALL_VLM_PROFILES:
        if prefix != "helper_vlm":
            assert payload[prefix]["reasoning_effort"] == "xhigh", prefix


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


# --- VLM health endpoints (issue #654) -------------------------------


@pytest.fixture(autouse=True)
def _reset_vlm_health_state():
    vlm_health._running = False
    vlm_health._snapshot = None
    yield
    vlm_health._running = False
    vlm_health._snapshot = None


@pytest.mark.asyncio
async def test_vlm_health_snapshot_idle(client: AsyncClient) -> None:
    response = await client.get("/api/v1/settings/vlm-health")

    assert response.status_code == 200
    assert response.json() == {
        "running": False,
        "started_at": None,
        "finished_at": None,
        "profiles": {},
    }


@pytest.mark.asyncio
async def test_vlm_health_run_conflict_returns_409(client: AsyncClient) -> None:
    assert vlm_health.begin_run() is True

    response = await client.post("/api/v1/settings/vlm-health/run")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_vlm_health_run_spawns_and_stores_snapshot_without_api_keys(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    canned = {
        "started_at": "started",
        "finished_at": "finished",
        "profiles": {
            "helper_vlm": {"status": "ok", "attempts": 1, "checked_at": "finished"}
        },
    }

    async def fake_run_probe(*, settings, **_):
        return canned

    monkeypatch.setattr(vlm_health, "run_probe", fake_run_probe)

    response = await client.post("/api/v1/settings/vlm-health/run")

    assert response.status_code == 202
    assert response.json()["running"] is True
    # Let the spawned background run finish on the test loop.
    await asyncio.sleep(0)

    final_response = await client.get("/api/v1/settings/vlm-health")
    assert final_response.status_code == 200
    final = final_response.json()
    assert final["running"] is False
    assert final["started_at"] == "started"
    assert final["profiles"]["helper_vlm"]["status"] == "ok"

    serialized = json.dumps(final)
    for canary in CANARY_KEYS.values():
        assert canary not in serialized
    assert list(VLM_PROFILE_PREFIXES) == ALL_VLM_PROFILES
