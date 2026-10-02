from __future__ import annotations

import pytest

from app.domain.state import InvalidStateTransitionError
from app.domain.ingestion.variation import (
    AssessmentFailure,
    VariantAssessment,
    VariantCandidate,
    VariantGenerationResult,
)
from app.problem_variation import (
    CREATABLE_INGESTION_MODES,
    SEMANTIC_FIELDS,
    IngestionMode,
    VariationStatus,
    build_original_snapshot,
    canonical_variation_mode,
    has_semantic_change,
    is_variant_mode,
    problem_content_from_snapshot,
    serialize_generation_result,
    serialize_variation_for_response,
    transition_variation_status,
)
from tests.domain.test_variant_validation import CANDIDATE, SOURCE


def test_ingestion_mode_defaults_and_variant_detection() -> None:
    assert is_variant_mode(None) is False
    assert is_variant_mode("original") is False
    assert is_variant_mode("data-only") is True
    assert is_variant_mode("transfer-variant") is True
    # Legacy persisted batches stay readable/continuable (#656).
    assert is_variant_mode("data-and-wording") is True


def test_transfer_variant_is_canonical_and_legacy_is_not_creatable() -> None:
    """New batches use transfer-variant; the legacy name is continuation-only (#656)."""
    assert IngestionMode.TRANSFER_VARIANT.value == "transfer-variant"
    assert IngestionMode.TRANSFER_VARIANT in CREATABLE_INGESTION_MODES
    assert IngestionMode.DATA_AND_WORDING not in CREATABLE_INGESTION_MODES


def test_canonical_variation_mode_normalizes_legacy_alias() -> None:
    assert canonical_variation_mode("data-and-wording") is IngestionMode.TRANSFER_VARIANT
    assert canonical_variation_mode("transfer-variant") is IngestionMode.TRANSFER_VARIANT
    assert canonical_variation_mode("data-only") is IngestionMode.DATA_ONLY
    assert canonical_variation_mode("original") is IngestionMode.ORIGINAL
    assert canonical_variation_mode(None) is IngestionMode.ORIGINAL


def test_happy_path_lifecycle_transitions_are_legal() -> None:
    s = VariationStatus
    transition_variation_status(s.NOT_REQUESTED, s.QUEUED)
    transition_variation_status(s.QUEUED, s.GENERATING)
    transition_variation_status(s.GENERATING, s.VALIDATING)
    transition_variation_status(s.VALIDATING, s.READY)
    transition_variation_status(s.READY, s.NEEDS_VALIDATION)
    transition_variation_status(s.NEEDS_VALIDATION, s.QUEUED)
    # "Keep validation" attestation is the only needs-validation -> READY edge.
    transition_variation_status(s.NEEDS_VALIDATION, s.READY)
    transition_variation_status(s.QUEUED, s.VALIDATING)
    transition_variation_status(s.VALIDATING, s.FAILED)
    transition_variation_status(s.FAILED, s.QUEUED)
    # Fail-attest (#658): a check-kind-only FAIL may be attested into READY;
    # this is the only failed -> READY edge.
    transition_variation_status(s.FAILED, s.READY)
    # Generate Again from ready discards the candidate and regenerates.
    transition_variation_status(s.READY, s.QUEUED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (VariationStatus.NOT_REQUESTED, VariationStatus.VALIDATING),
        (VariationStatus.NOT_REQUESTED, VariationStatus.READY),
        (VariationStatus.QUEUED, VariationStatus.READY),
        (VariationStatus.QUEUED, VariationStatus.NEEDS_VALIDATION),
        (VariationStatus.GENERATING, VariationStatus.READY),
        (VariationStatus.VALIDATING, VariationStatus.GENERATING),
        (VariationStatus.READY, VariationStatus.GENERATING),
        (VariationStatus.READY, VariationStatus.VALIDATING),
    ],
)
def test_illegal_lifecycle_transitions_raise(current: VariationStatus, target: VariationStatus) -> None:
    with pytest.raises(InvalidStateTransitionError):
        transition_variation_status(current, target)


def test_source_edit_invalidations_are_legal_from_every_active_state() -> None:
    s = VariationStatus
    for current in (s.QUEUED, s.GENERATING, s.VALIDATING, s.READY, s.NEEDS_VALIDATION, s.FAILED):
        transition_variation_status(current, s.NOT_REQUESTED)


def test_semantic_change_rules() -> None:
    before = {"text": "a", "problemType": "short-answer", "graphDsl": None,
              "correctAnswer": "1", "subject": "math", "tags": ["x"]}
    assert has_semantic_change(before, {**before, "tags": ["y"]}) is False
    assert has_semantic_change(before, {**before, "text": "b"}) is True
    assert has_semantic_change(before, {**before, "graphDsl": "create('board', {});"}) is True
    assert has_semantic_change(None, None) is False
    assert has_semantic_change({}, {}) is False
    assert has_semantic_change(None, before) is True


def test_semantic_change_treats_empty_graphdsl_as_none() -> None:
    before = {"text": "a", "problemType": "short-answer", "graphDsl": None,
              "correctAnswer": "1", "subject": "math"}
    empty = {**before, "graphDsl": ""}
    assert has_semantic_change(before, empty) is False
    assert has_semantic_change(empty, before) is False
    # A real graph change is still semantic in both directions.
    assert has_semantic_change(before, {**before, "graphDsl": "create('board', {});"}) is True
    assert has_semantic_change(
        {**before, "graphDsl": "create('board', {});"}, before
    ) is True


