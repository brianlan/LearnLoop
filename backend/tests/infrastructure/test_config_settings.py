from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.infrastructure.config.settings import Settings


def test_settings_defaults_to_chat_mode(monkeypatch) -> None:
    """All VLM API mode settings should default to 'chat' for backwards compatibility."""
    monkeypatch.setenv("HELPER_VLM_ENDPOINT", "https://example.com")
    monkeypatch.setenv("HELPER_VLM_MODEL", "test-model")
    monkeypatch.setenv("HELPER_VLM_API_KEY", "test-key")
    
    settings = Settings()
    
    assert settings.helper_vlm_api_mode == "chat"
    assert settings.math_ingestion_vlm_api_mode == "chat"
    assert settings.english_ingestion_vlm_api_mode == "chat"
    assert settings.grading_vlm_api_mode == "chat"
    assert settings.math_solution_vlm_api_mode == "chat"
    assert settings.english_solution_vlm_api_mode == "chat"
    assert settings.math_coaching_vlm_api_mode == "chat"
    assert settings.english_coaching_vlm_api_mode == "chat"


def test_settings_accepts_valid_api_modes(monkeypatch) -> None:
    """Settings should accept both 'chat' and 'responses' as valid API modes."""
    monkeypatch.setenv("HELPER_VLM_ENDPOINT", "https://example.com")
    monkeypatch.setenv("HELPER_VLM_MODEL", "test-model")
    monkeypatch.setenv("HELPER_VLM_API_KEY", "test-key")
    monkeypatch.setenv("HELPER_VLM_API_MODE", "responses")
    monkeypatch.setenv("MATH_INGESTION_VLM_API_MODE", "chat")
    
    settings = Settings()
    
    assert settings.helper_vlm_api_mode == "responses"
    assert settings.math_ingestion_vlm_api_mode == "chat"


def test_settings_rejects_invalid_api_mode(monkeypatch) -> None:
    """Settings should reject invalid API mode values."""
    monkeypatch.setenv("HELPER_VLM_ENDPOINT", "https://example.com")
    monkeypatch.setenv("HELPER_VLM_MODEL", "test-model")
    monkeypatch.setenv("HELPER_VLM_API_KEY", "test-key")
    monkeypatch.setenv("HELPER_VLM_API_MODE", "invalid-mode")
    
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    
    # Check that the error mentions the field name
    error_str = str(exc_info.value)
    assert "helper_vlm_api_mode" in error_str.lower()


def test_all_vlm_roles_have_api_mode_settings(monkeypatch) -> None:
    """Every VLM role should have an api_mode setting."""
    monkeypatch.setenv("HELPER_VLM_ENDPOINT", "https://example.com")
    monkeypatch.setenv("HELPER_VLM_MODEL", "test-model")
    monkeypatch.setenv("HELPER_VLM_API_KEY", "test-key")
    
    settings = Settings()
    
    # All VLM roles should have api_mode settings
    assert hasattr(settings, "helper_vlm_api_mode")
    assert hasattr(settings, "math_ingestion_vlm_api_mode")
    assert hasattr(settings, "english_ingestion_vlm_api_mode")
    assert hasattr(settings, "grading_vlm_api_mode")
    assert hasattr(settings, "math_solution_vlm_api_mode")
    assert hasattr(settings, "english_solution_vlm_api_mode")
    assert hasattr(settings, "math_coaching_vlm_api_mode")
    assert hasattr(settings, "english_coaching_vlm_api_mode")
