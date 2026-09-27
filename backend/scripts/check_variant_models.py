"""Opt-in real-model sample check for the variant generation gate (issue #612).

Run inside the agent/dev shell with explicit test profiles configured:

    uv run --frozen --active python scripts/check_variant_models.py

Normal CI never runs this script: it requires real provider credentials and
reports honestly what the configured models actually produced. A missing
provider configuration is reported as UNVERIFIED, never as a quality pass.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.domain.ingestion.variation import (  # noqa: E402
    ModelIdentity,
    ProblemContent,
    VariantCandidate,
)
from app.infrastructure.config.settings import get_settings  # noqa: E402
from app.infrastructure.vlm.variant_client import (  # noqa: E402
    _profile_unconfigured,
    build_variant_generator_vlm_client,
    build_variant_helper_vlm_client,
    build_variant_validator_vlm_client,
    generate_and_validate,
)

# Generator-quality cases: source-only; a compliant generation can satisfy the
# expected outcome, so the result records real generator quality.
GENERATION_CASES: list[tuple[str, str, ProblemContent, str]] = [
    (
        "equivalent-fraction-decimal",
        "data-only",
        ProblemContent(
            text="What is 1/2 of 20?",
            problemType="short-answer",
            subject="mathematics",
            graphDsl=None,
            correctAnswer="10",
        ),
        "pass",
    ),
    (
        "exact-division-remainder",
        "data-only",
        ProblemContent(
            text="A teacher shares 17 pencils equally among 5 students. "
            "How many whole pencils does each student get, and how many are left over?",
            problemType="short-answer",
            subject="mathematics",
            graphDsl=None,
            correctAnswer="3 remainder 2",
        ),
        "pass",
    ),
    (
        "multi-blank-complete-equivalent",
        "data-only",
        ProblemContent(
            text="Solve for x and y: x + y = 10 and x - y = 4.",
            problemType="short-answer",
            subject="mathematics",
            graphDsl=None,
            correctAnswer="x=7; y=3",
        ),
        "pass",
    ),
    (
        "percentage-marker-generation",
        "data-and-wording",
        ProblemContent(
            text="A price rises from 40 to 50. By what percent did it rise?",
            problemType="short-answer",
            subject="mathematics",
            graphDsl=None,
            correctAnswer="25%",
        ),
        "pass",
    ),
]

# Deterministic gate cases: an explicitly invalid (or form-contrast) candidate
# is injected, so the expected outcome tests the validator/gate itself, not
# whether a compliant generator happens to misbehave.
_INJECTED = ModelIdentity(provider="injected", model="deterministic-gate-case")


def _gate_candidate(
    *, text: str, correct_answer: str, graph_dsl: str | None = None
) -> VariantCandidate:
    return VariantCandidate(
        text=text,
        problemType="short-answer",
        subject="mathematics",
        graphDsl=graph_dsl,
        correctAnswer=correct_answer,
        generator=_INJECTED,
    )


GATE_CASES: list[tuple[str, str, ProblemContent, VariantCandidate, str]] = [
    (
        "multi-blank-omission",
        "data-only",
        ProblemContent(
            text="Solve for x and y: x + y = 10 and x - y = 4.",
            problemType="short-answer",
            subject="mathematics",
            graphDsl=None,
            correctAnswer="x=7; y=3",
        ),
        _gate_candidate(
            text="Solve for x and y: x + y = 12 and x - y = 2.",
            correct_answer="x=7",
        ),
        "fail",
    ),
    (
        "percentage-marker-missing",
        "data-and-wording",
        ProblemContent(
            text="A price rises from 40 to 50. By what percent did it rise?",
            problemType="short-answer",
            subject="mathematics",
            graphDsl=None,
            correctAnswer="25%",
        ),
        _gate_candidate(
            text="A price increases from 60 to 75. By what percentage did it increase?",
            correct_answer="25",
        ),
        "fail",
    ),
    (
        "fraction-decimal-form-equivalent",
        "data-only",
        ProblemContent(
            text="A recipe uses 1/2 litre of milk. How much is that in decimal litres?",
            problemType="short-answer",
            subject="mathematics",
            graphDsl=None,
            correctAnswer="1/2",
        ),
        _gate_candidate(
            text="A recipe uses 1/2 litre of milk. Write the amount as a decimal number of litres.",
            correct_answer="0.5",
        ),
        "pass",
    ),
    (
        "numeric-blowup-harder",
        "data-and-wording",
        ProblemContent(
            text="Compute 12 + 13.",
            problemType="short-answer",
            subject="mathematics",
            graphDsl=None,
            correctAnswer="25",
        ),
        _gate_candidate(
            text="Compute 1200 + 1300.",
            correct_answer="2500",
        ),
        "fail",
    ),
    (
        "changed-reasoning-direction",
        "data-and-wording",
        ProblemContent(
            text="A train travels 120 km in 2 hours. What is its speed in km/h?",
            problemType="short-answer",
            subject="mathematics",
            graphDsl=None,
            correctAnswer="60",
        ),
        _gate_candidate(
            text="A train travels at 60 km/h for 2 hours. How far does it travel in km?",
            correct_answer="120",
        ),
        "fail",
    ),
    (
        "wording-violation-data-only",
        "data-only",
        ProblemContent(
            text="A car travels 150 km in 3 hours. What is its speed in km/h?",
            problemType="short-answer",
            subject="mathematics",
            graphDsl=None,
            correctAnswer="50",
        ),
        _gate_candidate(
            text="A cyclist rides 150 kilometres in 3 hours. How fast does she ride, in km/h?",
            correct_answer="50",
        ),
        "fail",
    ),
    (
        "stale-graph-values",
        "data-only",
        ProblemContent(
            text="The rectangle below has width 6 and height 4. What is its area?",
            problemType="short-answer",
            subject="mathematics",
            graphDsl="create('board', {boundingbox: [-1, 5, 9, -1]});",
            correctAnswer="24",
        ),
        _gate_candidate(
            text="The rectangle below has width 8 and height 5. What is its area?",
            graph_dsl="create('board', {boundingbox: [-1, 5, 9, -1]});",
            correct_answer="40",
        ),
        "fail",
    ),
]

_PLACEHOLDER = "replace-me"


def _profile_missing() -> list[str]:
    settings = get_settings()
    missing = []
    for prefix in ("variant_generator_vlm", "variant_validator_vlm", "helper_vlm"):
        if any(
            _profile_unconfigured(getattr(settings, f"{prefix}_{field}"))
            for field in ("endpoint", "model", "api_key")
        ):
            missing.append(prefix)
    return missing


async def _run() -> int:
    missing = _profile_missing()
    if missing:
        print("UNVERIFIED: real-model sample check skipped; missing provider configuration for:")
        for prefix in missing:
            print(f"  - {prefix}_*")
        print("Configure the profiles and rerun. This is not a quality pass.")
        return 1

    settings = get_settings()
    generator = build_variant_generator_vlm_client(settings)
    validator1 = build_variant_validator_vlm_client(settings)
    second_model = (settings.variant_validator2_vlm_model or "").strip()
    validators = (
        [validator1, build_variant_validator_vlm_client(settings, second=True)]
        if second_model and second_model != _PLACEHOLDER
        else [validator1]
    )
    helper = build_variant_helper_vlm_client(settings)

    discrepancies = 0

    async def _run_case(
        name: str, kind: str, mode: str, source: ProblemContent, expected: str,
        candidate: VariantCandidate | None,
    ) -> None:
        nonlocal discrepancies
        result = await generate_and_validate(
            mode=mode,  # type: ignore[arg-type]
            source=source,
            generator=generator,
            validators=validators,
            helper=helper,
            candidate=candidate,
        )
        assessment = result.assessment
        status = "OK" if assessment.verdict == expected else "DISCREPANCY"
        if status == "DISCREPANCY":
            discrepancies += 1
        print(f"[{status}] {name} ({kind}): expected={expected} actual={assessment.verdict}")
        for failure in assessment.failures:
            print(f"    {failure.kind}: {failure.evidence}")

    for name, mode, source, expected in GENERATION_CASES:
        await _run_case(name, "generation", mode, source, expected, candidate=None)
    for name, mode, source, candidate, expected in GATE_CASES:
        await _run_case(name, "gate", mode, source, expected, candidate=candidate)

    if discrepancies:
        print(f"{discrepancies} case(s) did not match the expected outcome. See evidence above.")
        return 1
    print("All sample cases matched the expected outcomes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
