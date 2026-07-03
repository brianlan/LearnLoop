from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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
    uploadedAt: datetime | None = None
