from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.infrastructure.config.profile_status import (
    VLM_PROFILE_PREFIXES,
    profile_status,
)
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.vlm import health as vlm_health

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
    payload = {
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
        "preview_extracting_window_seconds": settings.preview_extracting_window_seconds,
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
    # VLM profiles come from the shared prefix tuple so a 12th profile cannot
    # drift between the payload and the health probe (#654).
    for prefix in VLM_PROFILE_PREFIXES:
        payload[prefix] = _vlm_profile(settings, prefix)
    return payload


@router.get("/vlm-health")
async def get_vlm_health() -> dict:
    """Current in-memory VLM availability snapshot (#654)."""
    return vlm_health.snapshot()


@router.post("/vlm-health/run")
async def run_vlm_health() -> JSONResponse:
    """Spawn one health run; 409 while another run is still in flight."""
    if not vlm_health.begin_run():
        raise HTTPException(
            status_code=409, detail="VLM health run already in progress"
        )
    vlm_health.spawn(get_settings())
    return JSONResponse(status_code=202, content=vlm_health.snapshot())
