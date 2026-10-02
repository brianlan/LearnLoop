"""Variant ingestion workflow rules shared by API handlers and the worker.

The variation lifecycle lives inside each item document (``item.variation``)
with one per-item ``contentRevision`` covering semantic source/candidate
changes and new generation. Tags and worker progress never change that
revision. This module owns the mode enum, the lifecycle states and their
legal transitions, the semantic snapshot/comparison rules used for edit
invalidation, and the serialization of validation evidence.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from app.domain.state import InvalidStateTransitionError
from app.domain.ingestion.variation import (
    ProblemContent,
    ValidatorReport,
    VariantCandidate,
    VariantGenerationResult,
    assess_variant,
)


class IngestionMode(str, Enum):
    """Batch-level ingestion mode, immutable after creation.

    ``DATA_AND_WORDING`` is a legacy persisted value (#656): historical
    batches keep it and remain readable/continuable, but new batch creation
    must use ``TRANSFER_VARIANT`` and never emits the legacy name.
    """

    ORIGINAL = "original"
    DATA_ONLY = "data-only"
    TRANSFER_VARIANT = "transfer-variant"
    DATA_AND_WORDING = "data-and-wording"


# Modes a new batch may be created with.
CREATABLE_INGESTION_MODES = (
    IngestionMode.ORIGINAL,
    IngestionMode.DATA_ONLY,
    IngestionMode.TRANSFER_VARIANT,
)

# All variant modes including the legacy value, for reading/continuation.
VARIANT_INGESTION_MODES = (
    IngestionMode.DATA_ONLY,
    IngestionMode.TRANSFER_VARIANT,
    IngestionMode.DATA_AND_WORDING,
)

# Legacy batches continue generation under the canonical transfer-variant
# contract; normalization happens once at the generation/admission boundary.
LEGACY_VARIANT_MODE_ALIASES = {
    IngestionMode.DATA_AND_WORDING: IngestionMode.TRANSFER_VARIANT,
}


def canonical_variation_mode(mode: str | IngestionMode | None) -> IngestionMode:
    """Map a stored batch mode to its canonical write/generation value."""
    resolved = IngestionMode(mode) if mode is not None else IngestionMode.ORIGINAL
    return LEGACY_VARIANT_MODE_ALIASES.get(resolved, resolved)


def is_variant_mode(mode: str | IngestionMode | None) -> bool:
    if mode is None:
        return False
    return IngestionMode(mode) in VARIANT_INGESTION_MODES


class VariationStatus(str, Enum):
    """Per-item variant lifecycle.

    not-requested → queued → generating → validating → ready | failed;
    ready → needs-validation; needs-validation → queued → validating;
    failed → queued only via manual Generate Again.
    """

    NOT_REQUESTED = "not-requested"
    QUEUED = "queued"
    GENERATING = "generating"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
    NEEDS_VALIDATION = "needs-validation"


VARIATION_TRANSITIONS: dict[VariationStatus, list[VariationStatus]] = {
    VariationStatus.NOT_REQUESTED: [VariationStatus.QUEUED],
    VariationStatus.QUEUED: [
        VariationStatus.GENERATING,
        VariationStatus.VALIDATING,
        # An original semantic edit cancels the queued attempt.
        VariationStatus.NOT_REQUESTED,
    ],
    VariationStatus.GENERATING: [
        VariationStatus.VALIDATING,
        VariationStatus.FAILED,
        VariationStatus.NOT_REQUESTED,
    ],
    VariationStatus.VALIDATING: [
        VariationStatus.READY,
        VariationStatus.FAILED,
        VariationStatus.NOT_REQUESTED,
    ],
    VariationStatus.READY: [
        VariationStatus.NEEDS_VALIDATION,
        # Generate Again discards the current candidate and regenerates.
        VariationStatus.QUEUED,
        # A source semantic edit invalidates the approved candidate.
        VariationStatus.NOT_REQUESTED,
    ],
    VariationStatus.FAILED: [
        VariationStatus.QUEUED,
        # Fail-attest (#658): a check-kind-only FAIL may be attested into
        # READY; the attest fence enforces the eligibility predicate.
        VariationStatus.READY,
        # A source semantic edit discards the failed attempt.
        VariationStatus.NOT_REQUESTED,
    ],
    VariationStatus.NEEDS_VALIDATION: [
        VariationStatus.QUEUED,
        # "Keep validation" attestation is the only direct approval exit.
        VariationStatus.READY,
        VariationStatus.NOT_REQUESTED,
    ],
}

# Statuses where background work exists and must be fenced/cancelled.
VARIATION_IN_FLIGHT = (
    VariationStatus.QUEUED,
    VariationStatus.GENERATING,
    VariationStatus.VALIDATING,
)


def transition_variation_status(
    current: VariationStatus, target: VariationStatus
) -> VariationStatus:
    if target not in VARIATION_TRANSITIONS.get(current, []):
        raise InvalidStateTransitionError(
            f"Invalid variation transition from {current} to {target}"
        )
    return target


class VariationNotFoundError(Exception):
    """The item does not exist in the batch."""


class RevisionMismatchError(Exception):
    """The submitted expectedRevision does not match the item's current value."""


class InvalidVariationStateError(Exception):
    """The variation lifecycle is not in a state that allows the action."""


class GenerationInProgressError(Exception):
    """A generation/validation attempt is already in flight."""


# Semantic content fields: a change to any of them invalidates variant data.
# ``tags`` is shared metadata and deliberately excluded.
SEMANTIC_FIELDS = ("text", "problemType", "graphDsl", "correctAnswer", "subject")


def build_original_snapshot(draft: Mapping[str, Any]) -> dict[str, Any]:
    """Snapshot the semantic identity of the confirmed source draft."""
    return {field: draft.get(field) for field in SEMANTIC_FIELDS}


def _semantic_values(content: Mapping[str, Any] | None) -> dict[str, Any]:
    if not content:
        return {field: None for field in SEMANTIC_FIELDS}
    values: dict[str, Any] = {}
    for field in SEMANTIC_FIELDS:
        value = content.get(field)
        if isinstance(value, str):
            # Whitespace-only differences are formatting, not semantic
            # change (#648): strip all whitespace runs before comparing.
            # "" and whitespace-only both collapse to None (#634 rule).
            value = "".join(value.split()) or None
        else:
            # ponytail: "" and None both mean "no graph" — compare meaning, not representation.
            value = value or None
        values[field] = value
    return values


def has_semantic_change(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> bool:
    """True when any semantic field differs between the two content dicts."""
    return _semantic_values(before) != _semantic_values(after)


def problem_content_from_snapshot(snapshot: Mapping[str, Any]) -> ProblemContent:
    """Build the validator-facing source content from the stored snapshot.

    Subject is required by the variant contract (DATA-source-identity);
    variant ingestion is math-only (SCOPE-math-only), so a missing subject
    defaults to math instead of failing generation.
    """
    return ProblemContent(
        text=snapshot["text"],
        problemType=snapshot["problemType"],
        subject=snapshot.get("subject") or "math",
        graphDsl=snapshot.get("graphDsl"),
        correctAnswer=snapshot["correctAnswer"],
    )


def serialize_generation_result(result: VariantGenerationResult) -> dict[str, Any]:
    """Serialize validation evidence for storage and display.

    Keeps structured checks, solved answers, comparisons and failure evidence
    (with model identities); no hidden reasoning or provider secrets exist on
    the report models.
    """
    return {
        "verdict": result.assessment.verdict,
        "failures": [failure.model_dump() for failure in result.assessment.failures],
        "reports": [
            report.model_dump(by_alias=True) for report in result.reports
        ],
    }


def serialize_variation_for_response(variation: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Client-facing variation view: status, progress and evidence only.

    ``claimToken``/``leaseUntil`` are internal fencing state and are never
    exposed.
    """
    if not variation:
        return None
    return {
        "status": variation.get("status"),
        "generationCount": variation.get("generationCount", 0),
        "original": variation.get("original"),
        "candidate": variation.get("candidate"),
        "validation": variation.get("validation"),
        "validatedRevision": variation.get("validatedRevision"),
        "attestation": variation.get("attestation"),
        "queuedAt": variation.get("queuedAt"),
    }


INGESTION_BATCHES_COLLECTION = "ingestion_batches"


async def backfill_variation_failure_kinds(database: Any) -> int:
    """One-time startup retag of legacy failure kinds (#658).

    Items validated before the #658 kind split stored every assessment
    failure as ``content``. Recompute the pure assessment over the stored
    original/candidate/reports under the canonical mode (as the worker
    does) and retag a stored failure's kind ONLY when its full evidence
    string exactly matches a recomputed failure — prefix matches are not
    enough. Entries the recompute cannot reproduce keep their kind;
    reports and the failure set are never mutated. Idempotent: a retag
    happens only where the stored kind actually differs.
    """
    collection = database[INGESTION_BATCHES_COLLECTION]
    retagged = 0
    cursor = collection.find(
        {
            "ingestionMode": {
                "$in": [mode.value for mode in VARIANT_INGESTION_MODES]
            }
        }
    )
    async for batch in cursor:
        for item in batch.get("items") or []:
            variation = item.get("variation") or {}
            validation = variation.get("validation") or {}
            failures = validation.get("failures") or []
            if (
                validation.get("verdict") != "fail"
                or not failures
                or not variation.get("original")
                or not variation.get("candidate")
            ):
                continue
            recomputed = assess_variant(
                mode=canonical_variation_mode(batch.get("ingestionMode")).value,
                source=problem_content_from_snapshot(variation["original"]),
                candidate=VariantCandidate.model_validate(variation["candidate"]),
                reports=[
                    ValidatorReport.model_validate(report)
                    for report in validation.get("reports") or []
                ],
            )
            recomputed_kinds = {
                failure.evidence: failure.kind for failure in recomputed.failures
            }
            new_failures = list(failures)
            changed = False
            for index, failure in enumerate(new_failures):
                kind = recomputed_kinds.get(failure.get("evidence"))
                if kind is not None and kind != failure.get("kind"):
                    new_failures[index] = {**failure, "kind": kind}
                    changed = True
                    retagged += 1
            if changed:
                await collection.update_one(
                    {"_id": batch["_id"], "items.itemId": item.get("itemId")},
                    {"$set": {"items.$.variation.validation.failures": new_failures}},
                )
    return retagged