def test_semantic_change_ignores_whitespace_only_differences() -> None:
    """Whitespace-only edits are formatting, not semantic change (#648)."""
    before = {"text": "What is 2+2?", "problemType": "short-answer",
              "graphDsl": None, "correctAnswer": "4", "subject": "math"}
    # Internal whitespace, tabs, newlines and full-width spaces are all
    # formatting.
    assert has_semantic_change(
        before, {**before, "text": "What  is\t2+2?\n"}
    ) is False
    assert has_semantic_change(
        before, {**before, "text": "What\u3000is 2+2?"}
    ) is False
    assert has_semantic_change(
        before, {**before, "correctAnswer": " 4 "}
    ) is False
    # A whitespace-only value is equivalent to no value at all.
    assert has_semantic_change(before, {**before, "graphDsl": "  "}) is False
    # A real character difference is still semantic in both directions.
    assert has_semantic_change(
        before, {**before, "text": "What is 2+3?"}
    ) is True
    assert has_semantic_change(
        {**before, "text": "What is 2+3?"}, before
    ) is True


def test_original_snapshot_covers_semantic_fields_only() -> None:
    snapshot = build_original_snapshot(
        {"text": "a", "problemType": "short-answer", "correctAnswer": "1",
         "graphDsl": None, "subject": "math", "tags": ["t"], "extra": "ignored"}
    )
    assert set(snapshot) == set(SEMANTIC_FIELDS)
    assert "tags" not in snapshot
    assert "extra" not in snapshot


def test_problem_content_from_snapshot_defaults_subject_to_math() -> None:
    content = problem_content_from_snapshot(
        {"text": "Solve x.", "problemType": "short-answer", "graphDsl": None,
         "correctAnswer": "3", "subject": None}
    )
    assert content.subject == "math"
    assert content.text == "Solve x."
    assert content.correct_answer == "3"


def test_serialize_generation_result_keeps_structured_evidence() -> None:
    result = VariantGenerationResult(
        candidate=CANDIDATE,
        reports=[],
        assessment=VariantAssessment(
            verdict="fail",
            failures=[AssessmentFailure(kind="content", evidence="something broke")],
        ),
    )
    evidence = serialize_generation_result(result)
    assert evidence["verdict"] == "fail"
    assert evidence["failures"] == [{"kind": "content", "evidence": "something broke"}]
    assert evidence["reports"] == []
    # Provenance stays inspectable via the candidate stored beside it.
    assert result.candidate.generator.model == "gen-1"


def test_variation_response_serialization_hides_fencing_state() -> None:
    variation = {
        "status": "validating",
        "generationCount": 2,
        "original": {"text": "a"},
        "candidate": None,
        "validation": None,
        "validatedRevision": None,
        "attestation": None,
        "claimToken": "secret-token",
        "leaseUntil": "2026-01-01T00:00:00Z",
        "queuedAt": "2026-01-01T00:00:00Z",
    }
    view = serialize_variation_for_response(variation)
    assert view is not None
    assert view["status"] == "validating"
    assert view["generationCount"] == 2
    assert "claimToken" not in view
    assert "leaseUntil" not in view
    assert serialize_variation_for_response(None) is None


def test_variation_response_serialization_exposes_attestation() -> None:
    """The user-attestation record is client-visible evidence (#648)."""
    attestation = {"revision": 3, "at": "2026-01-01T00:00:00Z"}
    variation = {
        "status": "ready",
        "generationCount": 1,
        "original": {"text": "a"},
        "candidate": None,
        "validation": None,
        "validatedRevision": None,
        "attestation": attestation,
    }
    view = serialize_variation_for_response(variation)
    assert view is not None
    assert view["attestation"] == attestation


def test_problem_domain_model_types_variation_with_legacy_default() -> None:
    """Problem.variation is typed; legacy documents without variation parse as None (issue #614)."""
    from app.domain.ingestion.variation import ProblemVariation
    from app.domain.models import Problem

    audit = {
        "bucket": "learnloop-media",
        "objectKey": "users/u1/problems/audit/b1/i1.png",
        "contentType": "image/png",
        "sizeBytes": 10,
        "sha256": "abc",
        "uploadedAt": None,
    }
    correct = {
        "display": "4",
        "normalizedText": "4",
        "normalizedSet": [],
        "format": "single",
    }
    doc = {
        "userId": "u1",
        "text": "What is 3+5?",
        "problemType": "short-answer",
        "correctAnswer": correct,
        "variation": {
            "mode": "data-only",
            "original": {
                "text": "What is 2+2?",
                "problemType": "short-answer",
                "subject": "math",
                "graphDsl": None,
                "correctAnswer": correct,
                "auditImage": audit,
            },
            "acceptedVariant": {
                "text": "What is 3+5?",
                "problemType": "short-answer",
                "subject": "math",
                "graphDsl": None,
                "correctAnswer": correct,
            },
            "generator": {"provider": "fake", "model": "gen-model"},
            "generationCount": 1,
            "validation": {"verdict": "pass", "helperModel": None, "reports": []},
        },
    }

    parsed = Problem(**doc)
    assert isinstance(parsed.variation, ProblemVariation)
    assert parsed.variation.mode == "data-only"
    assert parsed.variation.original.auditImage == audit
    assert parsed.variation.acceptedVariant.text == "What is 3+5?"
    assert parsed.variation.generationCount == 1
    assert parsed.variation.validation.verdict == "pass"

    legacy = Problem(
        userId="u1",
        text="What is 2+2?",
        problemType="short-answer",
        correctAnswer=correct,
    )
    assert legacy.variation is None
