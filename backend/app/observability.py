import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.infrastructure.config.settings import Settings, get_settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        auth_event = getattr(record, "auth_event", None)
        if auth_event is not None:
            payload["auth_event"] = auth_event

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in {
                "args",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "message",
                "module",
                "msecs",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            }:
                continue
            payload[key] = value

        return json.dumps(payload, default=str)


def configure_logging(settings: Settings | None = None) -> None:
    resolved_settings = settings or get_settings()
    root_logger = logging.getLogger()

    if getattr(root_logger, "_learnloop_configured", False):
        root_logger.setLevel(resolved_settings.app_log_level.upper())
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(resolved_settings.app_log_level.upper())
    root_logger._learnloop_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_auth_event(event: str, **fields: Any) -> None:
    get_logger("learnloop.auth").info(
        f"auth:{event}",
        extra={"auth_event": event, **fields},
    )


def log_teacher_password_event(event: str, **fields: Any) -> None:
    get_logger("learnloop.teacher_password").info(
        f"teacher_password:{event}",
        extra={"teacher_password_event": event, **fields},
    )


def log_solution_generation_event(event: str, problem_id: str, **fields: Any) -> None:
    get_logger("learnloop.solution_generation").info(
        f"solution_generation:{event}",
        extra={"solution_generation_event": event, "problem_id": problem_id, **fields},
    )


def log_coaching_event(
    event: str,
    conversation_id: str,
    message_count: int,
    response_time_ms: float,
    **fields: Any,
) -> None:
    get_logger("learnloop.coaching").info(
        f"coaching:{event}",
        extra={
            "coaching_event": event,
            "conversation_id": conversation_id,
            "message_count": message_count,
            "response_time_ms": response_time_ms,
            **fields,
        },
    )


async def get_solution_task_counts(database: Any) -> dict[str, int]:
    from app.infrastructure.storage.mongo import SOLUTION_GENERATION_TASKS_COLLECTION

    collection = database[SOLUTION_GENERATION_TASKS_COLLECTION]
    pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    cursor = collection.aggregate(pipeline)
    counts = {"pending": 0, "generating": 0, "ready": 0, "failed": 0}
    async for doc in cursor:
        status = doc["_id"]
        if status in counts:
            counts[status] = doc["count"]
        elif isinstance(status, str) and status.lower() in counts:
            counts[status.lower()] = doc["count"]
    return counts
