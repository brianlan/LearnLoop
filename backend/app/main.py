from typing import cast

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.types import ExceptionHandler

from app.infrastructure.config.settings import get_settings
from app.observability import configure_logging
from app.presentation.auth import router as auth_router
from app.presentation.exams import router as exams_router
from app.presentation.errors import ApiError, api_error_handler, validation_error_handler
from app.presentation.ingestion import router as ingestion_router
from app.presentation.media import router as media_router
from app.presentation.problems import router as problems_router
from app.presentation.tags import router as tags_router


def create_app() -> FastAPI:
    configure_logging(get_settings())

    application = FastAPI(title="LearnLoop API")
    application.add_exception_handler(
        ApiError, cast(ExceptionHandler, api_error_handler)
    )
    application.add_exception_handler(
        RequestValidationError, cast(ExceptionHandler, validation_error_handler)
    )

    @application.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    api_v1_router = APIRouter(prefix="/api/v1")
    api_v1_router.include_router(auth_router)
    api_v1_router.include_router(ingestion_router)
    api_v1_router.include_router(problems_router)
    api_v1_router.include_router(exams_router)
    api_v1_router.include_router(media_router)
    api_v1_router.include_router(tags_router)
    application.include_router(api_v1_router)

    return application


app = create_app()
