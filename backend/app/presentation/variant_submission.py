"""Transactional variant admission (issue #614).

Saves the current PASS candidate as the only practice Problem using a short
Mongo transaction, with immutable provenance and a permanent audit image.

Order of operations per item, per the issue contract:

1. Read the ready candidate revision and copy its crop into the permanent
   audit namespace (outside the transaction — storage calls must never run
   inside the retryable transaction callback).
2. Inside a short transaction: re-read fresh state and require the same
   passing revision, insert the Problem, record the item submitted, and
   enqueue the solution task — all on the same session.
3. After commit: register tags best-effort; a tag failure must never delete
   the committed Problem.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

from app.domain import ProblemSubject, ProblemType, normalize_answer
from app.domain.ingestion import BatchState, ItemState
from app.domain.ingestion.variation import (
    TRANSFER_VARIANT_PASSING_CHECKS,
    FrozenContentSnapshot,
    ModelIdentity,
    OriginalProvenance,
    ProblemVariation,
    ValidationProvenance,
)
from app.infrastructure.ingestion.documents import build_audit_image_key
from app.infrastructure.ingestion.repository import (
    INGESTION_BATCHES_COLLECTION,
    record_variant_item_submission,
)
from app.presentation.errors import ApiError
from app.presentation.tag_registration import _register_tags
from app.problem_variation import canonical_variation_mode, VariationStatus
from app.solution_generation import enqueue_solution_generation_task_for_problem

logger = logging.getLogger(__name__)


def copy_crop_to_audit_storage(
    storage: Any,
    user_id: Any,
    batch_id: Any,
    item_id: str,
    crop: Any,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Copy the item crop into the permanent audit namespace.

    The key is deterministic (batch/item identity), so a failed-submit retry
    overwrites the same object instead of accumulating copies. Returns the
    auditImage metadata stored on Problem.variation.original.
    """
    if not crop or not crop.get("bucket") or not crop.get("objectKey"):
        raise ApiError(409, "VARIANT_AUDIT_MISSING", "Item has no source crop to archive")
    data = storage.get_object(crop["bucket"], crop["objectKey"])
    object_key = build_audit_image_key(user_id, batch_id, item_id, crop.get("contentType"))
    content_type = crop.get("contentType") or "application/octet-stream"
    storage.put_object(crop["bucket"], object_key, data, content_type)
    return {
        "bucket": crop["bucket"],
        "objectKey": object_key,
        "contentType": content_type,
        "sizeBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "uploadedAt": now,
    }


def _frozen_snapshot(
    text: Any,
    problem_type: Any,
    subject: Any,
    graph_dsl: Any,
    correct_answer_raw: Any,
) -> dict[str, Any]:
    """Build one frozen content snapshot in the CorrectAnswer storage shape."""
    if (
        not isinstance(text, str)
        or not text.strip()
        or not isinstance(problem_type, str)
        or not isinstance(correct_answer_raw, str)
        or not correct_answer_raw.strip()
    ):
        raise ApiError(500, "VARIANT_ADMISSION_FAILED", "Invalid variant data")
    try:
        problem_type_enum = ProblemType(problem_type)
    except ValueError as exc:
        raise ApiError(500, "VARIANT_ADMISSION_FAILED", "Invalid variant data") from exc
    subject_value = subject if isinstance(subject, str) else ProblemSubject.MATH.value
    try:
        ProblemSubject(subject_value)
    except ValueError:
        subject_value = ProblemSubject.MATH.value
    return {
        "text": text,
        "problemType": problem_type_enum.value,
        "subject": subject_value,
        "graphDsl": graph_dsl if isinstance(graph_dsl, str) else None,
        "correctAnswer": normalize_answer(correct_answer_raw, problem_type_enum).model_dump(),
    }


def _admission_guard_failure(message: str) -> ApiError:
    return ApiError(409, "VARIANT_INVALIDATED", message)


def _validated_under_transfer_contract(validation: dict[str, Any]) -> bool:
    """Whether persisted reports prove the candidate passed the new
    transfer contract. Every new-contract validation reports
    surfaceDivergence; pre-#656 data-and-wording reports lack it."""
    passing_categories = TRANSFER_VARIANT_PASSING_CHECKS["surfaceDivergence"]
    return any(
        (report.get("checks") or {}).get("surfaceDivergence", {}).get("category")
        in passing_categories
        for report in validation.get("reports") or []
    )


