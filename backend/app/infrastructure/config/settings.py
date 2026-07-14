from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)
    app_log_level: str = Field(default="INFO")

    mongodb_uri: str = Field(
        default="mongodb://localhost:27017/learnloop?replicaSet=rs0&directConnection=true"
    )
    mongodb_database: str = Field(default="learnloop")

    s3_endpoint: str = Field(default="http://localhost:9000")
    s3_access_key: str = Field(default="replace-me")
    s3_secret_key: str = Field(default="replace-me")
    s3_bucket: str = Field(default="learnloop-media")
    s3_region: str = Field(default="us-east-1")
    s3_force_path_style: bool = Field(default=True)

    helper_vlm_endpoint: str = Field(default="https://example-helper-vlm-provider.invalid/api")
    helper_vlm_model: str = Field(default="replace-me")
    helper_vlm_api_key: str = Field(default="replace-me")
    helper_vlm_timeout_seconds: float = Field(default=60.0, gt=0)
    helper_vlm_provider: str = Field(default="openai")
    helper_vlm_api_mode: Literal["chat", "responses"] = Field(default="chat")

    math_ingestion_vlm_endpoint: str = Field(default="https://example-math-ingestion-vlm-provider.invalid/api")
    math_ingestion_vlm_model: str = Field(default="replace-me")
    math_ingestion_vlm_api_key: str = Field(default="replace-me")
    math_ingestion_vlm_timeout_seconds: float = Field(default=120.0, gt=0)
    math_ingestion_vlm_provider: str = Field(default="openai")
    math_ingestion_vlm_api_mode: Literal["chat", "responses"] = Field(default="chat")

    english_ingestion_vlm_endpoint: str = Field(default="https://example-english-ingestion-vlm-provider.invalid/api")
    english_ingestion_vlm_model: str = Field(default="replace-me")
    english_ingestion_vlm_api_key: str = Field(default="replace-me")
    english_ingestion_vlm_timeout_seconds: float = Field(default=120.0, gt=0)
    english_ingestion_vlm_provider: str = Field(default="openai")
    english_ingestion_vlm_api_mode: Literal["chat", "responses"] = Field(default="chat")

    grading_vlm_endpoint: str = Field(default="https://example-grading-vlm-provider.invalid/api")
    grading_vlm_model: str = Field(default="replace-me")
    grading_vlm_api_key: str = Field(default="replace-me")
    grading_vlm_timeout_seconds: float = Field(default=60.0, gt=0)
    grading_vlm_provider: str = Field(default="openai")
    grading_vlm_api_mode: Literal["chat", "responses"] = Field(default="chat")
    preview_extracting_window_seconds: float = Field(default=150.0, gt=0)

    math_solution_vlm_endpoint: str = Field(default="https://example-math-solution-provider.invalid/api")
    math_solution_vlm_model: str = Field(default="replace-me")
    math_solution_vlm_api_key: str = Field(default="replace-me")
    math_solution_vlm_timeout_seconds: float = Field(default=120.0, gt=0)
    math_solution_vlm_provider: str = Field(default="openai")
    math_solution_vlm_api_mode: Literal["chat", "responses"] = Field(default="chat")

    english_solution_vlm_endpoint: str = Field(default="https://example-english-solution-provider.invalid/api")
    english_solution_vlm_model: str = Field(default="replace-me")
    english_solution_vlm_api_key: str = Field(default="replace-me")
    english_solution_vlm_timeout_seconds: float = Field(default=120.0, gt=0)
    english_solution_vlm_provider: str = Field(default="openai")
    english_solution_vlm_api_mode: Literal["chat", "responses"] = Field(default="chat")

    math_coaching_vlm_endpoint: str = Field(default="https://example-math-coaching-provider.invalid/api")
    math_coaching_vlm_model: str = Field(default="replace-me")
    math_coaching_vlm_api_key: str = Field(default="replace-me")
    math_coaching_vlm_timeout_seconds: float = Field(default=60.0, gt=0)
    math_coaching_vlm_provider: str = Field(default="openai")
    math_coaching_vlm_api_mode: Literal["chat", "responses"] = Field(default="chat")

    english_coaching_vlm_endpoint: str = Field(default="https://example-english-coaching-provider.invalid/api")
    english_coaching_vlm_model: str = Field(default="replace-me")
    english_coaching_vlm_api_key: str = Field(default="replace-me")
    english_coaching_vlm_timeout_seconds: float = Field(default=60.0, gt=0)
    english_coaching_vlm_provider: str = Field(default="openai")
    english_coaching_vlm_api_mode: Literal["chat", "responses"] = Field(default="chat")

    solution_worker_poll_interval_seconds: int = Field(default=5, gt=0)
    solution_task_timeout_minutes: int = Field(default=10, gt=0)
    solution_max_retries: int = Field(default=3, ge=0)

    # Exam grading worker configuration
    exam_grading_worker_enabled: bool = Field(default=True)
    exam_grading_worker_poll_interval_seconds: float = Field(default=2.0, gt=0)
    exam_grading_lease_seconds: float = Field(default=60.0, gt=0)
    exam_grading_lease_refresh_seconds: float = Field(default=20.0, gt=0)

    session_cookie_name: str = Field(default="ll_session")
    session_secure: bool = Field(default=False)
    session_samesite: Literal["lax", "strict", "none"] = Field(default="lax")

    # Problem selection configuration (shared by practice and exam)
    problem_selection_cooldown_days: int = Field(default=7, ge=0)
    problem_selection_last_wrong_weight: float = Field(default=1.0, ge=0)
    problem_selection_failure_rate_weight: float = Field(default=1.0, ge=0)
    problem_selection_recency_weight: float = Field(default=1.0, ge=0)
    problem_selection_min_age_days: int = Field(default=3, ge=0)

    # Bulk ingestion configuration
    bulk_ingestion_max_images: int = Field(default=50, ge=1)
    bulk_ingestion_max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    bulk_ingestion_max_items: int = Field(default=200, ge=1)
    bulk_ingestion_batch_ttl_seconds: int = Field(default=86400, ge=1)
    bulk_ingestion_extraction_concurrency: int = Field(default=3, ge=1)
    bulk_ingestion_item_lease_timeout_seconds: int = Field(default=120, ge=1)
    bulk_ingestion_extraction_poll_interval_seconds: float = Field(default=5.0, gt=0)
    bulk_ingestion_extraction_worker_enabled: bool = Field(default=True)

    # Teacher password configuration
    teacher_password_default: str = Field(default="default-teacher-password")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
