"""Pre-save graphDsl validation route (issue #695)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.domain.whiteboard import MAX_DSL_LENGTH, explain_graph_dsl
from app.presentation.deps import CurrentUserDependency

router = APIRouter(prefix="/graph-dsl", tags=["graph-dsl"])


class GraphDslValidationRequest(BaseModel):
    graphDsl: str


class GraphDslValidationResponse(BaseModel):
    valid: bool
    issues: list[str]


@router.post("/validate", response_model=GraphDslValidationResponse)
async def validate_graph_dsl(
    payload: GraphDslValidationRequest,
    _: CurrentUserDependency,
) -> GraphDslValidationResponse:
    """Return the sanitizer's reasons for the submitted DSL.

    Empty DSL is valid because a graph is optional. Oversized input is capped
    before explanation, mirroring the domain's ``MAX_DSL_LENGTH`` contract.
    """
    if len(payload.graphDsl) > MAX_DSL_LENGTH:
        issues = [f"exceeds the maximum DSL length of {MAX_DSL_LENGTH} characters"]
    elif not payload.graphDsl.strip():
        issues = []
    else:
        issues = explain_graph_dsl(payload.graphDsl)
    return GraphDslValidationResponse(valid=not issues, issues=issues)