def _build_variation_problem_document(
    batch: dict[str, Any],
    item: dict[str, Any],
    user_id: Any,
    audit_image: dict[str, Any],
    tags: list[str],
    now: datetime,
) -> dict[str, Any]:
    """Assemble the admitted Problem document from fresh item state.

    Main fields come from the accepted variant; the confirmed original lives
    only in the provenance (with its audit image). Raises ``ApiError`` when
    the persisted candidate data is unusable.
    """
    variation = item.get("variation") or {}
    candidate = variation.get("candidate") or {}
    validation = variation.get("validation") or {}
    original_snapshot = variation.get("original") or {}
    try:
        accepted_variant = _frozen_snapshot(
            candidate.get("text"),
            candidate.get("problemType"),
            candidate.get("subject"),
            candidate.get("graphDsl"),
            candidate.get("correctAnswer"),
        )
        original_content = _frozen_snapshot(
            original_snapshot.get("text"),
            original_snapshot.get("problemType"),
            original_snapshot.get("subject"),
            original_snapshot.get("graphDsl"),
            original_snapshot.get("correctAnswer"),
        )
    except ApiError:
        raise
    except Exception as exc:  # malformed persisted candidate must not crash the batch
        raise ApiError(500, "VARIANT_ADMISSION_FAILED", "Invalid variant data") from exc

    reports = validation.get("reports") or []
    helper_model = None
    if reports:
        first_model = reports[0].get("validatorModel") or {}
        if first_model.get("provider") and first_model.get("model"):
            helper_model = ModelIdentity(
                provider=first_model["provider"], model=first_model["model"]
            )
    generator = candidate.get("generator") or {}
    # #648: an attested admission covers the current revision via the user
    # attestation, not a validator run — frozen honestly into provenance.
    attested_by_user = (variation.get("attestation") or {}).get(
        "revision"
    ) == item.get("contentRevision")
    batch_mode = batch.get("ingestionMode") or "data-only"
    provenance_mode = canonical_variation_mode(batch_mode).value
    if (
        batch_mode == "data-and-wording"
        and not _validated_under_transfer_contract(validation)
    ):
        # #656: an old legacy candidate's report predates the
        # surfaceDivergence gate, so it never proved the transfer contract;
        # preserve the historical label instead of relabeling retroactively.
        provenance_mode = batch_mode
    provenance = ProblemVariation(
        mode=provenance_mode,
        original=OriginalProvenance(**original_content, auditImage=audit_image),
        acceptedVariant=FrozenContentSnapshot(**accepted_variant),
        generator=ModelIdentity(
            provider=str(generator.get("provider") or ""),
            model=str(generator.get("model") or ""),
        ),
        generationCount=int(variation.get("generationCount") or 0),
        validation=ValidationProvenance(
            verdict="pass",
            helperModel=helper_model,
            reports=list(reports),
            attestedByUser=attested_by_user,
        ),
    )

    return {
        "_id": ObjectId(),
        "userId": user_id,
        "text": accepted_variant["text"],
        "problemType": accepted_variant["problemType"],
        "subject": accepted_variant["subject"],
        "graphDsl": accepted_variant["graphDsl"],
        "correctAnswer": accepted_variant["correctAnswer"],
        "tags": tags,
        "sourceImage": None,
        "origin": dict(item.get("origin") or {}),
        "variation": provenance.model_dump(),
        "tracking": {
            "exposureCount": 0,
            "correctCount": 0,
            "failedCount": 0,
            "lastTestedAt": None,
            "lastAttemptCorrect": None,
        },
        "isDeleted": False,
        "deletedAt": None,
        "isDisabled": False,
        "createdAt": now,
        "updatedAt": now,
    }


