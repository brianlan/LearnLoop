"""Atomic session persistence for problem variants (issue #685).

One live session per (problem, user): the partial unique index in
``storage/mongo.py`` rejects a second concurrent create with a duplicate-key
error, which the presentation layer maps to 409 (the client then enters the
existing session).

Mirrors the ingestion repository's predicate style: every interactive write
classifies a rejected predicate against a fresh read; every background write
returns ``bool`` and is fenced by the session's ``contentRevision`` plus
liveness (``submit IS NULL AND discardedAt IS NULL``). There is no claim
token or lease here — the in-process executor (issue #685 Q7) is fenced by
revision alone and is cancelled directly on override/discard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument

from app.domain.ingestion.variation import is_execution_only_failure
from app.problem_variation import (
    InvalidVariationStateError,
    RevisionMismatchError,
    VariationNotFoundError,
    VariationStatus,
    has_semantic_change,
)

PROBLEM_VARIANT_SESSIONS_COLLECTION = "problem_variant_sessions"

# A session may only be created for these modes; the legacy
# ``data-and-wording`` value is rejected at the presentation boundary.
CREATABLE_PROBLEM_VARIANT_MODES = ("data-only", "transfer-variant")

_LIVE_PREDICATE = {"submit": None, "discardedAt": None}


def _collection(database: Any) -> Any:
    return database[PROBLEM_VARIANT_SESSIONS_COLLECTION]


def _object_id(value: Any) -> Any:
    return value if isinstance(value, ObjectId) else ObjectId(str(value))


def build_problem_variant_session_document(
    *,
    problem_id: str,
    user_id: Any,
    mode: str,
    original: dict[str, Any],
    tags: list[str],
    now: datetime,
) -> dict[str, Any]:
    """Initial queued session document.

    ``variation`` mirrors ``item.variation`` field-for-field (sans claim/lease
    fencing state the in-process executor does not need) and ``contentRevision``
    starts at 0, mirroring a pre-Generate item; the first Generate override
    bumps it to 1. ``generationCount`` starts at 1: session creation starts the
    first generation run.
    """
    return {
        "_id": ObjectId(),
        "problemId": str(problem_id),
        "userId": user_id,
        "mode": mode,
        "contentRevision": 0,
        "tags": list(tags),
        "variation": {
            "status": VariationStatus.QUEUED.value,
            "generationCount": 1,
            "original": dict(original),
            "candidate": None,
            "validation": None,
            "validatedRevision": None,
            "attestation": None,
            "queuedAt": now,
        },
        "submit": None,
        "discardedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }


async def find_active_problem_variant_session(
    database: Any,
    user_id: Any,
    problem_id: str,
) -> dict[str, Any] | None:
    return await _collection(database).find_one(
        {
            "problemId": str(problem_id),
            "userId": user_id,
            **_LIVE_PREDICATE,
        }
    )


async def find_problem_variant_session(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
) -> dict[str, Any] | None:
    return await _collection(database).find_one(
        {
            "_id": _object_id(session_id),
            "problemId": str(problem_id),
            "userId": user_id,
        }
    )


def classify_session_conflict(
    session: dict[str, Any] | None, expected_revision: int
) -> None:
    """Turn a rejected predicate into the same conflict family as ingest."""
    if session is None or session.get("discardedAt") is not None:
        raise VariationNotFoundError("Variant session not found")
    if session.get("contentRevision") != expected_revision:
        raise RevisionMismatchError(
            f"expectedRevision {expected_revision} does not match session "
            f"contentRevision {session.get('contentRevision')}"
        )
    raise InvalidVariationStateError(
        f"Variant session is in state {session.get('variation', {}).get('status')}"
    )


async def claim_problem_variant_generation(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    *,
    claimed_revision: int,
    now: datetime,
) -> dict[str, Any] | None:
    """Atomically transition a queued session to ``generating``.

    The in-process executor's claim: fencing is the session's
    ``contentRevision`` (no token/lease is needed — the task handle is
    cancelled directly on override/discard). The claim predicate includes
    the revision the executor actually observed, so a superseded task can
    never consume a newly queued replacement attempt. Returns the claimed
    session or ``None`` when it was superseded or is no longer live.
    """
    claimed = await _collection(database).find_one_and_update(
        {
            "_id": _object_id(session_id),
            "problemId": str(problem_id),
            "userId": user_id,
            **_LIVE_PREDICATE,
            "variation.status": VariationStatus.QUEUED.value,
            "contentRevision": claimed_revision,
        },
        {
            "$set": {
                "variation.status": VariationStatus.GENERATING.value,
                "updatedAt": now,
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    return claimed


async def request_problem_variant_generation(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    *,
    expected_revision: int,
    now: datetime,
) -> bool:
    """Queue generation, overriding any in-flight attempt (issue #685).

    Unlike the batch ``request_variation_generation`` (which rejects
    in-flight work), a problem session's Generate override transitions
    queued/generating/validating → queued, clears the stale candidate and
    approval, and bumps ``contentRevision`` so the superseded task's fenced
    writes can never land. Mirrors ``repository.py`` field clearing.
    """
    result = await _collection(database).update_one(
        {
            "_id": _object_id(session_id),
            "problemId": str(problem_id),
            "userId": user_id,
            "contentRevision": expected_revision,
            **_LIVE_PREDICATE,
            "variation.status": {
                "$in": [
                    VariationStatus.QUEUED.value,
                    VariationStatus.GENERATING.value,
                    VariationStatus.VALIDATING.value,
                    VariationStatus.READY.value,
                    VariationStatus.FAILED.value,
                    VariationStatus.NEEDS_VALIDATION.value,
                ]
            },
        },
        {
            "$set": {
                "variation.status": VariationStatus.QUEUED.value,
                "variation.candidate": None,
                "variation.validation": None,
                "variation.validatedRevision": None,
                "variation.attestation": None,
                "variation.queuedAt": now,
                "updatedAt": now,
            },
            "$inc": {
                "contentRevision": 1,
                "variation.generationCount": 1,
            },
        },
    )
    return result.matched_count == 1


async def request_problem_variant_revalidation(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    *,
    expected_revision: int,
    now: datetime,
) -> bool:
    """Queue validator-only work for a needs-validation session.

    Keeps the source and candidate; does not touch ``contentRevision`` or
    ``generationCount`` (revision fencing separates consecutive validator-only
    runs).
    """
    result = await _collection(database).update_one(
        {
            "_id": _object_id(session_id),
            "problemId": str(problem_id),
            "userId": user_id,
            "contentRevision": expected_revision,
            **_LIVE_PREDICATE,
            "variation.status": VariationStatus.NEEDS_VALIDATION.value,
        },
        {
            "$set": {
                "variation.status": VariationStatus.QUEUED.value,
                "variation.validation": None,
                "variation.validatedRevision": None,
                "variation.attestation": None,
                "variation.queuedAt": now,
                "updatedAt": now,
            },
        },
    )
    return result.matched_count == 1


async def attest_problem_variant_validation(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    *,
    expected_revision: int,
    now: datetime,
) -> bool:
    """Restore READY by explicit user attestation.

    Same two eligibility branches as the batch attest predicate: a stale-PASS
    needs-validation session (#648), or a needs-validation/failed session
    whose failures are all check- or answer-kind (#658). Any non-check/answer
    (or kind-less legacy) failure entry fails the branch. ``validatedRevision``
    stays None so the two admission branches remain mutually exclusive.
    """
    result = await _collection(database).update_one(
        {
            "_id": _object_id(session_id),
            "problemId": str(problem_id),
            "userId": user_id,
            "contentRevision": expected_revision,
            **_LIVE_PREDICATE,
            "$or": [
                {
                    "variation.status": VariationStatus.NEEDS_VALIDATION.value,
                    "variation.validation.verdict": "pass",
                },
                {
                    "variation.status": {
                        "$in": [
                            VariationStatus.NEEDS_VALIDATION.value,
                            VariationStatus.FAILED.value,
                        ]
                    },
                    "variation.validation.verdict": "fail",
                    "variation.validation.failures.0": {"$exists": True},
                    "variation.validation.failures": {
                        "$not": {
                            "$elemMatch": {
                                "kind": {"$nin": ["check", "answer"]}
                            }
                        }
                    },
                },
            ],
        },
        {
            "$set": {
                "variation.status": VariationStatus.READY.value,
                "variation.attestation": {
                    "revision": expected_revision,
                    "at": now,
                },
                "updatedAt": now,
            },
        },
    )
    return result.matched_count == 1


async def save_problem_variant_checkpoint(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    *,
    claimed_revision: int,
    candidate: dict[str, Any],
    now: datetime,
) -> bool:
    """Persist the generated candidate and move to ``validating``.

    Fenced on the claimed revision and session liveness; snapshotting before
    any validator call mirrors the batch checkpoint contract.
    """
    result = await _collection(database).update_one(
        {
            "_id": _object_id(session_id),
            "problemId": str(problem_id),
            "userId": user_id,
            "contentRevision": claimed_revision,
            **_LIVE_PREDICATE,
            "variation.status": VariationStatus.GENERATING.value,
        },
        {
            "$set": {
                "variation.candidate": candidate,
                "variation.status": VariationStatus.VALIDATING.value,
                "updatedAt": now,
            },
        },
    )
    return result.matched_count == 1


async def save_problem_variant_result(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    *,
    claimed_revision: int,
    verdict: str,
    validation: dict[str, Any],
    candidate_present: bool,
    now: datetime,
) -> bool:
    """Land a validation verdict only for the current generation attempt.

    Status mapping is the single source shared with the batch pipeline
    (``save_variation_result``): pass lands ready; a fail with a stored
    candidate and only model-execution failures lands needs-validation;
    every other fail lands failed.
    """
    if verdict not in ("pass", "fail"):
        raise ValueError(f"Invalid variation verdict: {verdict}")
    if verdict == "pass":
        status = VariationStatus.READY.value
    elif candidate_present and is_execution_only_failure(validation):
        status = VariationStatus.NEEDS_VALIDATION.value
    else:
        status = VariationStatus.FAILED.value
    set_fields: dict[str, Any] = {
        "variation.status": status,
        "variation.validation": validation,
        "updatedAt": now,
    }
    if verdict == "pass":
        set_fields["variation.validatedRevision"] = claimed_revision
    result = await _collection(database).update_one(
        {
            "_id": _object_id(session_id),
            "problemId": str(problem_id),
            "userId": user_id,
            "contentRevision": claimed_revision,
            **_LIVE_PREDICATE,
            "variation.status": {
                "$in": [
                    VariationStatus.GENERATING.value,
                    VariationStatus.VALIDATING.value,
                ]
            },
        },
        {"$set": set_fields},
    )
    return result.matched_count == 1


_EDIT_ALLOWED = ("text", "problemType", "graphDsl", "correctAnswer")
_MAX_OCC_ATTEMPTS = 5


async def edit_problem_variant_candidate(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    *,
    candidate_update: dict[str, Any],
    tags: list[str] | None,
    expected_revision: int,
    now: datetime,
) -> None:
    """Edit a ready/needs-validation/failed candidate.

    Mirrors ``edit_variation_candidate`` including the #665 rule: a
    ``failed`` session with no candidate creates the candidate from nothing.
    A semantic candidate change enters needs-validation, clears the current
    approval and bumps ``contentRevision``; tags never invalidate.
    """
    collection = _collection(database)
    for _ in range(_MAX_OCC_ATTEMPTS):
        session = await collection.find_one(
            {
                "_id": _object_id(session_id),
                "problemId": str(problem_id),
                "userId": user_id,
            }
        )
        if session is None or session.get("discardedAt") is not None:
            raise VariationNotFoundError("Variant session not found")

        variation = dict(session.get("variation") or {})
        base = variation.get("candidate")
        if base is None:
            # Candidate-from-nothing (#665): seed the immutable fields the
            # PATCH schema doesn't accept so the hand-built candidate passes
            # VariantCandidate validation when the executor revalidates it.
            original = variation.get("original") or {}
            base = {
                "subject": original.get("subject", ""),
                # Provenance for a hand-built candidate: no model made it.
                "generator": {"provider": "user", "model": "manual-edit"},
            }
        merged_candidate = {
            **base,
            **candidate_update,
        }
        semantic_changed = has_semantic_change(
            variation.get("candidate"), merged_candidate
        )

        set_fields: dict[str, Any] = {
            "variation.candidate": merged_candidate,
            "updatedAt": now,
        }
        update: dict[str, Any] = {"$set": set_fields}
        if semantic_changed:
            update["$set"].update(
                {
                    "variation.status": VariationStatus.NEEDS_VALIDATION.value,
                    # The stored report stays visible (labeled stale); only
                    # the approval pointer and attestation are cleared.
                    "variation.validatedRevision": None,
                    "variation.attestation": None,
                }
            )
            update["$inc"] = {"contentRevision": 1}
        if tags is not None:
            update["$set"]["tags"] = tags

        result = await collection.find_one_and_update(
            {
                "_id": _object_id(session_id),
                "problemId": str(problem_id),
                "userId": user_id,
                "contentRevision": expected_revision,
                **_LIVE_PREDICATE,
                "variation.status": {
                    "$in": [
                        VariationStatus.READY.value,
                        VariationStatus.NEEDS_VALIDATION.value,
                        VariationStatus.FAILED.value,
                    ]
                },
            },
            update,
            return_document=ReturnDocument.AFTER,
        )
        if result is not None:
            return
        fresh = await collection.find_one(
            {
                "_id": _object_id(session_id),
                "problemId": str(problem_id),
                "userId": user_id,
            }
        )
        classify_session_conflict(fresh, expected_revision)
    raise RuntimeError("edit_problem_variant_candidate lost too many concurrent races")


async def update_problem_variant_session_tags(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    *,
    tags: list[str],
    now: datetime,
) -> bool:
    """Status-independent tag write (mirrors ``update_item_draft`` semantics).

    Tags are shared draft metadata, never semantic variant content: the write
    works in every live status — including queued/generating/validating — and
    never bumps ``contentRevision``.
    """
    result = await _collection(database).update_one(
        {
            "_id": _object_id(session_id),
            "problemId": str(problem_id),
            "userId": user_id,
            **_LIVE_PREDICATE,
        },
        {"$set": {"tags": list(tags), "updatedAt": now}},
    )
    return result.matched_count == 1


async def mark_problem_variant_session_submitted(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    *,
    admitted_problem_id: Any,
    now: datetime,
    session: Any = None,
) -> bool:
    """Record the admission inside the caller's transaction.

    Must run in the same Mongo transaction that inserted the admitted
    Problem, so the problem and the session record commit or roll back
    together. Requires the session to still be live; a concurrent
    submit/generate/discard makes it match nothing.
    """
    result = await _collection(database).update_one(
        {
            "_id": _object_id(session_id),
            "problemId": str(problem_id),
            "userId": user_id,
            **_LIVE_PREDICATE,
        },
        {
            "$set": {
                "submit": {
                    "submittedProblemId": str(admitted_problem_id),
                    "success": True,
                    "failureCode": None,
                    "failureMessage": None,
                },
                "updatedAt": now,
            },
        },
        session=session,
    )
    return result.matched_count == 1


async def discard_problem_variant_session(
    database: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    *,
    now: datetime,
) -> bool:
    """Terminal discard. Rejected after submit; idempotent otherwise."""
    result = await _collection(database).update_one(
        {
            "_id": _object_id(session_id),
            "problemId": str(problem_id),
            "userId": user_id,
            "submit": None,
            "discardedAt": None,
        },
        {"$set": {"discardedAt": now, "updatedAt": now}},
    )
    return result.matched_count == 1
