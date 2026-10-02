import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.types import ExceptionHandler

from app.infrastructure.config.settings import get_settings
from app.infrastructure.storage.mongo import ensure_database_setup, get_database, get_mongo_adapter
from app.infrastructure.storage.s3 import S3StorageAdapter
from app.observability import configure_logging
from app.infrastructure.vlm.client import (
    VLMClient,
    build_english_ingestion_vlm_client,
    build_math_ingestion_vlm_client,
)
from app.infrastructure.worker.extraction_worker import run_extraction_worker
from app.infrastructure.worker.solution_worker import run_solution_worker
from app.infrastructure.worker.exam_grading_worker import run_exam_grading_worker
from app.infrastructure.worker.variation_worker import run_variation_worker
from app.infrastructure.vlm import health as vlm_health
from app.infrastructure.vlm.variant_client import (
    VariantVLMError,
    build_variant_generator_vlm_client,
    build_variant_helper_vlm_client,
    build_variant_validator_vlm_client,
)
from app.solution_generation import backfill_solution_generation_tasks
from app.problem_variation import backfill_variation_failure_kinds
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
        math_client = build_math_ingestion_vlm_client(settings)
        english_client = build_english_ingestion_vlm_client(settings)
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


async def _run_exam_grading_worker_with_logging(database, storage, settings, stop_event, adapter):
    try:
        await run_exam_grading_worker(database, storage, settings, stop_event, adapter=adapter)
    except Exception:
        logger.exception("Exam grading worker crashed")
        raise


async def _run_variation_worker_with_logging(database, settings, stop_event):
    generator = None
    validator = None
    validator2 = None
    helper = None
    try:
        generator = build_variant_generator_vlm_client(settings)
        validator = build_variant_validator_vlm_client(settings)
        # Optional profile: fully unconfigured validator2 runs single-validator.
        validator2 = build_variant_validator_vlm_client(settings, second=True)
        helper = build_variant_helper_vlm_client(settings)
        validators = [v for v in (validator, validator2) if v is not None]
        if len(validators) == 1:
            identity = validators[0].identity
            logger.info(
                "Variation worker running with one validator: %s/%s "
                "(variant_validator2_vlm_* unconfigured)",
                identity["provider"],
                identity["model"],
            )
        await run_variation_worker(
            database,
            settings,
            generator,
            validators,
            helper,
            stop_event,
        )
    except VariantVLMError as exc:
        # Unconfigured model profiles: do not run the worker and do not crash
        # the app; queueing endpoints reject with the same explicit error.
        logger.warning("Variation worker disabled: %s", exc)
    except Exception:
        logger.exception("Variation worker crashed")
        raise
    finally:
        for client in (generator, validator, validator2, helper):
            if client is not None:
                await client.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = get_database()
    await ensure_database_setup(database)
    await backfill_solution_generation_tasks(database)
    # One-time legacy failure-kind retag so pre-#658 FAIL items can attest.
    await backfill_variation_failure_kinds(database)
    
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
    if settings.exam_grading_worker_enabled:
        worker_tasks.append(
            asyncio.create_task(_run_exam_grading_worker_with_logging(database, storage, settings, stop_event, get_mongo_adapter()))
        )
    if settings.variation_worker_enabled:
        worker_tasks.append(
            asyncio.create_task(
                _run_variation_worker_with_logging(database, settings, stop_event)
            )
        )

    # Post-launch VLM availability probe (#654): non-blocking by design; it
    # joins the same tracked task list so shutdown cancels it like the
    # workers (no "Task was destroyed but it is pending").
    if vlm_health.begin_run():
        worker_tasks.append(
            asyncio.create_task(vlm_health.run_stored_probe(settings))
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
