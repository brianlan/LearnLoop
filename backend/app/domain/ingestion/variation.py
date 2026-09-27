"""Deterministic variant assessment seam (issue #612).

Pure schemas and gate rules shared by the variant VLM clients and the later
worker. No provider I/O happens here; tests cross the same callable interface
as the future worker.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.whiteboard import sanitize_whiteboard_dsl

VariantMode = Literal["data-only", "data-and-wording"]

Preservation = Literal["preserved", "changed"]
ComplexityShift = Literal["comparable", "materially-easier", "materially-harder"]
RepresentationShift = Literal["none-or-nonmaterial", "material"]
ModeCompliance = Literal["compliant", "noncompliant"]
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


class ModelIdentity(BaseModel):
    provider: str
    model: str


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
    graph_dsl: str | None = Field(default=None, alias="graphDsl")
    correct_answer: str = Field(alias="correctAnswer")

    model_config = {"populate_by_name": True}


class VariantCandidate(BaseModel):
    text: str
    problem_type: str = Field(alias="problemType")
    graph_dsl: str | None = Field(default=None, alias="graphDsl")
    correct_answer: str = Field(alias="correctAnswer")
    generator: ModelIdentity

    model_config = {"populate_by_name": True}


class AssessmentFailure(BaseModel):
    kind: Literal["content", "provider", "invalid-response"]
    evidence: str


class VariantAssessment(BaseModel):
    verdict: Literal["pass", "fail"]
    failures: list[AssessmentFailure] = Field(default_factory=list)


def _content_failure(evidence: str) -> AssessmentFailure:
    return AssessmentFailure(kind="content", evidence=evidence)


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
    if candidate.graph_dsl:
        cleaned = sanitize_whiteboard_dsl(candidate.graph_dsl)
        if cleaned is None:
            failures.append(
                _content_failure("candidate graphDsl failed the safety check and was not accepted")
            )
    if mode == "data-only" and candidate.text.strip() == source.text.strip():
        failures.append(_content_failure("dataChange: candidate data is unchanged from the source"))
    return failures


def _category_disagreements(
    first: ValidatorReport, second: ValidatorReport
) -> list[AssessmentFailure]:
    failures: list[AssessmentFailure] = []
    for name, passing in PASSING_CHECK_VALUES.items():
        one = first.checks.get(name)
        two = second.checks.get(name)
        if one is None or two is None:
            continue
        if (one.category in passing) != (two.category in passing):
            failures.append(
                _content_failure(
                    f"validators disagree on {name}: "
                    f"'{one.category}' vs '{two.category}'"
                )
            )
    return failures


def _assess_report(
    report: ValidatorReport,
    *,
    source: ProblemContent,
    candidate: VariantCandidate,
) -> list[AssessmentFailure]:
    failures: list[AssessmentFailure] = []
    for name, passing in PASSING_CHECK_VALUES.items():
        check = report.checks.get(name)
        if check is None:
            failures.append(_content_failure(f"{name}: missing report category"))
            continue
        if check.category not in passing:
            failures.append(
                _content_failure(f"{name}: {check.category} - {check.evidence}")
            )
    graph_check = report.checks.get("graphConsistency")
    if graph_check is not None and graph_check.category == "not-applicable":
        if source.graph_dsl or candidate.graph_dsl:
            failures.append(
                _content_failure(
                    "graphConsistency: not-applicable is invalid because a graph is present"
                )
            )
    if report.original_solved_answer is None or report.variant_solved_answer is None:
        failures.append(
            _content_failure(
                "validator could not solve one of the problems; no helper comparison is possible"
            )
        )
    for side, comparison in (
        ("original", report.answer_comparison_original),
        ("variant", report.answer_comparison_variant),
    ):
        if comparison.result != "equivalent":
            failures.append(
                _content_failure(
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
    the caller; here every failure is content-derived. Fails closed: missing
    reports, missing categories, uncertain comparisons or a wrong report count
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
            failures.extend(_category_disagreements(reports[0], reports[1]))
        for report in reports:
            failures.extend(_assess_report(report, source=source, candidate=candidate))
    return VariantAssessment(
        verdict="pass" if not failures else "fail",
        failures=failures,
    )
