from fastapi import APIRouter

from app.infrastructure.config.settings import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings_info() -> dict:
    settings = get_settings()
    return {
        "app": {
            "env": settings.app_env,
            "host": settings.app_host,
            "port": settings.app_port,
            "log_level": settings.app_log_level,
        },
        "database": {
            "name": settings.mongodb_database,
        },
        "storage": {
            "endpoint": settings.s3_endpoint,
            "bucket": settings.s3_bucket,
            "region": settings.s3_region,
            "force_path_style": settings.s3_force_path_style,
        },
        "vlm": {
            "endpoint": settings.vlm_endpoint,
            "model": settings.vlm_model,
            "timeout_seconds": settings.vlm_timeout_seconds,
            "preview_extracting_window_seconds": settings.preview_extracting_window_seconds,
        },
        "session": {
            "cookie_name": settings.session_cookie_name,
            "secure": settings.session_secure,
            "samesite": settings.session_samesite,
        },
        "practice": {
            "cooldown_days": settings.practice_cooldown_days,
            "last_wrong_weight": settings.practice_last_wrong_weight,
            "failure_rate_weight": settings.practice_failure_rate_weight,
            "recency_weight": settings.practice_recency_weight,
        },
    }