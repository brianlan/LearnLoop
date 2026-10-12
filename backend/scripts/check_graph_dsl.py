"""Stored-DSL rejection sentinel for the graphDsl sanitizer (issue #691).

Scans every stored ``problems.graphDsl`` and every active ingestion-batch
item's source draft and persisted variation candidate through
``sanitize_whiteboard_dsl`` and exits nonzero when a rejection appears that is
not on the known-exceptions list. Run from the repo root inside the dev/agent
environment:

    python backend/scripts/check_graph_dsl.py [--exceptions PATH]

Known-exceptions file format (JSON):

    {
      "exceptions": [
        {
          "id": "<problem or batch-item id prefix>",
          "reason": "<why this stored rejection is expected>"
        }
      ]
    }

A stored non-empty DSL that the sanitizer rejects is an unexpected rejection
unless its document id starts with one of the listed prefixes. An empty or
missing graphDsl is not a rejection. The truncated fragment recorded during
the graphDsl incident (dev batch 6acc7432, item 46f7d5ed) is the only known
exception at introduction time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import AsyncMongoClient  # noqa: E402

from app.domain.whiteboard.graph_dsl import sanitize_whiteboard_dsl  # noqa: E402
from app.infrastructure.config.settings import get_settings  # noqa: E402

DEFAULT_EXCEPTIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "check_graph_dsl_exceptions.json"
)


def _load_exceptions(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return {entry["id"]: entry.get("reason", "") for entry in payload["exceptions"]}


def _expected_reason(document_id: str, exceptions: dict[str, str]) -> str | None:
    for prefix, reason in exceptions.items():
        if document_id.startswith(prefix):
            return reason
    return None


async def _scan(exceptions: dict[str, str]) -> int:
    settings = get_settings()
    client = AsyncMongoClient(settings.mongodb_uri)
    database = client[settings.mongodb_database]

    scanned = 0
    expected: list[tuple[str, str, str]] = []
    unexpected: list[tuple[str, str, str]] = []

    def _record(location: str, document_id: str, dsl: str) -> None:
        reason = _expected_reason(document_id, exceptions)
        target = expected if reason is not None else unexpected
        target.append((f"{location} {document_id}", dsl, reason or ""))

    problems = await database["problems"].find(
        {"graphDsl": {"$nin": [None, ""]}}
    ).to_list(length=None)
    for problem in problems:
        dsl = problem.get("graphDsl")
        if not isinstance(dsl, str) or not dsl.strip():
            continue
        scanned += 1
        if sanitize_whiteboard_dsl(dsl) is None:
            _record("problems", str(problem["_id"]), dsl)

    batches = await database["ingestion_batches"].find({"status": "active"}).to_list(
        length=None
    )
    for batch in batches:
        for item in batch.get("items", []):
            document_id = str(item.get("itemId"))
            # Admission copies the candidate DSL into the problem, so both the
            # source draft and the persisted candidate must pass the sanitizer.
            variation = item.get("variation") or {}
            candidates = {
                "draft": (item.get("draft") or {}).get("graphDsl"),
                "candidate": (variation.get("candidate") or {}).get("graphDsl"),
            }
            for source, dsl in candidates.items():
                if not isinstance(dsl, str) or not dsl.strip():
                    continue
                scanned += 1
                if sanitize_whiteboard_dsl(dsl) is None:
                    _record(f"ingestion_batches {source}", document_id, dsl)

    print(f"Scanned {scanned} stored graphDsl value(s) through the sanitizer.")
    for location, dsl, reason in expected:
        print(f"[EXPECTED] {location}: {reason}\n    {dsl}")
    for location, dsl, _ in unexpected:
        print(f"[UNEXPECTED] {location} stores DSL the sanitizer rejects:\n    {dsl}")

    if unexpected:
        print(
            f"{len(unexpected)} unexpected rejection(s). Fix the stored DSL or add a "
            "documented known-exception; do not silence the sentinel silently."
        )
        return 1
    print("No unexpected stored-DSL rejections.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sentinel: scan stored graphDsl for sanitizer rejections."
    )
    parser.add_argument(
        "--exceptions",
        default=DEFAULT_EXCEPTIONS_PATH,
        help="path to the known-exceptions JSON file "
        f"(default: {DEFAULT_EXCEPTIONS_PATH})",
    )
    args = parser.parse_args()
    return asyncio.run(_scan(_load_exceptions(args.exceptions)))


if __name__ == "__main__":
    raise SystemExit(main())
