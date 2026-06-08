from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.infrastructure.config.settings import Settings
from app.main import create_app
from app.presentation import settings as settings_presentation


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(
        settings_presentation,
        "get_settings",
        lambda: Settings(
            ingestion_vlm_endpoint="https://ingestion.example/api",
            ingestion_vlm_model="ingestion-model",
            ingestion_vlm_timeout_seconds=12,
            grading_vlm_endpoint="https://grading.example/api",
            grading_vlm_model="grading-model",
            grading_vlm_timeout_seconds=34,
            math_solution_vlm_endpoint="https://math-solution.example/api",
            math_solution_vlm_model="math-solution-model",
            math_solution_vlm_timeout_seconds=56,
            english_solution_vlm_endpoint="https://english-solution.example/api",
            english_solution_vlm_model="english-solution-model",
            english_solution_vlm_timeout_seconds=57,
            math_coaching_vlm_endpoint="https://math-coaching.example/api",
            math_coaching_vlm_model="math-coaching-model",
            math_coaching_vlm_timeout_seconds=78,
            english_coaching_vlm_endpoint="https://english-coaching.example/api",
            english_coaching_vlm_model="english-coaching-model",
            english_coaching_vlm_timeout_seconds=79,
            preview_extracting_window_seconds=18,
        ),
    )
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_settings_info_exposes_explicit_ai_profiles(client: AsyncClient) -> None:
    response = await client.get("/api/v1/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ingestion_vlm"] == {
        "endpoint": "https://ingestion.example/api",
        "model": "ingestion-model",
        "timeout_seconds": 12,
        "preview_extracting_window_seconds": 18,
    }
    assert payload["grading_vlm"] == {
        "endpoint": "https://grading.example/api",
        "model": "grading-model",
        "timeout_seconds": 34,
    }
    assert payload["math_solution_vlm"] == {
        "endpoint": "https://math-solution.example/api",
        "model": "math-solution-model",
        "timeout_seconds": 56,
    }
    assert payload["english_solution_vlm"] == {
        "endpoint": "https://english-solution.example/api",
        "model": "english-solution-model",
        "timeout_seconds": 57,
    }
    assert payload["math_coaching_vlm"] == {
        "endpoint": "https://math-coaching.example/api",
        "model": "math-coaching-model",
        "timeout_seconds": 78,
    }
    assert payload["english_coaching_vlm"] == {
        "endpoint": "https://english-coaching.example/api",
        "model": "english-coaching-model",
        "timeout_seconds": 79,
    }
    assert "vlm" not in payload
