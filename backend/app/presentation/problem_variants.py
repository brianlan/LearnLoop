"""Problem-level variant review routes (issue #685).

Create-variant sessions on the problem detail page, mirroring the batch
variant workflow semantics (`bulk_ingestion.py`): profile pre-checks before
work is created, `expectedRevision` guards on every state-changing route,
read-only source, status-independent tag saves, fail-attestation with the
real verdict, and transactional admission through the shared core in
``variant_submission.py``. Generation runs in-process
(``problem_variants.executor``) with revision fencing and task cancellation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.infrastructure.problem_variants.executor import (
    cancel_problem_variant_task,
    start_problem_variant_generation,
)
from app.infrastructure.problem_variants.repository import (
    CREATABLE_PROBLEM_VARIANT_MODES,
    PROBLEM_VARIANT_SESSIONS_COLLECTION,
    attest_problem_variant_validation,
    classify_session_conflict,
    discard_problem_variant_session,
    build_problem_variant_session_document,
    edit_problem_variant_candidate,
    find_active_problem_variant_session,
    find_problem_variant_session,
    mark_problem_variant_session_submitted,
    release_problem_variant_submit_reservation,
    request_problem_variant_generation,
    request_problem_variant_revalidation,
    reserve_problem_variant_for_submit,
    update_problem_variant_session_tags,
)
from app.infrastructure.vlm.variant_client import (
    VariantVLMError,
    build_variant_clients,
)
from app.presentation.deps import (
    AdapterDependency,
    CurrentUserDependency,
    DatabaseDependency,
    SettingsDependency,
)
from app.presentation.errors import ApiError
from app.presentation.helpers import normalize_tags, parse_object_id
from app.presentation.problem_variants_schemas import (
    ProblemVariantSessionResponse,
    ProblemVariantSessionView,
    ProblemVariantSubmitResponse,
    serialize_problem_variant_session,
)
from app.presentation.tag_registration import _register_tags
from app.presentation.variant_submission import (
    admit_variant_problem,
    register_admitted_problem_tags,
)
from app.problem_variation import (
    GenerationInProgressError,
    InvalidVariationStateError,
    RevisionMismatchError,
    VariationNotFoundError,
    VariationStatus,
)

router = APIRouter(tags=["problem-variants"])


def _raise_variant_conflict(exc: Exception) -> ApiError:
    if isinstance(exc, VariationNotFoundError):
        return ApiError(404, "NOT_FOUND", str(exc))
    if isinstance(exc, RevisionMismatchError):
        return ApiError(409, "REVISION_MISMATCH", str(exc))
    if isinstance(exc, GenerationInProgressError):
        return ApiError(409, "VARIATION_BUSY", str(exc))
    return ApiError(409, "INVALID_VARIATION_STATE", str(exc))


def _classify_variant_conflict(session: Any, expected_revision: int) -> None:
    """Classify a rejected action and translate its domain errors to API
    errors, exactly like the PATCH route — expected conflicts must surface
    as structured 409s, never 500s."""
    try:
        classify_session_conflict(session, expected_revision)
    except (
        VariationNotFoundError,
        RevisionMismatchError,
        GenerationInProgressError,
        InvalidVariationStateError,
    ) as exc:
        raise _raise_variant_conflict(exc) from exc


def _require_variant_profiles(settings: Any) -> tuple[Any, Any, Any, Any]:
    """Profile pre-check before any session/work is created (409 when
    unconfigured so unconfigured deployments never create work that can
    never run)."""
    try:
        return build_variant_clients(settings)
    except VariantVLMError as exc:
        raise ApiError(409, exc.code, str(exc)) from exc


async def _load_owned_problem(
    database: Any,
    problem_id: str,
    user_id: Any,
    *,
    require_available: bool,
) -> dict[str, Any]:
    object_id = parse_object_id(problem_id, resource_name="Problem")
    problem = await database["problems"].find_one(
        {"_id": object_id, "userId": user_id}
    )
    if problem is None:
        raise ApiError(404, "NOT_FOUND", "Problem not found")
    if require_available and (
        problem.get("isDeleted") or problem.get("isDisabled")
    ):
        raise ApiError(409, "PROBLEM_UNAVAILABLE", "Problem is not available")
    return problem


def _audit_image_for_source(problem: dict[str, Any]) -> dict[str, Any]:
    """The auditImage three-case rule (issue #685).

    A problem with its own source image freezes that image's metadata; a
    variant problem (no source image) inherits its own provenance's audit
    image — so a whole derivation chain shares one audit image; anything
    else has nothing to audit and is rejected.
    """
    source_image = problem.get("sourceImage")
    if source_image and source_image.get("bucket") and source_image.get("objectKey"):
        return dict(source_image)
    variation = problem.get("variation") or {}
    inherited = (variation.get("original") or {}).get("auditImage")
    if inherited:
        return dict(inherited)
    raise ApiError(
        409, "VARIANT_AUDIT_MISSING", "Problem has no source image to archive"
    )


async def _load_owned_session(
    database: Any,
    problem_id: str,
    session_id: str,
    user_id: Any,
) -> dict[str, Any]:
    parse_object_id(session_id, resource_name="Variant session")
    session = await find_problem_variant_session(
        database, user_id, problem_id, session_id
    )
    if session is None or session.get("discardedAt") is not None:
        raise ApiError(404, "NOT_FOUND", "Variant session not found")
    return session


class CreateProblemVariantRequest(BaseModel):
    mode: str


class ProblemVariantCandidateUpdateRequest(BaseModel):
    expectedRevision: int
    text: str | None = None
    problemType: str | None = None
    graphDsl: str | None = None
    correctAnswer: str | None = None
    tags: list[str] | None = None


class ProblemVariantRevisionRequest(BaseModel):
    expectedRevision: int


@router.post(
    "/problems/{problem_id}/variants",
    response_model=ProblemVariantSessionResponse,
    status_code=202,
)
async def create_problem_variant(
    problem_id: str,
    request: CreateProblemVariantRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
) -> ProblemVariantSessionResponse:
    """Create a variant session for the problem and start generation."""
    if request.mode not in CREATABLE_PROBLEM_VARIANT_MODES:
        raise ApiError(
            409,
            "INVALID_VARIANT_MODE",
            "Variant mode must be data-only or transfer-variant",
        )
    problem = await _load_owned_problem(
        database, problem_id, user["_id"], require_available=True
    )
    # Profile pre-check runs BEFORE the session insert: an unconfigured
    # deployment never creates a stuck session.
    clients = _require_variant_profiles(settings)
    existing = await find_active_problem_variant_session(
        database, user["_id"], problem_id
    )
    if existing is not None:
        raise ApiError(
            409,
            "VARIANT_SESSION_EXISTS",
            "A variant session already exists for this problem",
        )
    audit_image = _audit_image_for_source(problem)
    correct_answer = dict(problem.get("correctAnswer") or {})
    original = {
        "text": problem.get("text"),
        "problemType": problem.get("problemType"),
        "graphDsl": problem.get("graphDsl"),
        # The session original mirrors the item draft shape: the display
        # string; admission normalizes it into the CorrectAnswer dict.
        "correctAnswer": str(correct_answer.get("display", "")),
        "subject": problem.get("subject", "math"),
    }
    session_doc = build_problem_variant_session_document(
        problem_id=problem_id,
        user_id=user["_id"],
        mode=request.mode,
        original=original,
        tags=[],
        now=datetime.now(UTC),
    )
    try:
        await database[PROBLEM_VARIANT_SESSIONS_COLLECTION].insert_one(session_doc)
    except DuplicateKeyError as exc:
        # The partial unique index rejected a concurrent second create.
        raise ApiError(
            409,
            "VARIANT_SESSION_EXISTS",
            "A variant session already exists for this problem",
        ) from exc
    await start_problem_variant_generation(
        database,
        settings,
        user_id=user["_id"],
        problem_id=problem_id,
        session_id=session_doc["_id"],
        clients=clients,
    )
    return ProblemVariantSessionResponse(
        session=serialize_problem_variant_session(session_doc)
    )


@router.get(
    "/problems/{problem_id}/variants/active",
    response_model=ProblemVariantSessionResponse,
)
async def get_active_problem_variant(
    problem_id: str,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> ProblemVariantSessionResponse:
    session = await find_active_problem_variant_session(
        database, user["_id"], problem_id
    )
    return ProblemVariantSessionResponse(
        session=(
            serialize_problem_variant_session(session) if session is not None else None
        )
    )


@router.patch(
    "/problems/{problem_id}/variants/{session_id}/candidate",
    response_model=ProblemVariantSessionResponse,
)
async def edit_problem_variant_candidate_endpoint(
    problem_id: str,
    session_id: str,
    request: ProblemVariantCandidateUpdateRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> ProblemVariantSessionResponse:
    session = await _load_owned_session(database, problem_id, session_id, user["_id"])
    candidate_update = request.model_dump(
        exclude_unset=True, exclude={"tags", "expectedRevision"}
    )
    if not candidate_update and request.tags is None:
        raise ApiError(400, "INVALID_REQUEST", "No candidate changes provided")
    tags = normalize_tags(request.tags) if request.tags is not None else None
    try:
        if not candidate_update and tags is not None:
            # Status-independent branch: tags are shared metadata, never
            # semantic variant content — they save in every live status
            # (queued/generating/validating included) without a revision bump.
            updated = await update_problem_variant_session_tags(
                database,
                user["_id"],
                problem_id,
                session["_id"],
                tags=tags,
                now=datetime.now(UTC),
            )
            if not updated:
                classify_session_conflict(session, request.expectedRevision)
        else:
            await edit_problem_variant_candidate(
                database,
                user["_id"],
                problem_id,
                session["_id"],
                candidate_update=candidate_update,
                tags=tags,
                expected_revision=request.expectedRevision,
                now=datetime.now(UTC),
            )
    except (
        VariationNotFoundError,
        RevisionMismatchError,
        GenerationInProgressError,
        InvalidVariationStateError,
    ) as exc:
        raise _raise_variant_conflict(exc) from exc
    fresh = await find_problem_variant_session(
        database, user["_id"], problem_id, session["_id"]
    )
    return ProblemVariantSessionResponse(
        session=serialize_problem_variant_session(fresh)
    )


@router.post(
    "/problems/{problem_id}/variants/{session_id}/generate",
    response_model=ProblemVariantSessionResponse,
    status_code=202,
)
async def generate_problem_variant(
    problem_id: str,
    session_id: str,
    request: ProblemVariantRevisionRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
) -> ProblemVariantSessionResponse:
    """Generate / Retry / Regenerate. Overrides an in-flight attempt:
    the superseded task is cancelled and its fenced writes can never land
    (the revision bump self-heals a dead process's stuck session)."""
    session = await _load_owned_session(database, problem_id, session_id, user["_id"])
    clients = _require_variant_profiles(settings)
    queued = await request_problem_variant_generation(
        database,
        user["_id"],
        problem_id,
        session["_id"],
        expected_revision=request.expectedRevision,
        now=datetime.now(UTC),
    )
    if not queued:
        fresh = await find_problem_variant_session(
            database, user["_id"], problem_id, session["_id"]
        )
        _classify_variant_conflict(fresh, request.expectedRevision)
    await start_problem_variant_generation(
        database,
        settings,
        user_id=user["_id"],
        problem_id=problem_id,
        session_id=session["_id"],
        clients=clients,
    )
    fresh = await find_problem_variant_session(
        database, user["_id"], problem_id, session["_id"]
    )
    return ProblemVariantSessionResponse(
        session=serialize_problem_variant_session(fresh)
    )


@router.post(
    "/problems/{problem_id}/variants/{session_id}/revalidate",
    response_model=ProblemVariantSessionResponse,
    status_code=202,
)
async def revalidate_problem_variant(
    problem_id: str,
    session_id: str,
    request: ProblemVariantRevisionRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
    settings: SettingsDependency,
) -> ProblemVariantSessionResponse:
    session = await _load_owned_session(database, problem_id, session_id, user["_id"])
    clients = _require_variant_profiles(settings)
    queued = await request_problem_variant_revalidation(
        database,
        user["_id"],
        problem_id,
        session["_id"],
        expected_revision=request.expectedRevision,
        now=datetime.now(UTC),
    )
    if not queued:
        fresh = await find_problem_variant_session(
            database, user["_id"], problem_id, session["_id"]
        )
        _classify_variant_conflict(fresh, request.expectedRevision)
    await start_problem_variant_generation(
        database,
        settings,
        user_id=user["_id"],
        problem_id=problem_id,
        session_id=session["_id"],
        clients=clients,
    )
    fresh = await find_problem_variant_session(
        database, user["_id"], problem_id, session["_id"]
    )
    return ProblemVariantSessionResponse(
        session=serialize_problem_variant_session(fresh)
    )


@router.post(
    "/problems/{problem_id}/variants/{session_id}/attest",
    response_model=ProblemVariantSessionResponse,
)
async def attest_problem_variant(
    problem_id: str,
    session_id: str,
    request: ProblemVariantRevisionRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> ProblemVariantSessionResponse:
    """Attestation: restore READY by explicit teacher acceptance.

    Same eligibility branches as the batch attest: a stale-PASS
    needs-validation session, or a FAIL whose failures are all check- or
    answer-kind. Completes synchronously; never touches the validators.
    """
    session = await _load_owned_session(database, problem_id, session_id, user["_id"])
    attested = await attest_problem_variant_validation(
        database,
        user["_id"],
        problem_id,
        session["_id"],
        expected_revision=request.expectedRevision,
        now=datetime.now(UTC),
    )
    if not attested:
        fresh = await find_problem_variant_session(
            database, user["_id"], problem_id, session["_id"]
        )
        _classify_variant_conflict(fresh, request.expectedRevision)
    fresh = await find_problem_variant_session(
        database, user["_id"], problem_id, session["_id"]
    )
    return ProblemVariantSessionResponse(
        session=serialize_problem_variant_session(fresh)
    )


@router.post(
    "/problems/{problem_id}/variants/{session_id}/submit",
    response_model=ProblemVariantSubmitResponse,
)
async def submit_problem_variant(
    problem_id: str,
    session_id: str,
    request: ProblemVariantRevisionRequest,
    database: DatabaseDependency,
    adapter: AdapterDependency,
    user: CurrentUserDependency,
) -> ProblemVariantSubmitResponse:
    """Admit the accepted candidate as a NEW problem inside a transaction.

    The source problem is never modified; the session records the admitted
    problem id. The admission only lands at the caller's reviewed revision.
    Idempotency is an explicit 409: the client navigates to the admitted
    problem instead of creating a second one.
    """
    session_object_id = parse_object_id(session_id, resource_name="Variant session")
    now = datetime.now(UTC)

    # Submit-reserve: atomically claim the ready session so a concurrent
    # Generate cannot clear the candidate mid-admission (#685 contract).
    token = await reserve_problem_variant_for_submit(
        database, user["_id"], problem_id, session_object_id, now=now
    )
    if token is None:
        # Not reservable: classify against fresh state (404 / stale revision /
        # already submitted / not ready).
        fresh = await find_problem_variant_session(
            database, user["_id"], problem_id, session_object_id
        )
        if fresh is not None and fresh.get("submit"):
            raise ApiError(
                409,
                "VARIANT_ALREADY_SUBMITTED",
                "This variant session already admitted a problem",
            )
        _classify_variant_conflict(fresh, request.expectedRevision)
        raise ApiError(
            409, "VARIANT_INVALIDATED", "Variant is not ready for submission"
        )

    async def _transaction(txn_session: Any) -> dict[str, Any]:
        session = await database[PROBLEM_VARIANT_SESSIONS_COLLECTION].find_one(
            {
                "_id": session_object_id,
                "problemId": problem_id,
                "userId": user["_id"],
            },
            session=txn_session,
        )
        if session is None or session.get("discardedAt") is not None:
            raise ApiError(404, "NOT_FOUND", "Variant session not found")
        if session.get("submit"):
            raise ApiError(
                409,
                "VARIANT_ALREADY_SUBMITTED",
                "This variant session already admitted a problem",
            )
        if session.get("contentRevision") != request.expectedRevision:
            # Reject stale reviews before any admission work.
            raise ApiError(
                409,
                "REVISION_MISMATCH",
                f"expectedRevision {request.expectedRevision} does not match "
                f"session contentRevision {session.get('contentRevision')}",
            )
        reservation = (session.get("variation") or {}).get("submitReservation") or {}
        if (
            reservation.get("token") != token
            or reservation.get("expiresAt") is None
            or reservation.get("expiresAt") <= now
        ):
            raise ApiError(
                409, "VARIATION_BUSY", "Submit reservation is no longer held"
            )
        variation = session.get("variation") or {}
        validation = variation.get("validation") or {}
        candidate = variation.get("candidate") or {}
        attestation = variation.get("attestation") or {}
        attestation_ok = attestation.get("revision") == session.get(
            "contentRevision"
        )
        if variation.get("status") != VariationStatus.READY.value:
            raise ApiError(
                409, "VARIANT_INVALIDATED", "Variant is not validated"
            )
        if validation.get("verdict") != "pass" and not attestation_ok:
            raise ApiError(
                409, "VARIANT_INVALIDATED", "Variant validation did not pass"
            )
        if not candidate:
            raise ApiError(409, "VARIANT_INVALIDATED", "Variant candidate is missing")
        if (
            variation.get("validatedRevision") != session.get("contentRevision")
            and not attestation_ok
        ):
            raise ApiError(
                409,
                "VARIANT_INVALIDATED",
                "Validated variant is not the current version",
            )
        source = await database["problems"].find_one(
            {"_id": parse_object_id(problem_id, resource_name="Problem"),
             "userId": user["_id"]},
            session=txn_session,
        )
        if source is None or source.get("isDeleted"):
            raise ApiError(
                409, "VARIANT_SOURCE_GONE", "Source problem is no longer available"
            )
        audit_image = _audit_image_for_source(source)

        async def _record_submission(admitted_id: Any, record_session: Any) -> bool:
            return await mark_problem_variant_session_submitted(
                database,
                user["_id"],
                problem_id,
                session["_id"],
                admitted_problem_id=admitted_id,
                expected_revision=request.expectedRevision,
                now=now,
                session=record_session,
                submit_token=token,
            )

        return await admit_variant_problem(
            database,
            user["_id"],
            mode=session.get("mode") or "data-only",
            original=variation.get("original") or {},
            accepted_variant=candidate,
            generator=candidate.get("generator") or {},
            generation_count=variation.get("generationCount") or 0,
            validation=validation,
            tags=list(session.get("tags") or []),
            origin={},
            audit_image=audit_image,
            source_problem_id=str(source["_id"]),
            attested_by_user=attestation_ok,
            submit_recorder=_record_submission,
            now=now,
            session=txn_session,
        )

    try:
        async with adapter.start_session() as mongo_session:
            result = await mongo_session.with_transaction(_transaction)
    except Exception:
        # Release the reservation so Generate is not blocked for the timeout
        # window after a failed submit. Best-effort: the reservation also
        # expires on its own.
        await release_problem_variant_submit_reservation(
            database, user["_id"], problem_id, session_object_id,
            token=token, now=datetime.now(UTC),
        )
        raise
    # Post-commit, best-effort: a tag failure never invalidates the admitted
    # problem (ingest parity).
    session = await find_problem_variant_session(
        database, user["_id"], problem_id, session_id
    )
    if session is not None:
        await register_admitted_problem_tags(
            database, user["_id"], list(session.get("tags") or [])
        )
    return ProblemVariantSubmitResponse(**result)


@router.post(
    "/problems/{problem_id}/variants/{session_id}/discard",
    response_model=ProblemVariantSessionResponse,
)
async def discard_problem_variant(
    problem_id: str,
    session_id: str,
    request: ProblemVariantRevisionRequest,
    database: DatabaseDependency,
    user: CurrentUserDependency,
) -> ProblemVariantSessionResponse:
    """Terminal discard at the caller's reviewed revision (session mode is
    immutable at creation, so a wrong mode or abandoned candidate is
    otherwise unrecoverable). Cancels any in-flight task handle."""
    session = await _load_owned_session(database, problem_id, session_id, user["_id"])
    if session.get("contentRevision") != request.expectedRevision:
        # Reject stale requests before cancelling anything.
        raise ApiError(
            409,
            "REVISION_MISMATCH",
            f"expectedRevision {request.expectedRevision} does not match "
            f"session contentRevision {session.get('contentRevision')}",
        )
    discarded = await discard_problem_variant_session(
        database,
        user["_id"],
        problem_id,
        session["_id"],
        expected_revision=request.expectedRevision,
        now=datetime.now(UTC),
    )
    if discarded:
        cancel_problem_variant_task(session["_id"])
    fresh = await find_problem_variant_session(
        database, user["_id"], problem_id, session["_id"]
    )
    if not discarded and fresh is not None and not fresh.get("discardedAt"):
        # The write matched nothing. Distinguish a submit that admitted a
        # problem from a revision bump (Generate or a semantic PATCH) that
        # won the read-to-write race — the caller must retry against the
        # newer revision, not hear "already admitted".
        if fresh.get("submit"):
            raise ApiError(
                409,
                "VARIANT_ALREADY_SUBMITTED",
                "This variant session already admitted a problem",
            )
        raise ApiError(
            409,
            "REVISION_MISMATCH",
            f"expectedRevision {request.expectedRevision} does not match "
            f"session contentRevision {fresh.get('contentRevision')}",
        )
    return ProblemVariantSessionResponse(
        session=serialize_problem_variant_session(fresh)
    )
