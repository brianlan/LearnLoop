from fastapi import APIRouter

from app.infrastructure.config.profile_status import profile_status
from app.infrastructure.config.settings import Settings, get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


def _vlm_profile(settings: Settings, prefix: str) -> dict:
    """Uniform VLM profile payload: connection fields + config health.

    `status` is computed from endpoint/model/api_key via the shared rule; the
    api_key value itself is never exposed.
    """
    endpoint = getattr(settings, f"{prefix}_endpoint")
    model = getattr(settings, f"{prefix}_model")
    provider = getattr(settings, f"{prefix}_provider")
    api_mode = getattr(settings, f"{prefix}_api_mode")
    timeout_seconds = getattr(settings, f"{prefix}_timeout_seconds")
    api_key = getattr(settings, f"{prefix}_api_key")
    return {
        "endpoint": endpoint,
        "model": model,
        "provider": provider,
        "api_mode": api_mode,
        "timeout_seconds": timeout_seconds,
        "status": profile_status(endpoint=endpoint, model=model, api_key=api_key),
    }


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
        "helper_vlm": _vlm_profile(settings, "helper_vlm"),
        "math_ingestion_vlm": _vlm_profile(settings, "math_ingestion_vlm"),
        "english_ingestion_vlm": _vlm_profile(settings, "english_ingestion_vlm"),
        "preview_extracting_window_seconds": settings.preview_extracting_window_seconds,
        "grading_vlm": _vlm_profile(settings, "grading_vlm"),
        "math_solution_vlm": _vlm_profile(settings, "math_solution_vlm"),
        "english_solution_vlm": _vlm_profile(settings, "english_solution_vlm"),
        "math_coaching_vlm": _vlm_profile(settings, "math_coaching_vlm"),
        "english_coaching_vlm": _vlm_profile(settings, "english_coaching_vlm"),
        "variant_generator_vlm": _vlm_profile(settings, "variant_generator_vlm"),
        "variant_validator_vlm": _vlm_profile(settings, "variant_validator_vlm"),
        "variant_validator2_vlm": _vlm_profile(settings, "variant_validator2_vlm"),
        "session": {
            "cookie_name": settings.session_cookie_name,
            "secure": settings.session_secure,
            "samesite": settings.session_samesite,
        },
        "problem_selection": {
            "cooldown_days": settings.problem_selection_cooldown_days,
            "last_wrong_weight": settings.problem_selection_last_wrong_weight,
            "failure_rate_weight": settings.problem_selection_failure_rate_weight,
            "recency_weight": settings.problem_selection_recency_weight,
            "min_problem_age_days": settings.problem_selection_min_age_days,
        },
    }
