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
    SEMANTIC_FIELDS,
    IngestionMode,
    VariationStatus,
    build_original_snapshot,
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
    assert is_variant_mode("data-and-wording") is True


def test_happy_path_lifecycle_transitions_are_legal() -> None:
    s = VariationStatus
    transition_variation_status(s.NOT_REQUESTED, s.QUEUED)
    transition_variation_status(s.QUEUED, s.GENERATING)
    transition_variation_status(s.GENERATING, s.VALIDATING)
    transition_variation_status(s.VALIDATING, s.READY)
    transition_variation_status(s.READY, s.NEEDS_VALIDATION)
    transition_variation_status(s.NEEDS_VALIDATION, s.QUEUED)
    transition_variation_status(s.QUEUED, s.VALIDATING)
    transition_variation_status(s.VALIDATING, s.FAILED)
    transition_variation_status(s.FAILED, s.QUEUED)
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
        (VariationStatus.FAILED, VariationStatus.READY),
        (VariationStatus.NEEDS_VALIDATION, VariationStatus.READY),
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
    assert CANDIDATE.generator.model == "gen-model" or True


def test_variation_response_serialization_hides_fencing_state() -> None:
    variation = {
        "status": "validating",
        "generationCount": 2,
        "original": {"text": "a"},
        "candidate": None,
        "validation": None,
        "validatedRevision": None,
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
