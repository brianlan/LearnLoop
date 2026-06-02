import asyncio
import logging
from contextlib import asynccontextmanager
from typing import cast

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.types import ExceptionHandler

from app.infrastructure.config.settings import get_settings
from app.infrastructure.storage.mongo import ensure_database_setup, get_database
from app.observability import configure_logging
from app.infrastructure.worker.solution_worker import run_solution_worker
from app.presentation.solution_generation import backfill_solution_generation_tasks
from app.presentation.auth import router as auth_router
from app.presentation.exams import router as exams_router
from app.presentation.errors import ApiError, api_error_handler, validation_error_handler
from app.presentation.folders import router as folders_router
from app.presentation.ingestion import router as ingestion_router
from app.presentation.media import router as media_router
from app.presentation.problems import router as problems_router
from app.presentation.tags import router as tags_router
from app.presentation.practice import router as practice_router
from app.presentation.settings import router as settings_router
from app.presentation.teacher_password import router as teacher_password_router
from app.presentation.coaching import router as coaching_router

logger = logging.getLogger(__name__)


async def _run_worker_with_logging(database, stop_event):
    try:
        await run_solution_worker(database, stop_event)
    except Exception:
        logger.exception("Solution worker crashed")
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = get_database()
    await ensure_database_setup(database)
    await backfill_solution_generation_tasks(database)
    
    # Start worker
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(_run_worker_with_logging(database, stop_event))
    
    yield
    
    # Stop worker
    stop_event.set()
    try:
        # Give it a short time to shut down
        await asyncio.wait_for(worker_task, timeout=5.0)
    except asyncio.TimeoutError:
        worker_task.cancel()


def create_app() -> FastAPI:
    configure_logging(get_settings())

    application = FastAPI(title="LearnLoop API", lifespan=lifespan)
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
    api_v1_router.include_router(folders_router)
    api_v1_router.include_router(practice_router)
    api_v1_router.include_router(settings_router)
    api_v1_router.include_router(teacher_password_router)
    api_v1_router.include_router(coaching_router)
    application.include_router(api_v1_router)

    return application


app = create_app()
