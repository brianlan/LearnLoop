"""Typed admission provenance for variant problems (issue #614).

Leaf module so that ``app.domain.models`` can type ``Problem.variation``
without importing the ingestion package (which transitively imports
``app.domain.models`` via ``app.domain.state``).

These models type the value stored on Problem.variation after a PASS
candidate is admitted. They intentionally exclude all worker fencing state
(status/claimToken/leaseUntil/contentRevision/validatedRevision) — that
state lives on the ingestion item only. Frozen content snapshots carry the
existing CorrectAnswer storage shape (display/normalizedText/normalizedSet/
format), and equivalence at admission remains helper-based, never a
normalized-string comparison.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ``transfer-variant`` is the canonical mode for new admissions (#656).
# ``data-and-wording`` is a legacy read-only value kept so historical
# provenance stays valid; new writes never emit it.
VariantMode = Literal["data-only", "transfer-variant", "data-and-wording"]


class ModelIdentity(BaseModel):
    provider: str
    model: str


class FrozenContentSnapshot(BaseModel):
    """Immutable content snapshot in the Problem's own storage shape."""

    text: str
    problemType: str
    subject: str
    graphDsl: str | None = None
    correctAnswer: dict[str, Any]


class OriginalProvenance(FrozenContentSnapshot):
    """The confirmed source content plus its permanent audit image.

    ``auditImage`` uses the existing SourceImage metadata shape; the object
    lives in a permanent audit namespace and is never an active task image.
    """

    auditImage: dict[str, Any]


class ValidationProvenance(BaseModel):
    """Successful validation evidence frozen at admission time.

    ``attestedByUser`` marks an admission admitted via explicit user
    attestation instead of a validator run covering the current revision
    (#648 stale-PASS, #658 check-kind FAIL); a stale validator report is
    never frozen as if validator-covered. ``verdict`` records the REAL
    verdict of the frozen evidence — a fail-attested admission freezes
    ``"fail"`` so the audit trail stays honest.
    """

    verdict: Literal["pass", "fail"]
    helperModel: ModelIdentity | None = None
    reports: list[dict[str, Any]] = Field(default_factory=list)
    attestedByUser: bool = False


class ProblemVariation(BaseModel):
    """Typed Problem.variation value for admitted variant problems.

    Every field is written once at admission and is never updated afterwards;
    top-level Problem edits only ever touch the main fields.
    """

    mode: VariantMode
    original: OriginalProvenance
    acceptedVariant: FrozenContentSnapshot
    generator: ModelIdentity
    generationCount: int
    validation: ValidationProvenance
