"""Deterministic variant assessment seam (issue #612).

Pure schemas and gate rules shared by the variant VLM clients and the later
worker. No provider I/O happens here; tests cross the same callable interface
as the future worker.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field

from app.domain.whiteboard import sanitize_whiteboard_dsl

from app.domain.variation_provenance import (
    FrozenContentSnapshot,
    ModelIdentity,
    OriginalProvenance,
    ProblemVariation,
    ValidationProvenance,
    VariantMode,
)

__all__ = [
    "VariantMode",
    "ModelIdentity",
    "ProblemContent",
    "VariantCandidate",
    "AssessmentFailure",
    "VariantAssessment",
    "VariantGenerationResult",
    "FrozenContentSnapshot",
    "OriginalProvenance",
    "ValidationProvenance",
    "ProblemVariation",
    "is_attestable",
]

Preservation = Literal["preserved", "changed"]
ComplexityShift = Literal["comparable", "materially-easier", "materially-harder"]
RepresentationShift = Literal["none-or-nonmaterial", "material"]
ModeCompliance = Literal["compliant", "noncompliant"]
SurfaceDivergence = Literal["substantial", "insufficient"]
GraphConsistency = Literal["consistent", "inconsistent", "not-applicable"]
DataChange = Literal["changed", "unchanged"]
WellPosed = Literal["yes", "no"]
ComparisonResult = Literal["equivalent", "different", "uncertain"]

# Category name -> values that keep the gate open.
PASSING_CHECK_VALUES: dict[str, frozenset[str]] = {
    "originalWellPosed": frozenset({"yes"}),
    "variantWellPosed": frozenset({"yes"}),
    "coreKnowledge": frozenset({"preserved"}),
    "solutionStructure": frozenset({"preserved"}),
    "quantityRoles": frozenset({"preserved"}),
    "difficultyShift": frozenset({"comparable"}),
    "numericComplexityShift": frozenset({"comparable"}),
    "representationShift": frozenset({"none-or-nonmaterial"}),
    "modeCompliance": frozenset({"compliant"}),
    "graphConsistency": frozenset({"consistent", "not-applicable"}),
    "dataChange": frozenset({"changed"}),
}

# transfer-variant additionally requires substantial surface divergence: a
# cosmetic reskin fails even when every deep mathematical check passes
# (issue #656). data-only keeps its strict wording-preservation contract and
# never requires surface divergence. Legacy "data-and-wording" continuation
# is judged under the same transfer-variant gate.
TRANSFER_VARIANT_PASSING_CHECKS: dict[str, frozenset[str]] = {
    **PASSING_CHECK_VALUES,
    "surfaceDivergence": frozenset({"substantial"}),
}


def passing_check_values(mode: VariantMode) -> dict[str, frozenset[str]]:
    """Required validator categories and their passing values for a mode."""
    if mode in ("transfer-variant", "data-and-wording"):
        return TRANSFER_VARIANT_PASSING_CHECKS
    return PASSING_CHECK_VALUES


class Check(BaseModel):
    category: str
    evidence: str


class AnswerComparison(BaseModel):
    result: ComparisonResult = "uncertain"
    evidence: str = ""


class ValidatorReport(BaseModel):
    validator_model: ModelIdentity = Field(alias="validatorModel")
    original_solved_answer: str | None = Field(default=None, alias="originalSolvedAnswer")
    variant_solved_answer: str | None = Field(default=None, alias="variantSolvedAnswer")
    original_solution_summary: str = Field(alias="originalSolutionSummary")
    variant_solution_summary: str = Field(alias="variantSolutionSummary")
    checks: dict[str, Check] = Field(default_factory=dict)
    answer_comparison_original: AnswerComparison = Field(
        default_factory=AnswerComparison, alias="answerComparisonOriginal"
    )
    answer_comparison_variant: AnswerComparison = Field(
        default_factory=AnswerComparison, alias="answerComparisonVariant"
    )

    model_config = {"populate_by_name": True}


class ProblemContent(BaseModel):
    text: str
    problem_type: str = Field(alias="problemType")
    subject: str
    graph_dsl: str | None = Field(default=None, alias="graphDsl")
    correct_answer: str = Field(alias="correctAnswer")

    model_config = {"populate_by_name": True}


class VariantCandidate(BaseModel):
    text: str
    problem_type: str = Field(alias="problemType")
    subject: str
    graph_dsl: str | None = Field(default=None, alias="graphDsl")
    correct_answer: str = Field(alias="correctAnswer")
    generator: ModelIdentity

    model_config = {"populate_by_name": True}


class AssessmentFailure(BaseModel):
    # Failure-kind split (#658): machines own correctness, humans own
    # judgment. ``content``/``answer`` are verified facts or correctness
    # failures and are never overridable; ``check`` are validator judgment
    # failures and may be attested away by the teacher. ``provider`` and
    # ``invalid-response`` are model-execution failures.
    kind: Literal["content", "check", "answer", "provider", "invalid-response"]
    evidence: str


class VariantAssessment(BaseModel):
    verdict: Literal["pass", "fail"]
    failures: list[AssessmentFailure] = Field(default_factory=list)


class VariantGenerationResult(BaseModel):
    """Complete orchestration outcome consumed by the later worker.

    ``candidate`` is None only when generation itself failed. ``reports``
    preserves every completed validator report (with model identity, solved
    answers and helper comparisons) even when a later step failed, so failure
    evidence stays inspectable.
    """

    candidate: VariantCandidate | None = None
    reports: list[ValidatorReport] = Field(default_factory=list)
    assessment: VariantAssessment


def _content_failure(evidence: str) -> AssessmentFailure:
    return AssessmentFailure(kind="content", evidence=evidence)


def _check_failure(evidence: str) -> AssessmentFailure:
    return AssessmentFailure(kind="check", evidence=evidence)


def _answer_failure(evidence: str) -> AssessmentFailure:
    return AssessmentFailure(kind="answer", evidence=evidence)


def check_candidate(mode: VariantMode, source: ProblemContent, candidate: VariantCandidate) -> list[AssessmentFailure]:
    """Cheap, conclusive checks run before any validator call."""
    failures: list[AssessmentFailure] = []
    if not candidate.text.strip():
        failures.append(_content_failure("candidate text is empty"))
    if not candidate.correct_answer.strip():
        failures.append(_content_failure("candidate correctAnswer is empty"))
    if candidate.problem_type != source.problem_type:
        failures.append(
            _content_failure(
                f"problemType mismatch: source '{source.problem_type}', candidate returned "
                f"'{candidate.problem_type}'"
            )
        )
    # Subject inheritance is part of the candidate contract: the variant must
    # carry the confirmed source subject (the generator path assigns it
    # server-side; this guard also covers pre-built/injected candidates).
    if candidate.subject != source.subject:
        failures.append(
            _content_failure(
                f"subject mismatch: source '{source.subject}', candidate returned "
                f"'{candidate.subject}'"
            )
        )
    source_has_graph = bool((source.graph_dsl or "").strip())
    candidate_graph = (candidate.graph_dsl or "").strip()
    if candidate.graph_dsl is not None and not candidate_graph:
        failures.append(_content_failure("candidate graphDsl is present but empty"))
    elif source_has_graph and not candidate_graph:
        failures.append(
            _content_failure(
                "candidate graphDsl is missing but the source problem has a graph"
            )
        )
    elif candidate_graph and not source_has_graph:
        failures.append(
            _content_failure(
                "candidate graphDsl is set but the source problem has no graph"
            )
        )
    if candidate_graph:
        cleaned = sanitize_whiteboard_dsl(candidate.graph_dsl)
        if cleaned is None:
            failures.append(
                _content_failure("candidate graphDsl failed the safety check and was not accepted")
            )
    if mode == "data-only" and candidate.text.strip() == source.text.strip():
        failures.append(_content_failure("dataChange: candidate data is unchanged from the source"))
    return failures


def _category_disagreements(
    first: ValidatorReport, second: ValidatorReport, *, mode: VariantMode
) -> list[AssessmentFailure]:
    failures: list[AssessmentFailure] = []
    for name in passing_check_values(mode):
        one = first.checks.get(name)
        two = second.checks.get(name)
        if one is None or two is None:
            continue
        # Critical-category comparison: the exact values must match, not just
        # their pass/fail membership (e.g. "consistent" vs "not-applicable",
        # or "materially-easier" vs "materially-harder", are disagreements).
        if one.category != two.category:
            failures.append(
                _check_failure(
                    f"validators disagree on {name}: "
                    f"'{one.category}' vs '{two.category}'"
                )
            )
    return failures


def _assess_report(
    report: ValidatorReport,
    *,
    mode: VariantMode,
    source: ProblemContent,
    candidate: VariantCandidate,
) -> list[AssessmentFailure]:
    failures: list[AssessmentFailure] = []
    for name, passing in passing_check_values(mode).items():
        check = report.checks.get(name)
        if check is None:
            failures.append(_content_failure(f"{name}: missing report category"))
            continue
        if check.category not in passing:
            failures.append(
                _check_failure(f"{name}: {check.category} - {check.evidence}")
            )
    graph_check = report.checks.get("graphConsistency")
    if graph_check is not None:
        source_has_graph = bool((source.graph_dsl or "").strip())
        candidate_has_graph = bool((candidate.graph_dsl or "").strip())
        if graph_check.category == "not-applicable":
            if source_has_graph or candidate_has_graph:
                # Graph presence was already content-verified by
                # check_candidate; the residual failure is validator
                # judgment contradicting a verified fact, so it stays
                # check-kind (#658).
                failures.append(
                    _check_failure(
                        "graphConsistency: not-applicable is invalid because a graph is present"
                    )
                )
        elif not source_has_graph and not candidate_has_graph:
            failures.append(
                _check_failure(
                    f"graphConsistency: {graph_check.category} is invalid because "
                    "neither problem has a graph; only not-applicable applies"
                )
            )
    if report.original_solved_answer is None or report.variant_solved_answer is None:
        failures.append(
            _answer_failure(
                "validator could not solve one of the problems; no helper comparison is possible"
            )
        )
    for side, comparison in (
        ("original", report.answer_comparison_original),
        ("variant", report.answer_comparison_variant),
    ):
        if comparison.result != "equivalent":
            failures.append(
                _answer_failure(
                    f"helper comparison for {side} answer: {comparison.result} - "
                    f"{comparison.evidence}"
                )
            )
    return failures


def assess_variant(
    *,
    mode: VariantMode,
    source: ProblemContent,
    candidate: VariantCandidate,
    reports: list[ValidatorReport],
) -> VariantAssessment:
    """Deterministic PASS/FAIL aggregation over completed validator reports.

    Provider/invalid-response failures must be supplied as pre-built failures by
    the caller; every failure here is derived from the stored evidence with a
    fixed kind (#658): candidate/content defects and incomplete evidence are
    ``content``, validator judgment failures are ``check``, and answer
    correctness failures are ``answer``. Fails closed: missing reports,
    missing categories, uncertain comparisons or a wrong report count
    block PASS.
    """
    failures = check_candidate(mode, source, candidate)
    if not reports:
        failures.append(_content_failure("no validator report was produced"))
    if len(reports) not in (1, 2):
        failures.append(
            _content_failure(f"expected 1 or 2 validator reports, received {len(reports)}")
        )
    else:
        if len(reports) == 2:
            failures.extend(_category_disagreements(reports[0], reports[1], mode=mode))
        for report in reports:
            failures.extend(
                _assess_report(report, mode=mode, source=source, candidate=candidate)
            )
    return VariantAssessment(
        verdict="pass" if not failures else "fail",
        failures=failures,
    )


def is_attestable(validation: Mapping[str, Any] | None) -> bool:
    """Whether a stored validation record may be attested into READY (#658).

    True for the #648 stale-PASS path and for a FAIL whose failures are all
    check-kind (validator judgment). Answer/content/provider kinds are
    correctness or verified facts and are never overridable; kind-less
    legacy entries and worker raw kinds fail closed. Status gating
    (needs-validation/failed) happens at the attest fence, not here.
    """
    if not validation:
        return False
    verdict = validation.get("verdict")
    if verdict == "pass":
        return True
    if verdict != "fail":
        return False
    failures = validation.get("failures") or []
    return bool(failures) and all(
        failure.get("kind") == "check" for failure in failures
    )
