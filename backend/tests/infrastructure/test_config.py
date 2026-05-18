from app.infrastructure.config.settings import Settings
from pydantic_settings import SettingsConfigDict


class _IsolatedSettings(Settings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")


def test_settings_load_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_HOST", "127.0.0.1")
    monkeypatch.setenv("APP_PORT", "8080")
    monkeypatch.setenv("APP_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("MONGODB_URI", "mongodb://example/test")
    monkeypatch.setenv("MONGODB_DATABASE", "learnloop-test")
    monkeypatch.setenv("S3_ENDPOINT", "http://localhost:9002")
    monkeypatch.setenv("S3_ACCESS_KEY", "key")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("S3_BUCKET", "media")
    monkeypatch.setenv("S3_REGION", "eu-central-1")
    monkeypatch.setenv("S3_FORCE_PATH_STYLE", "false")
    monkeypatch.setenv("VLM_ENDPOINT", "https://vlm.example/api")
    monkeypatch.setenv("VLM_MODEL", "demo-model")
    monkeypatch.setenv("VLM_API_KEY", "demo-key")
    monkeypatch.setenv("VLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("PREVIEW_EXTRACTING_WINDOW_SECONDS", "18")
    monkeypatch.setenv("SESSION_COOKIE_NAME", "cookie")
    monkeypatch.setenv("SESSION_SECURE", "true")
    monkeypatch.setenv("SESSION_SAMESITE", "strict")

    settings = _IsolatedSettings()

    assert settings.app_env == "test"
    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 8080
    assert settings.app_log_level == "DEBUG"
    assert settings.mongodb_uri == "mongodb://example/test"
    assert settings.mongodb_database == "learnloop-test"
    assert settings.s3_endpoint == "http://localhost:9002"
    assert settings.s3_access_key == "key"
    assert settings.s3_secret_key == "secret"
    assert settings.s3_bucket == "media"
    assert settings.s3_region == "eu-central-1"
    assert settings.s3_force_path_style is False
    assert settings.vlm_endpoint == "https://vlm.example/api"
    assert settings.vlm_model == "demo-model"
    assert settings.vlm_api_key == "demo-key"
    assert settings.vlm_timeout_seconds == 12
    assert settings.preview_extracting_window_seconds == 18
    assert settings.session_cookie_name == "cookie"
    assert settings.session_secure is True
    assert settings.session_samesite == "strict"


def test_settings_defaults_when_environment_missing(monkeypatch) -> None:
    for key in [
        "APP_ENV",
        "APP_HOST",
        "APP_PORT",
        "APP_LOG_LEVEL",
        "MONGODB_URI",
        "MONGODB_DATABASE",
        "S3_ENDPOINT",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "S3_BUCKET",
        "S3_REGION",
        "S3_FORCE_PATH_STYLE",
        "VLM_ENDPOINT",
        "VLM_MODEL",
        "VLM_API_KEY",
        "VLM_TIMEOUT_SECONDS",
        "PREVIEW_EXTRACTING_WINDOW_SECONDS",
        "SESSION_COOKIE_NAME",
        "SESSION_SECURE",
        "SESSION_SAMESITE",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = _IsolatedSettings()

    assert settings.mongodb_uri == "mongodb://localhost:27017/learnloop?replicaSet=rs0&directConnection=true"
    assert settings.s3_force_path_style is True
    assert settings.preview_extracting_window_seconds == 150
    assert settings.session_samesite == "lax"