def _check_admission_guards(
    batch: dict[str, Any], item_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Validate fresh batch/item state for admission.

    Returns ``(already_submitted_outcome, item)``: when the item was already
    submitted the outcome carries its recorded problem id (idempotent
    success); otherwise the outcome is ``None`` and the item is the fresh
    READY document. Raises ``ApiError`` (409 ``VARIANT_INVALIDATED``) when
    the item can no longer be admitted.
    """
    item = next(
        (i for i in batch.get("items", []) if i.get("itemId") == item_id),
        None,
    )
    if item is None:
        raise ApiError(404, "NOT_FOUND", "Item not found")
    # Idempotent read-back must precede the batch-status guard: a concurrent
    # submit may have completed the batch between this transaction's retries,
    # and the recorded problem stays authoritative either way.
    if item.get("status") == ItemState.SUBMITTED.value:
        submit = item.get("submit") or {}
        problem_id = submit.get("submittedProblemId")
        if submit.get("success") and problem_id:
            return {"problemId": str(problem_id), "alreadySubmitted": True}, item
        raise _admission_guard_failure("Item submission state is inconsistent")
    if batch.get("status") != BatchState.ACTIVE.value:
        raise _admission_guard_failure("Batch is no longer active")
    if item.get("status") != ItemState.READY.value:
        raise _admission_guard_failure("Item is no longer ready")

    variation = item.get("variation") or {}
    candidate = variation.get("candidate") or {}
    validation = variation.get("validation") or {}
    attestation = variation.get("attestation") or {}
    if not variation.get("original"):
        raise _admission_guard_failure("Item has no confirmed source")
    if variation.get("status") != VariationStatus.READY.value:
        raise _admission_guard_failure("Variant is not validated")
    if validation.get("verdict") != "pass":
        raise _admission_guard_failure("Variant validation did not pass")
    if not candidate:
        raise _admission_guard_failure("Variant candidate is missing")
    if (
        variation.get("validatedRevision") != item.get("contentRevision")
        and attestation.get("revision") != item.get("contentRevision")
    ):
        raise _admission_guard_failure("Validated variant is not the current version")
    return None, item


async def admit_variant_item(
    database: Any,
    adapter: Any,
    user_id: Any,
    batch_id: str | ObjectId,
    item_id: str,
    *,
    audit_image: dict[str, Any],
    tags: list[str],
    now: datetime,
) -> dict[str, Any]:
    """Admit one item's PASS candidate inside a short Mongo transaction.

    Re-reads fresh state inside the transaction and requires the same
    passing revision that was selected outside, then creates the Problem
    (main content from the accepted variant, ``sourceImage`` null, full
    provenance), records the item submitted, and enqueues the solution task
    on the same session. A concurrent invalidation therefore can never save
    the source or a stale candidate; an already-submitted item returns its
    recorded problem id instead of duplicating anything.
    """
    batch_object_id = batch_id if isinstance(batch_id, ObjectId) else ObjectId(str(batch_id))

    async def _transaction(session: Any) -> dict[str, Any]:
        batch = await database[INGESTION_BATCHES_COLLECTION].find_one(
            {"_id": batch_object_id, "userId": user_id}, session=session
        )
        if batch is None:
            raise ApiError(404, "NOT_FOUND", "Batch not found")
        expires_at = batch.get("expiresAt")
        if expires_at is not None:
            # Mongo round-trips datetimes as naive UTC; normalize before the
            # aware-now comparison (same discipline as the repository tests).
            if getattr(expires_at, "tzinfo", None) is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                raise _admission_guard_failure("Batch has expired")

        already_submitted, item = _check_admission_guards(batch, item_id)
        if already_submitted is not None:
            return already_submitted

        problem = _build_variation_problem_document(
            batch, item, user_id, audit_image, tags, now
        )
        await database["problems"].insert_one(problem, session=session)
        recorded = await record_variant_item_submission(
            database,
            batch_id,
            user_id,
            item_id,
            problem_id=problem["_id"],
            now=now,
            session=session,
        )
        if not recorded:
            # The transaction snapshot already proved the item was READY, so
            # this can only mean an unexpected conflict; abort and let the
            # transaction retry re-read fresh state.
            raise _admission_guard_failure("Item changed during admission")
        await enqueue_solution_generation_task_for_problem(
            database, problem, now=now, session=session
        )
        return {"problemId": str(problem["_id"]), "alreadySubmitted": False}

    async with adapter.start_session() as session:
        return await session.with_transaction(_transaction)


async def register_admitted_problem_tags(
    database: Any,
    user_id: Any,
    tags: list[str],
) -> None:
    """Best-effort post-commit tag registration.

    A tag-registration error must never delete or invalidate the already
    committed Problem, so every failure is swallowed after logging.
    """
    try:
        await _register_tags(database, user_id, tags)
    except Exception:
        logger.warning(
            "Post-commit tag registration failed for user %s; problem kept", user_id
        )
