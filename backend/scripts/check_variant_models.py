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

from app.domain.ingestion.variation import ProblemContent  # noqa: E402
from app.infrastructure.config.settings import get_settings  # noqa: E402
from app.infrastructure.vlm.variant_client import (  # noqa: E402
    _profile_unconfigured,
    build_variant_generator_vlm_client,
    build_variant_helper_vlm_client,
    build_variant_validator_vlm_client,
    generate_and_validate,
)

# Corpus: (case name, mode, source content, expected verdict).
CASES = [
    (
        "equivalent-fraction-decimal",
        "data-only",
        ProblemContent(
            text="What is 1/2 of 20?",
            problemType="short-answer",
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
            graphDsl=None,
            correctAnswer="x=7; y=3",
        ),
        "pass",
    ),
    (
        "multi-blank-omission",
        "data-only",
        ProblemContent(
            text="Solve for x and y: x + y = 10 and x - y = 4.",
            problemType="short-answer",
            graphDsl=None,
            correctAnswer="x=7; y=3",
        ),
        "fail",
    ),
    (
        "numeric-blowup-harder",
        "data-and-wording",
        ProblemContent(
            text="Compute 12 + 13.",
            problemType="short-answer",
            graphDsl=None,
            correctAnswer="25",
        ),
        "fail",
    ),
    (
        "changed-reasoning-direction",
        "data-and-wording",
        ProblemContent(
            text="A train travels 120 km in 2 hours. What is its speed in km/h?",
            problemType="short-answer",
            graphDsl=None,
            correctAnswer="60",
        ),
        "fail",
    ),
    (
        "wording-violation-data-only",
        "data-only",
        ProblemContent(
            text="A car travels 150 km in 3 hours. What is its speed in km/h?",
            problemType="short-answer",
            graphDsl=None,
            correctAnswer="50",
        ),
        "fail",
    ),
    (
        "percentage-marker-required",
        "data-and-wording",
        ProblemContent(
            text="A price rises from 40 to 50. By what percent did it rise?",
            problemType="short-answer",
            graphDsl=None,
            correctAnswer="25%",
        ),
        "pass",
    ),
    (
        "stale-graph-values",
        "data-only",
        ProblemContent(
            text="The rectangle below has width 6 and height 4. What is its area?",
            problemType="short-answer",
            graphDsl="create('board', {boundingbox: [-1, 5, 9, -1]});",
            correctAnswer="24",
        ),
        "fail",
    ),
]

_PLACEHOLDER = "replace-me"


def _profile_missing() -> list[str]:
    settings = get_settings()
    missing = []
    for prefix in ("variant_generator_vlm", "variant_validator_vlm", "helper_vlm"):
        if _profile_unconfigured(getattr(settings, f"{prefix}_model")) or _profile_unconfigured(
            getattr(settings, f"{prefix}_api_key")
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
    for name, mode, source, expected in CASES:
        result = await generate_and_validate(
            mode=mode,  # type: ignore[arg-type]
            source=source,
            generator=generator,
            validators=validators,
            helper=helper,
        )
        assessment = result.assessment
        status = "OK" if assessment.verdict == expected else "DISCREPANCY"
        if status == "DISCREPANCY":
            discrepancies += 1
        print(f"[{status}] {name}: expected={expected} actual={assessment.verdict}")
        for failure in assessment.failures:
            print(f"    {failure.kind}: {failure.evidence}")

    if discrepancies:
        print(f"{discrepancies} case(s) did not match the expected outcome. See evidence above.")
        return 1
    print("All sample cases matched the expected outcomes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
