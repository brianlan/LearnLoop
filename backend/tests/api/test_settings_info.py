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
            solution_llm_endpoint="https://solution.example/api",
            solution_llm_model="solution-model",
            solution_llm_timeout_seconds=56,
            coaching_llm_endpoint="https://coaching.example/api",
            coaching_llm_model="coaching-model",
            coaching_llm_timeout_seconds=78,
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
    assert payload["solution_llm"] == {
        "endpoint": "https://solution.example/api",
        "model": "solution-model",
        "timeout_seconds": 56,
    }
    assert payload["coaching_llm"] == {
        "endpoint": "https://coaching.example/api",
        "model": "coaching-model",
        "timeout_seconds": 78,
    }
    assert "vlm" not in payload
