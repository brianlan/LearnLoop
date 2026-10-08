"""Deterministic gate tests for the variant assessment seam (issue #612)."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.ingestion.variation import (
    AnswerComparison,
    AssessmentFailure,
    Check,
    ModelIdentity,
    ProblemContent,
    VariantMode,
    ValidatorReport,
    assess_variant,
    check_candidate,
)
from app.infrastructure.vlm.variant_client import VariantCandidate

SOURCE = ProblemContent(
    text="A train travels 120 km in 2 hours. What is its speed in km/h?",
    problemType="short-answer",
    subject="mathematics",
    graphDsl=None,
    correctAnswer="60",
)

CANDIDATE = VariantCandidate(
    text="A train travels 180 km in 3 hours. What is its speed in km/h?",
    problemType="short-answer",
    subject="mathematics",
    graphDsl=None,
    correctAnswer="60",
    generator=ModelIdentity(provider="openai", model="gen-1"),
)

IDENTITY = ModelIdentity(provider="openai", model="val-1")

PASSING_CATEGORIES: dict[str, str] = {
    "originalWellPosed": "yes",
    "variantWellPosed": "yes",
    "coreKnowledge": "preserved",
    "solutionStructure": "preserved",
    "quantityRoles": "preserved",
    "difficultyShift": "comparable",
    "numericComplexityShift": "comparable",
    "representationShift": "none-or-nonmaterial",
    "modeCompliance": "compliant",
    "graphConsistency": "not-applicable",
    "dataChange": "changed",
}

# transfer-variant additionally requires substantial surface divergence
# (issue #656).
DEEP_PASSING_CATEGORIES: dict[str, str] = {
    **PASSING_CATEGORIES,
    "surfaceDivergence": "substantial",
}


def _report(
    *,
    categories: dict[str, str] | None = None,
    evidence: dict[str, str] | None = None,
    original_solved: str | None = "60",
    variant_solved: str | None = "60",
    original_cmp: str = "equivalent",
    variant_cmp: str = "equivalent",
    identity: ModelIdentity = IDENTITY,
) -> ValidatorReport:
    checks = {
        name: Check(category=category, evidence=(evidence or {}).get(name, "clear"))
        for name, category in (categories or PASSING_CATEGORIES).items()
    }
    return ValidatorReport(
        validatorModel=identity,
        originalSolvedAnswer=original_solved,
        variantSolvedAnswer=variant_solved,
        originalSolutionSummary="distance over time",
        variantSolutionSummary="distance over time",
        checks=checks,
        answerComparisonOriginal=AnswerComparison(result=original_cmp, evidence="both 60"),
        answerComparisonVariant=AnswerComparison(result=variant_cmp, evidence="both 60"),
    )


def _assess(
    reports: list[ValidatorReport], mode: VariantMode = "data-only"
) -> Any:
    return assess_variant(mode=mode, source=SOURCE, candidate=CANDIDATE, reports=reports)


def test_single_complete_passing_report_passes() -> None:
    assessment = _assess([_report()])
    assert assessment.verdict == "pass"
    assert assessment.failures == []


@pytest.mark.parametrize(
    "category,failing_value",
    [
        ("originalWellPosed", "no"),
        ("variantWellPosed", "no"),
        ("coreKnowledge", "changed"),
        ("solutionStructure", "changed"),
        ("quantityRoles", "changed"),
        ("difficultyShift", "materially-easier"),
        ("numericComplexityShift", "materially-harder"),
        ("representationShift", "material"),
        ("modeCompliance", "noncompliant"),
        ("graphConsistency", "inconsistent"),
        ("dataChange", "unchanged"),
    ],
)
def test_each_failing_category_blocks_pass_with_evidence(
    category: str, failing_value: str
) -> None:
    categories = {**PASSING_CATEGORIES, category: failing_value}
    assessment = _assess([_report(categories=categories)])
    assert assessment.verdict == "fail"
    assert any(
        # Category failures are validator judgment: check-kind (#658).
        failure.kind == "check" and failure.evidence.startswith(category)
        for failure in assessment.failures
    )


def test_difficulty_shift_comparable_covers_slightly_easier() -> None:
    categories = {**PASSING_CATEGORIES, "difficultyShift": "comparable"}
    assessment = _assess([_report(categories=categories)])
    assert assessment.verdict == "pass"


def test_missing_category_blocks_pass() -> None:
    categories = {k: v for k, v in PASSING_CATEGORIES.items() if k != "dataChange"}
    assessment = _assess([_report(categories=categories)])
    assert assessment.verdict == "fail"
    assert any("dataChange: missing report category" in f.evidence for f in assessment.failures)


def test_unsolvable_report_fails_and_blocks_helper() -> None:
    assessment = _assess([_report(original_solved=None)])
    assert assessment.verdict == "fail"
    assert any("could not solve" in f.evidence for f in assessment.failures)


@pytest.mark.parametrize("result", ["different", "uncertain"])
def test_helper_non_equivalent_blocks_pass(result: str) -> None:
    assessment = _assess([_report(variant_cmp=result)])
    assert assessment.verdict == "fail"
    assert any(
        f"helper comparison for variant answer: {result}" in f.evidence
        for f in assessment.failures
    )


def test_two_agreeing_reports_pass() -> None:
    second = _report(identity=ModelIdentity(provider="openai", model="val-2"))
    second = second.model_copy(
        update={
            "original_solution_summary": "a differently worded but same-structure explanation",
        }
    )
    assessment = _assess([_report(), second])
    assert assessment.verdict == "pass"


def test_two_validator_disagreement_blocks_pass() -> None:
    disagreeing = _report(
        categories={**PASSING_CATEGORIES, "difficultyShift": "materially-harder"},
        identity=ModelIdentity(provider="openai", model="val-2"),
    )
    assessment = _assess([_report(), disagreeing])
    assert assessment.verdict == "fail"
    assert any(
        "validators disagree on difficultyShift" in f.evidence for f in assessment.failures
    )


def test_differing_prose_alone_is_not_disagreement() -> None:
    second = _report(
        evidence={"solutionStructure": "a completely different explanation of the same method"},
        identity=ModelIdentity(provider="openai", model="val-2"),
    )
    assessment = _assess([_report(), second])
    assert assessment.verdict == "pass"


def test_report_count_outside_one_or_two_fails() -> None:
    assert _assess([]).verdict == "fail"
    assert any("no validator report" in f.evidence for f in _assess([]).failures)
    three = [_report(), _report(), _report()]
    assessment = _assess(three)
    assert assessment.verdict == "fail"
    assert any("expected 1 or 2" in f.evidence for f in assessment.failures)


def test_type_mismatch_fails_with_source_and_candidate_type() -> None:
    wrong_type = CANDIDATE.model_copy(update={"problem_type": "fill-in-the-blank"})
    failures = check_candidate("data-only", SOURCE, wrong_type)
    assert any(
        "problemType mismatch: source 'short-answer', candidate returned 'fill-in-the-blank'"
        in f.evidence
        for f in failures
    )


def test_subject_mismatch_fails_with_source_and_candidate_subject() -> None:
    """The candidate contract requires the inherited source subject."""
    mismatched = CANDIDATE.model_copy(update={"subject": "geography"})
    failures = check_candidate("data-only", SOURCE, mismatched)
    assert any(
        "subject mismatch: source 'mathematics', candidate returned 'geography'"
        in f.evidence
        for f in failures
    )


def test_incomplete_candidate_rejected_before_validators() -> None:
    empty_text = CANDIDATE.model_copy(update={"text": "  "})
    empty_answer = CANDIDATE.model_copy(update={"correct_answer": ""})
    for candidate in (empty_text, empty_answer):
        failures = check_candidate("data-only", SOURCE, candidate)
        assert failures


def test_unsafe_graph_dsl_rejected() -> None:
    unsafe = CANDIDATE.model_copy(update={"graph_dsl": "fetch('http://evil');"})
    failures = check_candidate("data-only", SOURCE, unsafe)
    assert any("graphDsl failed the safety check" in f.evidence for f in failures)


def test_two_argument_bounding_box_dsl_passes_safety_check() -> None:
    dsl = "board.setBoundingBox([-1, 7, 11, -1], true);"
    source = SOURCE.model_copy(update={"graph_dsl": dsl})
    candidate = CANDIDATE.model_copy(update={"graph_dsl": dsl})
    failures = check_candidate("data-only", source, candidate)
    assert not any("graphDsl failed the safety check" in f.evidence for f in failures)


def test_graph_not_applicable_invalid_when_graph_present() -> None:
    source_with_graph = SOURCE.model_copy(update={"graph_dsl": "create('board', {});"})
    candidate_with_graph = CANDIDATE.model_copy(update={"graph_dsl": "create('board', {});"})
    report = _report(
        categories={**PASSING_CATEGORIES, "graphConsistency": "not-applicable"}
    )
    assessment = assess_variant(
        mode="data-only",
        source=source_with_graph,
        candidate=candidate_with_graph,
        reports=[report],
    )
    assert assessment.verdict == "fail"
    assert any(
        "not-applicable is invalid because a graph is present" in f.evidence
        for f in assessment.failures
    )


def test_graphless_consistent_rejected() -> None:
    """The only valid graphless graphConsistency category is not-applicable."""
    report = _report(categories={**PASSING_CATEGORIES, "graphConsistency": "consistent"})
    assessment = assess_variant(
        mode="data-only", source=SOURCE, candidate=CANDIDATE, reports=[report]
    )
    assert assessment.verdict == "fail"
    assert any(
        "consistent is invalid because neither problem has a graph" in f.evidence
        for f in assessment.failures
    )


def test_two_validators_disagree_on_nonpassing_values_blocks_pass() -> None:
    """Exact category equality: two failing values are still a disagreement."""
    easier = _report(
        categories={**PASSING_CATEGORIES, "difficultyShift": "materially-easier"},
        identity=ModelIdentity(provider="openai", model="val-1"),
    )
    harder = _report(
        categories={**PASSING_CATEGORIES, "difficultyShift": "materially-harder"},
        identity=ModelIdentity(provider="openai", model="val-2"),
    )
    assessment = _assess([easier, harder])
    assert assessment.verdict == "fail"
    assert any(
        "validators disagree on difficultyShift: 'materially-easier' vs 'materially-harder'"
        in f.evidence
        for f in assessment.failures
    )


def test_two_validators_graph_category_value_disagreement_blocks_pass() -> None:
    """consistent vs not-applicable are not interchangeable pass values."""
    consistent = _report(
        categories={**PASSING_CATEGORIES, "graphConsistency": "consistent"},
        identity=ModelIdentity(provider="openai", model="val-1"),
    )
    not_applicable = _report(
        categories={**PASSING_CATEGORIES, "graphConsistency": "not-applicable"},
        identity=ModelIdentity(provider="openai", model="val-2"),
    )
    assessment = _assess([consistent, not_applicable])
    assert assessment.verdict == "fail"
    assert any(
        "validators disagree on graphConsistency: 'consistent' vs 'not-applicable'"
        in f.evidence
        for f in assessment.failures
    )


# ---------------------------------------------------------------------------
# Surface divergence gate (issue #656): deep similarity + surface diversity
# for transfer-variant; data-only never requires surface divergence.
# ---------------------------------------------------------------------------


def test_transfer_variant_with_substantial_surface_divergence_passes() -> None:
    assessment = _assess(
        [_report(categories=DEEP_PASSING_CATEGORIES)], mode="transfer-variant"
    )
    assert assessment.verdict == "pass"
    assert assessment.failures == []


def test_cosmetic_reskin_fails_even_when_deep_checks_pass() -> None:
    report = _report(
        categories={**PASSING_CATEGORIES, "surfaceDivergence": "insufficient"}
    )
    assessment = _assess([report], mode="transfer-variant")
    assert assessment.verdict == "fail"
    assert any(
        "surfaceDivergence: insufficient" in f.evidence for f in assessment.failures
    )


def test_transfer_variant_missing_surface_divergence_fails_closed() -> None:
    assessment = _assess([_report()], mode="transfer-variant")
    assert assessment.verdict == "fail"
    assert any(
        "surfaceDivergence: missing report category" in f.evidence
        for f in assessment.failures
    )


def test_transfer_variant_invalid_surface_divergence_category_fails_closed() -> None:
    report = _report(categories={**PASSING_CATEGORIES, "surfaceDivergence": "extreme"})
    assessment = _assess([report], mode="transfer-variant")
    assert assessment.verdict == "fail"
    assert any("surfaceDivergence: extreme" in f.evidence for f in assessment.failures)


def test_deep_structure_changed_fails_even_with_substantial_surface_divergence() -> None:
    categories = {**DEEP_PASSING_CATEGORIES, "solutionStructure": "changed"}
    assessment = _assess([_report(categories=categories)], mode="transfer-variant")
    assert assessment.verdict == "fail"
    assert any(
        "solutionStructure: changed" in f.evidence for f in assessment.failures
    )


def test_legacy_data_and_wording_continues_under_transfer_gate() -> None:
    """Legacy batches named data-and-wording are judged by the same
    transfer-variant contract (#656)."""
    assert _assess(
        [_report(categories=DEEP_PASSING_CATEGORIES)], mode="data-and-wording"
    ).verdict == "pass"
    reskin = _report(
        categories={**PASSING_CATEGORIES, "surfaceDivergence": "insufficient"}
    )
    assessment = _assess([reskin], mode="data-and-wording")
    assert assessment.verdict == "fail"
    assert any(
        "surfaceDivergence: insufficient" in f.evidence for f in assessment.failures
    )


def test_data_only_does_not_require_surface_divergence() -> None:
    """data-only keeps its wording contract: surface divergence is absent or
    insufficient without invalidating the assessment."""
    assert _assess([_report()]).verdict == "pass"
    report = _report(
        categories={**PASSING_CATEGORIES, "surfaceDivergence": "insufficient"}
    )
    assert _assess([report]).verdict == "pass"


def test_two_validators_disagree_on_surface_divergence_blocks_pass() -> None:
    substantial = _report(categories=DEEP_PASSING_CATEGORIES)
    insufficient = _report(
        categories={**PASSING_CATEGORIES, "surfaceDivergence": "insufficient"},
        identity=ModelIdentity(provider="openai", model="val-2"),
    )
    assessment = _assess([substantial, insufficient], mode="transfer-variant")
    assert assessment.verdict == "fail"
    assert any(
        "validators disagree on surfaceDivergence: 'substantial' vs 'insufficient'"
        in f.evidence
        for f in assessment.failures
    )


def test_data_only_unchanged_text_rejected_before_validators() -> None:
    identical = CANDIDATE.model_copy(update={"text": SOURCE.text})
    failures = check_candidate("data-only", SOURCE, identical)
    assert any("dataChange" in f.evidence for f in failures)


def test_provider_failure_kind_values() -> None:
    assert AssessmentFailure(kind="provider", evidence="x").kind == "provider"
    assert AssessmentFailure(kind="invalid-response", evidence="x").kind == "invalid-response"
    assert AssessmentFailure(kind="content", evidence="x").kind == "content"


# ---------------------------------------------------------------------------
# GraphDSL presence parity between source and candidate.
# ---------------------------------------------------------------------------

SAFE_GRAPH = "var A = board.create('point', [0, 0]);"


def test_missing_candidate_graph_for_graph_source_fails() -> None:
    source = SOURCE.model_copy(update={"graph_dsl": SAFE_GRAPH})
    failures = check_candidate("data-only", source, CANDIDATE)
    assert any(
        "candidate graphDsl is missing but the source problem has a graph" in f.evidence
        for f in failures
    )


def test_added_candidate_graph_for_graphless_source_fails() -> None:
    candidate = CANDIDATE.model_copy(update={"graph_dsl": SAFE_GRAPH})
    failures = check_candidate("data-only", SOURCE, candidate)
    assert any(
        "candidate graphDsl is set but the source problem has no graph" in f.evidence
        for f in failures
    )


def test_empty_candidate_graph_fails() -> None:
    candidate = CANDIDATE.model_copy(update={"graph_dsl": "   "})
    failures = check_candidate("data-only", SOURCE, candidate)
    assert any("candidate graphDsl is present but empty" in f.evidence for f in failures)


def test_graph_parity_with_matching_graphs_passes_gate() -> None:
    source = SOURCE.model_copy(update={"graph_dsl": SAFE_GRAPH})
    candidate = CANDIDATE.model_copy(update={"graph_dsl": SAFE_GRAPH})
    assert check_candidate("data-only", source, candidate) == []
    report = _report(categories={**PASSING_CATEGORIES, "graphConsistency": "consistent"})
    assessment = assess_variant(
        mode="data-only", source=source, candidate=candidate, reports=[report]
    )
    assert assessment.verdict == "pass"


# ---------------------------------------------------------------------------
# Failure-kind split and attestation eligibility (issues #658, #665).
# Machines provide correctness evidence (answer/check kinds are attestable);
# humans own the final call. Content kinds are verified facts and are never
# overridable.
# ---------------------------------------------------------------------------


def _kinds(assessment: Any) -> set[str]:
    return {f.kind for f in assessment.failures}


def test_check_category_failure_is_check_kind() -> None:
    """Validator judgment failures (e.g. 'too close to the template') are
    check-kind: the motivating transfer-variant case (#658)."""
    report = _report(
        categories={**DEEP_PASSING_CATEGORIES, "modeCompliance": "noncompliant"}
    )
    assessment = _assess([report], mode="transfer-variant")
    assert assessment.verdict == "fail"
    assert _kinds(assessment) == {"check"}


def test_category_disagreement_is_check_kind() -> None:
    second = _report(
        categories={**PASSING_CATEGORIES, "difficultyShift": "materially-harder"},
        identity=ModelIdentity(provider="openai", model="val-2"),
    )
    assert _kinds(_assess([_report(), second])) == {"check"}


def test_graph_consistency_structural_invalidity_is_check_kind() -> None:
    """Graph presence is content-verified earlier; the residual structural
    invalidity is validator judgment contradicting a verified fact."""
    source = SOURCE.model_copy(update={"graph_dsl": SAFE_GRAPH})
    candidate = CANDIDATE.model_copy(update={"graph_dsl": SAFE_GRAPH})
    report = _report(
        categories={**PASSING_CATEGORIES, "graphConsistency": "not-applicable"}
    )
    assessment = assess_variant(
        mode="data-only", source=source, candidate=candidate, reports=[report]
    )
    assert _kinds(assessment) == {"check"}


def test_incomplete_evidence_is_content_kind() -> None:
    """Missing categories, missing reports and wrong report counts are
    defects of the evidence, not judgment: never overridable."""
    missing_category = _assess(
        [_report(categories={k: v for k, v in PASSING_CATEGORIES.items() if k != "dataChange"})]
    )
    assert "content" in _kinds(missing_category)
    no_report = _assess([])
    assert _kinds(no_report) == {"content"}
    wrong_count = _assess([_report(), _report(), _report()])
    assert "content" in _kinds(wrong_count)


def test_transfer_variant_missing_surface_divergence_is_content_kind() -> None:
    assessment = _assess([_report()], mode="transfer-variant")
    assert "content" in _kinds(assessment)


def test_could_not_solve_is_answer_kind() -> None:
    assessment = _assess([_report(original_solved=None)])
    assert _kinds(assessment) == {"answer"}


def test_helper_comparison_failure_is_answer_kind() -> None:
    assessment = _assess([_report(variant_cmp="different")])
    assert _kinds(assessment) == {"answer"}


def test_candidate_deterministic_failures_are_content_kind() -> None:
    empty_text = CANDIDATE.model_copy(update={"text": "  "})
    failures = check_candidate("data-only", SOURCE, empty_text)
    assert {f.kind for f in failures} == {"content"}
    unchanged = CANDIDATE.model_copy(update={"text": SOURCE.text})
    failures = check_candidate("data-only", SOURCE, unchanged)
    assert {f.kind for f in failures} == {"content"}


def test_is_attestable_truth_table() -> None:
    from app.domain.ingestion.variation import is_attestable

    # Stale-PASS path (#648): attestable from needs-validation.
    assert is_attestable({"verdict": "pass"}) is True
    # Check-only FAIL: the #658 judgment path.
    check_only = {"verdict": "fail", "failures": [{"kind": "check", "evidence": "a"}]}
    assert is_attestable(check_only) is True
    multi_check = {
        "verdict": "fail",
        "failures": [
            {"kind": "check", "evidence": "a"},
            {"kind": "check", "evidence": "b"},
        ],
    }
    assert is_attestable(multi_check) is True
    # Answer-only FAIL: the #665 answer-override path.
    assert is_attestable(
        {"verdict": "fail", "failures": [{"kind": "answer", "evidence": "a"}]}
    ) is True
    # Check + answer mix: attestable in one click (#665).
    assert is_attestable(
        {
            "verdict": "fail",
            "failures": [
                {"kind": "check", "evidence": "a"},
                {"kind": "answer", "evidence": "b"},
            ],
        }
    ) is True
    # Any content failure makes the set non-attestable.
    assert is_attestable(
        {
            "verdict": "fail",
            "failures": [
                {"kind": "check", "evidence": "a"},
                {"kind": "content", "evidence": "b"},
            ],
        }
    ) is False
    assert is_attestable(
        {"verdict": "fail", "failures": [{"kind": "provider", "evidence": "a"}]}
    ) is False
    # Empty failures, missing verdict, missing validation: never attestable.
    assert is_attestable({"verdict": "fail", "failures": []}) is False
    assert is_attestable({"verdict": "fail"}) is False
    assert is_attestable({}) is False
    assert is_attestable(None) is False
    # Kind-less legacy entries and worker raw kinds are excluded by design.
    assert is_attestable(
        {"verdict": "fail", "failures": [{"evidence": "legacy entry"}]}
    ) is False
    assert is_attestable(
        {"verdict": "fail", "failures": [{"kind": "vlm-timeout", "evidence": "a"}]}
    ) is False


# ---------------------------------------------------------------------------
# Execution-only failures and never-ran comparisons (#671).
# ---------------------------------------------------------------------------


def test_is_execution_only_failure_truth_table() -> None:
    from app.domain.ingestion.variation import is_execution_only_failure

    # The reported production case: invalid-response with the VLM JSON text.
    assert is_execution_only_failure(
        {
            "verdict": "fail",
            "failures": [
                {
                    "kind": "invalid-response",
                    "evidence": (
                        "Model execution failure: validator openai/x failed: "
                        "VLM provider response content was not valid JSON"
                    ),
                }
            ],
        }
    ) is True
    assert is_execution_only_failure(
        {"verdict": "fail", "failures": [{"kind": "provider", "evidence": "down"}]}
    ) is True
    # Worker raw kinds (exc.code), including the prefix rule.
    assert is_execution_only_failure(
        {"verdict": "fail", "failures": [{"kind": "vlm-timeout", "evidence": "a"}]}
    ) is True
    assert is_execution_only_failure(
        {
            "verdict": "fail",
            "failures": [{"kind": "vlm-profile-invalid", "evidence": "a"}],
        }
    ) is True
    # Several execution failures are still execution-only.
    assert is_execution_only_failure(
        {
            "verdict": "fail",
            "failures": [
                {"kind": "provider", "evidence": "a"},
                {"kind": "invalid-response", "evidence": "b"},
            ],
        }
    ) is True
    # Mixed with any judgment/content kind: a verdict exists -> False.
    for kind in ("check", "answer", "content", "invalid-candidate"):
        assert is_execution_only_failure(
            {
                "verdict": "fail",
                "failures": [
                    {"kind": "provider", "evidence": "a"},
                    {"kind": kind, "evidence": "b"},
                ],
            }
        ) is False
    # Kind-less legacy entry: fails closed without raising.
    assert is_execution_only_failure(
        {"verdict": "fail", "failures": [{"evidence": "legacy"}]}
    ) is False
    # Empty failures, missing/non-fail verdict, missing validation.
    assert is_execution_only_failure({"verdict": "fail", "failures": []}) is False
    assert is_execution_only_failure({"verdict": "fail"}) is False
    assert is_execution_only_failure({"verdict": "pass", "failures": []}) is False
    assert is_execution_only_failure({}) is False
    assert is_execution_only_failure(None) is False


def test_never_ran_comparison_produces_no_synthetic_answer_failure() -> None:
    """A report whose helper comparisons never ran (None) adds no synthetic
    uncertain answer failures (#671); the client path that skipped the
    comparison records its own explicit failure."""
    report = _report().model_copy(
        update={"answer_comparison_original": None, "answer_comparison_variant": None}
    )
    assessment = _assess([report])
    assert not any(f.kind == "answer" for f in assessment.failures)
    assert not any("helper comparison" in f.evidence for f in assessment.failures)


def test_ran_uncertain_comparison_still_fails() -> None:
    """A comparison that ran and is uncertain remains an answer failure."""
    assessment = _assess([_report(variant_cmp="uncertain")])
    assert assessment.verdict == "fail"
    assert any(
        "helper comparison for variant answer: uncertain" in f.evidence
        for f in assessment.failures
    )
