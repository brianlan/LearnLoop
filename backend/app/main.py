import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.types import ExceptionHandler

from app.infrastructure.config.settings import get_settings
from app.infrastructure.storage.mongo import ensure_database_setup, get_database
from app.infrastructure.storage.s3 import S3StorageAdapter
from app.observability import configure_logging
from app.infrastructure.vlm.client import VLMClient
from app.infrastructure.vlm.prompts import ENGLISH_EXTRACTION_SYSTEM_PROMPT
from app.infrastructure.worker.extraction_worker import run_extraction_worker
from app.infrastructure.worker.solution_worker import run_solution_worker
from app.solution_generation import backfill_solution_generation_tasks
from app.presentation.auth import router as auth_router
from app.presentation.exams import router as exams_router
from app.presentation.errors import ApiError, api_error_handler, validation_error_handler
from app.presentation.folders import router as folders_router
from app.presentation.bulk_ingestion import router as bulk_ingestion_router
from app.presentation.ingestion import router as ingestion_router
from app.presentation.media import router as media_router
from app.presentation.problems import router as problems_router
from app.presentation.tags import router as tags_router
from app.presentation.practice import router as practice_router
from app.presentation.settings import router as settings_router
from app.presentation.teacher_password import router as teacher_password_router
from app.presentation.coaching import router as coaching_router
from app.presentation.home import router as home_router

logger = logging.getLogger(__name__)


async def _run_worker_with_logging(database, stop_event):
    try:
        await run_solution_worker(database, stop_event)
    except Exception:
        logger.exception("Solution worker crashed")
        raise


async def _run_extraction_worker_with_logging(database, storage, settings, stop_event):
    math_client: VLMClient | None = None
    english_client: VLMClient | None = None
    try:
        math_client = VLMClient(
            endpoint=settings.math_ingestion_vlm_endpoint,
            model=settings.math_ingestion_vlm_model,
            api_key=settings.math_ingestion_vlm_api_key,
            timeout_seconds=settings.math_ingestion_vlm_timeout_seconds,
            provider=settings.math_ingestion_vlm_provider,
            api_mode=settings.math_ingestion_vlm_api_mode,
        )
        english_client = VLMClient(
            endpoint=settings.english_ingestion_vlm_endpoint,
            model=settings.english_ingestion_vlm_model,
            api_key=settings.english_ingestion_vlm_api_key,
            timeout_seconds=settings.english_ingestion_vlm_timeout_seconds,
            provider=settings.english_ingestion_vlm_provider,
            api_mode=settings.english_ingestion_vlm_api_mode,
            extraction_system_prompt=ENGLISH_EXTRACTION_SYSTEM_PROMPT,
            request_correct_answer=True,
        )
        await run_extraction_worker(
            database,
            storage,
            settings,
            math_client,
            english_client,
            stop_event,
        )
    except Exception:
        logger.exception("Extraction worker crashed")
        raise
    finally:
        if math_client is not None:
            await math_client.aclose()
        if english_client is not None:
            await english_client.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = get_database()
    await ensure_database_setup(database)
    await backfill_solution_generation_tasks(database)
    
    settings = get_settings()
    storage = S3StorageAdapter(settings=settings)

    # Start workers
    stop_event = asyncio.Event()
    worker_tasks: list[asyncio.Task[Any]] = [
        asyncio.create_task(_run_worker_with_logging(database, stop_event)),
    ]
    if settings.bulk_ingestion_extraction_worker_enabled:
        worker_tasks.append(
            asyncio.create_task(
                _run_extraction_worker_with_logging(database, storage, settings, stop_event)
            )
        )
    
    yield
    
    # Stop workers
    stop_event.set()
    for task in worker_tasks:
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


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
    api_v1_router.include_router(bulk_ingestion_router)
    api_v1_router.include_router(problems_router)
    api_v1_router.include_router(exams_router)
    api_v1_router.include_router(media_router)
    api_v1_router.include_router(tags_router)
    api_v1_router.include_router(folders_router)
    api_v1_router.include_router(practice_router)
    api_v1_router.include_router(settings_router)
    api_v1_router.include_router(teacher_password_router)
    api_v1_router.include_router(coaching_router)
    api_v1_router.include_router(home_router)
    application.include_router(api_v1_router)

    return application


app = create_app()
