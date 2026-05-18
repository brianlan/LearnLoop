from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.presentation.schemas import CorrectAnswerPayload, SourceImagePayload


class PreviewDraftPayload(BaseModel):
    text: str | None = None
    problemType: str | None = None
    graphDsl: str | None = None
    correctAnswer: str | None = None
    tags: list[str] = Field(default_factory=list)


class PreviewExtractionPayload(BaseModel):
    requestModel: str | None = None
    requestStartedAt: datetime | None = None
    requestFinishedAt: datetime | None = None
    success: bool | None = None
    rawText: str | None = None
    rawProblemType: str | None = None
    rawGraphDsl: str | None = None
    rawProviderResponse: dict[str, Any] | None = None
    failureCode: str | None = None
    failureMessage: str | None = None


class PreviewPayload(BaseModel):
    id: str
    status: str
    sourceImage: SourceImagePayload
    draft: PreviewDraftPayload
    extraction: PreviewExtractionPayload
    createdAt: datetime
    updatedAt: datetime
    expiresAt: datetime


class PreviewResponse(BaseModel):
    preview: PreviewPayload


class ProblemPayload(BaseModel):
    id: str
    text: str
    problemType: str
    graphDsl: str | None = None
    correctAnswer: CorrectAnswerPayload
    tags: list[str] = Field(default_factory=list)
    sourceImage: SourceImagePayload | None = None
    createdAt: datetime
    updatedAt: datetime


class ProblemResponse(BaseModel):
    problem: ProblemPayload


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def serialize_preview(preview: Mapping[str, Any]) -> PreviewPayload:
    extraction = dict(preview.get("extraction", {}))
    draft = dict(preview.get("editableDraft", {}))
    source_image = dict(preview.get("sourceImage", {}))
    return PreviewPayload.model_validate(
        {
            "id": str(preview["_id"]),
            "status": str(preview["status"]),
            "sourceImage": source_image,
            "draft": {
                "text": draft.get("text"),
                "problemType": _enum_value(draft.get("problemType")),
                "graphDsl": draft.get("graphDsl"),
                "correctAnswer": draft.get("correctAnswer"),
                "tags": list(draft.get("tags", [])),
            },
            "extraction": {
                "requestModel": extraction.get("requestModel"),
                "requestStartedAt": extraction.get("requestStartedAt"),
                "requestFinishedAt": extraction.get("requestFinishedAt"),
                "success": extraction.get("success"),
                "rawText": extraction.get("rawText"),
                "rawProblemType": _enum_value(extraction.get("rawProblemType")),
                "rawGraphDsl": extraction.get("rawGraphDsl"),
                "rawProviderResponse": extraction.get("rawProviderResponse"),
                "failureCode": extraction.get("failureCode"),
                "failureMessage": extraction.get("failureMessage"),
            },
            "createdAt": preview["createdAt"],
            "updatedAt": preview["updatedAt"],
            "expiresAt": preview["expiresAt"],
        }
    )


def serialize_problem(problem: Mapping[str, Any]) -> ProblemPayload:
    return ProblemPayload.model_validate(
        {
            "id": str(problem["_id"]),
            "text": problem["text"],
            "problemType": _enum_value(problem["problemType"]),
            "graphDsl": problem.get("graphDsl"),
            "correctAnswer": problem["correctAnswer"],
            "tags": list(problem.get("tags", [])),
            "sourceImage": problem.get("sourceImage"),
            "createdAt": problem["createdAt"],
            "updatedAt": problem["updatedAt"],
        }
    )
