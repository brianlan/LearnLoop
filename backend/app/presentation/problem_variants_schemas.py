"""Response models for problem-variant sessions (issue #685).

Mirrors the batch ``BatchItemPayload.variation`` contract: the session's
``variation`` subtree is presented via ``serialize_variation_for_response``
(status/progress/evidence only — claim/lease fencing state never exists on
sessions) as a raw dict, with the session's own bookkeeping fields alongside.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.presentation.schemas import UTCDatetime
from app.problem_variation import serialize_variation_for_response


class ProblemVariantSessionView(BaseModel):
    sessionId: str
    problemId: str
    mode: str
    contentRevision: int
    tags: list[str]
    variation: dict[str, Any] | None = None
    submit: dict[str, Any] | None = None
    discardedAt: UTCDatetime | None = None
    createdAt: UTCDatetime
    updatedAt: UTCDatetime


class ProblemVariantSessionResponse(BaseModel):
    session: ProblemVariantSessionView | None


class ProblemVariantSubmitResponse(BaseModel):
    problemId: str
    alreadySubmitted: bool


def serialize_problem_variant_session(session: dict[str, Any]) -> ProblemVariantSessionView:
    return ProblemVariantSessionView(
        sessionId=str(session["_id"]),
        problemId=str(session.get("problemId", "")),
        mode=str(session.get("mode", "")),
        contentRevision=int(session.get("contentRevision", 0)),
        tags=[str(tag) for tag in session.get("tags") or []],
        variation=serialize_variation_for_response(session.get("variation")),
        submit=session.get("submit"),
        discardedAt=session.get("discardedAt"),
        createdAt=session["createdAt"],
        updatedAt=session["updatedAt"],
    )
