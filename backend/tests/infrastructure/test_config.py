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
    monkeypatch.setenv("HELPER_VLM_ENDPOINT", "https://helper.example/api")
    monkeypatch.setenv("HELPER_VLM_MODEL", "helper-model")
    monkeypatch.setenv("HELPER_VLM_API_KEY", "helper-key")
    monkeypatch.setenv("HELPER_VLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("MATH_INGESTION_VLM_ENDPOINT", "https://math-ingestion.example/api")
    monkeypatch.setenv("MATH_INGESTION_VLM_MODEL", "math-ingestion-model")
    monkeypatch.setenv("MATH_INGESTION_VLM_API_KEY", "math-ingestion-key")
    monkeypatch.setenv("MATH_INGESTION_VLM_TIMEOUT_SECONDS", "22")
    monkeypatch.setenv("ENGLISH_INGESTION_VLM_ENDPOINT", "https://english-ingestion.example/api")
    monkeypatch.setenv("ENGLISH_INGESTION_VLM_MODEL", "english-ingestion-model")
    monkeypatch.setenv("ENGLISH_INGESTION_VLM_API_KEY", "english-ingestion-key")
    monkeypatch.setenv("ENGLISH_INGESTION_VLM_TIMEOUT_SECONDS", "32")
    monkeypatch.setenv("GRADING_VLM_ENDPOINT", "https://grading.example/api")
    monkeypatch.setenv("GRADING_VLM_MODEL", "grading-model")
    monkeypatch.setenv("GRADING_VLM_API_KEY", "grading-key")
    monkeypatch.setenv("GRADING_VLM_TIMEOUT_SECONDS", "34")
    monkeypatch.setenv("PREVIEW_EXTRACTING_WINDOW_SECONDS", "18")
    monkeypatch.setenv("MATH_SOLUTION_VLM_ENDPOINT", "https://math-solution.example/api")
    monkeypatch.setenv("MATH_SOLUTION_VLM_MODEL", "math-solution-model")
    monkeypatch.setenv("MATH_SOLUTION_VLM_API_KEY", "math-solution-key")
    monkeypatch.setenv("MATH_SOLUTION_VLM_TIMEOUT_SECONDS", "56")
    monkeypatch.setenv("ENGLISH_SOLUTION_VLM_ENDPOINT", "https://english-solution.example/api")
    monkeypatch.setenv("ENGLISH_SOLUTION_VLM_MODEL", "english-solution-model")
    monkeypatch.setenv("ENGLISH_SOLUTION_VLM_API_KEY", "english-solution-key")
    monkeypatch.setenv("ENGLISH_SOLUTION_VLM_TIMEOUT_SECONDS", "57")
    monkeypatch.setenv("MATH_COACHING_VLM_ENDPOINT", "https://math-coaching.example/api")
    monkeypatch.setenv("MATH_COACHING_VLM_MODEL", "math-coaching-model")
    monkeypatch.setenv("MATH_COACHING_VLM_API_KEY", "math-coaching-key")
    monkeypatch.setenv("MATH_COACHING_VLM_TIMEOUT_SECONDS", "78")
    monkeypatch.setenv("ENGLISH_COACHING_VLM_ENDPOINT", "https://english-coaching.example/api")
    monkeypatch.setenv("ENGLISH_COACHING_VLM_MODEL", "english-coaching-model")
    monkeypatch.setenv("ENGLISH_COACHING_VLM_API_KEY", "english-coaching-key")
    monkeypatch.setenv("ENGLISH_COACHING_VLM_TIMEOUT_SECONDS", "79")
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
    assert settings.helper_vlm_endpoint == "https://helper.example/api"
    assert settings.helper_vlm_model == "helper-model"
    assert settings.helper_vlm_api_key == "helper-key"
    assert settings.helper_vlm_timeout_seconds == 12
    assert settings.math_ingestion_vlm_endpoint == "https://math-ingestion.example/api"
    assert settings.math_ingestion_vlm_model == "math-ingestion-model"
    assert settings.math_ingestion_vlm_api_key == "math-ingestion-key"
    assert settings.math_ingestion_vlm_timeout_seconds == 22
    assert settings.english_ingestion_vlm_endpoint == "https://english-ingestion.example/api"
    assert settings.english_ingestion_vlm_model == "english-ingestion-model"
    assert settings.english_ingestion_vlm_api_key == "english-ingestion-key"
    assert settings.english_ingestion_vlm_timeout_seconds == 32
    assert settings.grading_vlm_endpoint == "https://grading.example/api"
    assert settings.grading_vlm_model == "grading-model"
    assert settings.grading_vlm_api_key == "grading-key"
    assert settings.grading_vlm_timeout_seconds == 34
    assert settings.preview_extracting_window_seconds == 18
    assert settings.math_solution_vlm_endpoint == "https://math-solution.example/api"
    assert settings.math_solution_vlm_model == "math-solution-model"
    assert settings.math_solution_vlm_api_key == "math-solution-key"
    assert settings.math_solution_vlm_timeout_seconds == 56
    assert settings.english_solution_vlm_endpoint == "https://english-solution.example/api"
    assert settings.english_solution_vlm_model == "english-solution-model"
    assert settings.english_solution_vlm_api_key == "english-solution-key"
    assert settings.english_solution_vlm_timeout_seconds == 57
    assert settings.math_coaching_vlm_endpoint == "https://math-coaching.example/api"
    assert settings.math_coaching_vlm_model == "math-coaching-model"
    assert settings.math_coaching_vlm_api_key == "math-coaching-key"
    assert settings.math_coaching_vlm_timeout_seconds == 78
    assert settings.english_coaching_vlm_endpoint == "https://english-coaching.example/api"
    assert settings.english_coaching_vlm_model == "english-coaching-model"
    assert settings.english_coaching_vlm_api_key == "english-coaching-key"
    assert settings.english_coaching_vlm_timeout_seconds == 79
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
        "HELPER_VLM_ENDPOINT",
        "HELPER_VLM_MODEL",
        "HELPER_VLM_API_KEY",
        "HELPER_VLM_TIMEOUT_SECONDS",
        "MATH_INGESTION_VLM_ENDPOINT",
        "MATH_INGESTION_VLM_MODEL",
        "MATH_INGESTION_VLM_API_KEY",
        "MATH_INGESTION_VLM_TIMEOUT_SECONDS",
        "ENGLISH_INGESTION_VLM_ENDPOINT",
        "ENGLISH_INGESTION_VLM_MODEL",
        "ENGLISH_INGESTION_VLM_API_KEY",
        "ENGLISH_INGESTION_VLM_TIMEOUT_SECONDS",
        "GRADING_VLM_ENDPOINT",
        "GRADING_VLM_MODEL",
        "GRADING_VLM_API_KEY",
        "GRADING_VLM_TIMEOUT_SECONDS",
        "PREVIEW_EXTRACTING_WINDOW_SECONDS",
        "MATH_SOLUTION_VLM_ENDPOINT",
        "MATH_SOLUTION_VLM_MODEL",
        "MATH_SOLUTION_VLM_API_KEY",
        "MATH_SOLUTION_VLM_TIMEOUT_SECONDS",
        "ENGLISH_SOLUTION_VLM_ENDPOINT",
        "ENGLISH_SOLUTION_VLM_MODEL",
        "ENGLISH_SOLUTION_VLM_API_KEY",
        "ENGLISH_SOLUTION_VLM_TIMEOUT_SECONDS",
        "MATH_COACHING_VLM_ENDPOINT",
        "MATH_COACHING_VLM_MODEL",
        "MATH_COACHING_VLM_API_KEY",
        "MATH_COACHING_VLM_TIMEOUT_SECONDS",
        "ENGLISH_COACHING_VLM_ENDPOINT",
        "ENGLISH_COACHING_VLM_MODEL",
        "ENGLISH_COACHING_VLM_API_KEY",
        "ENGLISH_COACHING_VLM_TIMEOUT_SECONDS",
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
    assert settings.helper_vlm_endpoint == "https://example-helper-vlm-provider.invalid/api"
    assert settings.helper_vlm_model == "replace-me"
    assert settings.helper_vlm_api_key == "replace-me"
    assert settings.helper_vlm_timeout_seconds == 60
    assert settings.math_ingestion_vlm_endpoint == "https://example-math-ingestion-vlm-provider.invalid/api"
    assert settings.math_ingestion_vlm_model == "replace-me"
    assert settings.math_ingestion_vlm_api_key == "replace-me"
    assert settings.math_ingestion_vlm_timeout_seconds == 120
    assert settings.english_ingestion_vlm_endpoint == "https://example-english-ingestion-vlm-provider.invalid/api"
    assert settings.english_ingestion_vlm_model == "replace-me"
    assert settings.english_ingestion_vlm_api_key == "replace-me"
    assert settings.english_ingestion_vlm_timeout_seconds == 120
    assert settings.grading_vlm_endpoint == "https://example-grading-vlm-provider.invalid/api"
    assert settings.grading_vlm_model == "replace-me"
    assert settings.grading_vlm_api_key == "replace-me"
    assert settings.grading_vlm_timeout_seconds == 60
    assert settings.math_solution_vlm_endpoint == "https://example-math-solution-provider.invalid/api"
    assert settings.math_solution_vlm_model == "replace-me"
    assert settings.math_solution_vlm_api_key == "replace-me"
    assert settings.math_solution_vlm_timeout_seconds == 120
    assert settings.english_solution_vlm_endpoint == "https://example-english-solution-provider.invalid/api"
    assert settings.english_solution_vlm_model == "replace-me"
    assert settings.english_solution_vlm_api_key == "replace-me"
    assert settings.english_solution_vlm_timeout_seconds == 120
    assert settings.math_coaching_vlm_endpoint == "https://example-math-coaching-provider.invalid/api"
    assert settings.math_coaching_vlm_model == "replace-me"
    assert settings.math_coaching_vlm_api_key == "replace-me"
    assert settings.math_coaching_vlm_timeout_seconds == 60
    assert settings.english_coaching_vlm_endpoint == "https://example-english-coaching-provider.invalid/api"
    assert settings.english_coaching_vlm_model == "replace-me"
    assert settings.english_coaching_vlm_api_key == "replace-me"
    assert settings.english_coaching_vlm_timeout_seconds == 60
    assert settings.solution_worker_poll_interval_seconds == 5
    assert settings.solution_task_timeout_minutes == 10
    assert settings.solution_max_retries == 3
    assert settings.session_samesite == "lax"
