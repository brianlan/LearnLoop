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
    monkeypatch.setenv("INGESTION_VLM_ENDPOINT", "https://ingestion.example/api")
    monkeypatch.setenv("INGESTION_VLM_MODEL", "ingestion-model")
    monkeypatch.setenv("INGESTION_VLM_API_KEY", "ingestion-key")
    monkeypatch.setenv("INGESTION_VLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("GRADING_VLM_ENDPOINT", "https://grading.example/api")
    monkeypatch.setenv("GRADING_VLM_MODEL", "grading-model")
    monkeypatch.setenv("GRADING_VLM_API_KEY", "grading-key")
    monkeypatch.setenv("GRADING_VLM_TIMEOUT_SECONDS", "34")
    monkeypatch.setenv("PREVIEW_EXTRACTING_WINDOW_SECONDS", "18")
    monkeypatch.setenv("SOLUTION_LLM_ENDPOINT", "https://solution.example/api")
    monkeypatch.setenv("SOLUTION_LLM_MODEL", "solution-model")
    monkeypatch.setenv("SOLUTION_LLM_API_KEY", "solution-key")
    monkeypatch.setenv("SOLUTION_LLM_TIMEOUT_SECONDS", "56")
    monkeypatch.setenv("COACHING_LLM_ENDPOINT", "https://coaching.example/api")
    monkeypatch.setenv("COACHING_LLM_MODEL", "coaching-model")
    monkeypatch.setenv("COACHING_LLM_API_KEY", "coaching-key")
    monkeypatch.setenv("COACHING_LLM_TIMEOUT_SECONDS", "78")
    monkeypatch.setenv("SOLUTION_WORKER_POLL_INTERVAL_SECONDS", "9")
    monkeypatch.setenv("SOLUTION_TASK_TIMEOUT_MINUTES", "15")
    monkeypatch.setenv("SOLUTION_MAX_RETRIES", "4")
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
    assert settings.ingestion_vlm_endpoint == "https://ingestion.example/api"
    assert settings.ingestion_vlm_model == "ingestion-model"
    assert settings.ingestion_vlm_api_key == "ingestion-key"
    assert settings.ingestion_vlm_timeout_seconds == 12
    assert settings.grading_vlm_endpoint == "https://grading.example/api"
    assert settings.grading_vlm_model == "grading-model"
    assert settings.grading_vlm_api_key == "grading-key"
    assert settings.grading_vlm_timeout_seconds == 34
    assert settings.preview_extracting_window_seconds == 18
    assert settings.solution_llm_endpoint == "https://solution.example/api"
    assert settings.solution_llm_model == "solution-model"
    assert settings.solution_llm_api_key == "solution-key"
    assert settings.solution_llm_timeout_seconds == 56
    assert settings.coaching_llm_endpoint == "https://coaching.example/api"
    assert settings.coaching_llm_model == "coaching-model"
    assert settings.coaching_llm_api_key == "coaching-key"
    assert settings.coaching_llm_timeout_seconds == 78
    assert settings.solution_worker_poll_interval_seconds == 9
    assert settings.solution_task_timeout_minutes == 15
    assert settings.solution_max_retries == 4
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
        "INGESTION_VLM_ENDPOINT",
        "INGESTION_VLM_MODEL",
        "INGESTION_VLM_API_KEY",
        "INGESTION_VLM_TIMEOUT_SECONDS",
        "GRADING_VLM_ENDPOINT",
        "GRADING_VLM_MODEL",
        "GRADING_VLM_API_KEY",
        "GRADING_VLM_TIMEOUT_SECONDS",
        "PREVIEW_EXTRACTING_WINDOW_SECONDS",
        "SOLUTION_LLM_ENDPOINT",
        "SOLUTION_LLM_MODEL",
        "SOLUTION_LLM_API_KEY",
        "SOLUTION_LLM_TIMEOUT_SECONDS",
        "COACHING_LLM_ENDPOINT",
        "COACHING_LLM_MODEL",
        "COACHING_LLM_API_KEY",
        "COACHING_LLM_TIMEOUT_SECONDS",
        "SOLUTION_WORKER_POLL_INTERVAL_SECONDS",
        "SOLUTION_TASK_TIMEOUT_MINUTES",
        "SOLUTION_MAX_RETRIES",
        "SESSION_COOKIE_NAME",
        "SESSION_SECURE",
        "SESSION_SAMESITE",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = _IsolatedSettings()

    assert settings.mongodb_uri == "mongodb://localhost:27017/learnloop?replicaSet=rs0&directConnection=true"
    assert settings.s3_force_path_style is True
    assert settings.preview_extracting_window_seconds == 150
    assert settings.ingestion_vlm_endpoint == "https://example-ingestion-vlm-provider.invalid/api"
    assert settings.ingestion_vlm_model == "replace-me"
    assert settings.ingestion_vlm_api_key == "replace-me"
    assert settings.ingestion_vlm_timeout_seconds == 120
    assert settings.grading_vlm_endpoint == "https://example-grading-vlm-provider.invalid/api"
    assert settings.grading_vlm_model == "replace-me"
    assert settings.grading_vlm_api_key == "replace-me"
    assert settings.grading_vlm_timeout_seconds == 60
    assert settings.solution_llm_endpoint == "https://example-solution-provider.invalid/api"
    assert settings.solution_llm_model == "replace-me"
    assert settings.solution_llm_api_key == "replace-me"
    assert settings.solution_llm_timeout_seconds == 120
    assert settings.coaching_llm_endpoint == "https://example-coaching-provider.invalid/api"
    assert settings.coaching_llm_model == "replace-me"
    assert settings.coaching_llm_api_key == "replace-me"
    assert settings.coaching_llm_timeout_seconds == 60
    assert settings.solution_worker_poll_interval_seconds == 5
    assert settings.solution_task_timeout_minutes == 10
    assert settings.solution_max_retries == 3
    assert settings.session_samesite == "lax"
