from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def ensure_utc(value: datetime | None) -> datetime | None:
    """Treat naive datetimes (e.g. from Mongo/PyMongo) as UTC for API output.

    Timezone-aware datetimes are converted to UTC; naive datetimes are assumed
    to already be UTC. ``None`` is returned unchanged so optional fields are
    preserved.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# Pydantic datetime field that always serializes with UTC timezone information.
UTCDatetime = Annotated[datetime, AfterValidator(ensure_utc)]


class CorrectAnswerPayload(BaseModel):
    display: str
    normalizedText: str
    normalizedSet: list[str] = Field(default_factory=list)
    format: str


class SourceImagePayload(BaseModel):
    bucket: str
    objectKey: str
    contentType: str | None = None
    sizeBytes: int | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    uploadedAt: UTCDatetime | None = None
    mediaUrl: str | None = None
