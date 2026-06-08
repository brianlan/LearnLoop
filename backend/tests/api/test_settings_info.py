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
            helper_vlm_endpoint="https://helper.example/api",
            helper_vlm_model="helper-model",
            helper_vlm_timeout_seconds=12,
            math_ingestion_vlm_endpoint="https://math-ingestion.example/api",
            math_ingestion_vlm_model="math-ingestion-model",
            math_ingestion_vlm_timeout_seconds=22,
            english_ingestion_vlm_endpoint="https://english-ingestion.example/api",
            english_ingestion_vlm_model="english-ingestion-model",
            english_ingestion_vlm_timeout_seconds=32,
            grading_vlm_endpoint="https://grading.example/api",
            grading_vlm_model="grading-model",
            grading_vlm_timeout_seconds=34,
            solution_vlm_endpoint="https://solution.example/api",
            solution_vlm_model="solution-model",
            solution_vlm_timeout_seconds=56,
            coaching_vlm_endpoint="https://coaching.example/api",
            coaching_vlm_model="coaching-model",
            coaching_vlm_timeout_seconds=78,
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
    assert payload["helper_vlm"] == {
        "endpoint": "https://helper.example/api",
        "model": "helper-model",
        "timeout_seconds": 12,
    }
    assert payload["math_ingestion_vlm"] == {
        "endpoint": "https://math-ingestion.example/api",
        "model": "math-ingestion-model",
        "timeout_seconds": 22,
    }
    assert payload["english_ingestion_vlm"] == {
        "endpoint": "https://english-ingestion.example/api",
        "model": "english-ingestion-model",
        "timeout_seconds": 32,
    }
    assert payload["preview_extracting_window_seconds"] == 18
    assert payload["grading_vlm"] == {
        "endpoint": "https://grading.example/api",
        "model": "grading-model",
        "timeout_seconds": 34,
    }
    assert payload["solution_vlm"] == {
        "endpoint": "https://solution.example/api",
        "model": "solution-model",
        "timeout_seconds": 56,
    }
    assert payload["coaching_vlm"] == {
        "endpoint": "https://coaching.example/api",
        "model": "coaching-model",
        "timeout_seconds": 78,
    }
    assert "vlm" not in payload
