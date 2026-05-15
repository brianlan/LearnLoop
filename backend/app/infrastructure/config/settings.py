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

    vlm_endpoint: str = Field(default="https://example-vlm-provider.invalid/api")
    vlm_model: str = Field(default="replace-me")
    vlm_api_key: str = Field(default="replace-me")
    vlm_timeout_seconds: float = Field(default=30.0, gt=0)

    session_cookie_name: str = Field(default="ll_session")
    session_secure: bool = Field(default=False)
    session_samesite: Literal["lax", "strict", "none"] = Field(default="lax")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
