"""In-process generation executor for problem variant sessions (issue #685).

Per Q7 this is deliberately lighter than the batch pipeline's lease worker:
a single user babysits a single problem, so a per-session ``asyncio.Task``
with revision fencing replaces claim/lease machinery. A Generate override or
discard cancels the task handle directly; even without cancellation, a
superseded task's writes are dropped by the repository's revision fencing.

# ponytail: in-process task registry, no claim/lease/durability — if process
# deaths mid-generation become frequent, swap this executor for the batch
# claim/lease worker shape; the session shape and ``generate_and_validate``
# are unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.infrastructure.problem_variants.repository import (
    claim_problem_variant_generation,
    find_problem_variant_session,
    save_problem_variant_checkpoint,
    save_problem_variant_result,
)
from app.infrastructure.vlm.variant_client import (
    VariantCandidate,
    generate_and_validate,
)
from app.problem_variation import (
    VariationStatus,
    canonical_variation_mode,
    problem_content_from_snapshot,
    serialize_generation_result,
)

logger = logging.getLogger(__name__)

# str(session_id) → running task handle for the current generation attempt.
_tasks: dict[str, asyncio.Task[Any]] = {}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _failed_validation_evidence(clients_identity: str, exc: Exception) -> dict[str, Any]:
    """Structured failure evidence for an escaped execution error.

    Mirrors ``variation_worker._failed_validation_evidence``: VLM errors
    carry a code, raw provider errors reuse the generic "provider" kind so a
    stored candidate still routes to needs-validation (#677).
    """
    kind = getattr(exc, "code", "provider")
    return {
        "verdict": "fail",
        "failures": [
            {
                "kind": kind,
                "evidence": f"{clients_identity} failed: {exc}",
            }
        ],
        "reports": [],
    }


def cancel_problem_variant_task(session_id: Any) -> None:
    """Cancel the in-flight generation task for one session, if any."""
    task = _tasks.pop(str(session_id), None)
    if task is not None and not task.done():
        task.cancel()


async def _run_session_generation(
    database: Any,
    settings: Any,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    clients: tuple[Any, Any, Any, Any] | None,
) -> None:
    """Run one fenced generation/validation attempt for a queued session."""
    generator, validator, validator2, helper = clients
    validators = [validator] + ([validator2] if validator2 is not None else [])
    session = await find_problem_variant_session(
        database, user_id, problem_id, session_id
    )
    if session is None or session.get("discardedAt") is not None:
        return
    variation = session.get("variation") or {}
    if variation.get("status") != VariationStatus.QUEUED.value:
        return
    claimed_revision = session.get("contentRevision")
    if not isinstance(claimed_revision, int):
        return
    claimed = await claim_problem_variant_generation(
        database,
        user_id,
        problem_id,
        session_id,
        claimed_revision=claimed_revision,
        now=_utc_now(),
    )
    if claimed is None:
        return
    # Execute from the claimed document: the claim is authoritative, and a
    # concurrent override between the read above and the claim would have
    # left the pre-claim snapshot stale.
    claimed_variation = claimed.get("variation") or {}
    snapshot = claimed_variation.get("original")
    if not snapshot:
        return
    mode = canonical_variation_mode(claimed.get("mode")).value
    source = problem_content_from_snapshot(snapshot)

    stored_candidate = claimed_variation.get("candidate")
    if isinstance(stored_candidate, dict):
        # Validator-only revalidation: the revalidate endpoint keeps the
        # stored, possibly user-edited candidate, so validate it instead of
        # regenerating (mirrors variation_worker's persisted-candidate
        # branch). Generate-override always clears the candidate, so a
        # present candidate unambiguously means "validate what is stored".
        candidate_dict = stored_candidate
    else:
        identity = (
            f"generator {generator.identity['provider']}/{generator.identity['model']}"
        )

        # Step 1: generate and checkpoint before any validator call.
        try:
            candidate = await generator.generate_candidate(mode=mode, source=source)
        except Exception as exc:
            await save_problem_variant_result(
                database,
                user_id,
                problem_id,
                session_id,
                claimed_revision=claimed_revision,
                verdict="fail",
                validation=_failed_validation_evidence(identity, exc),
                candidate_present=False,
                now=_utc_now(),
            )
            return
        candidate_dict = candidate.model_dump(by_alias=True)
        checkpointed = await save_problem_variant_checkpoint(
            database,
            user_id,
            problem_id,
            session_id,
            claimed_revision=claimed_revision,
            candidate=candidate_dict,
            now=_utc_now(),
        )
        if not checkpointed:
            logger.info(
                "Discarding problem variant candidate for %s: session superseded",
                session_id,
            )
            return

    # Step 2: validate the checkpointed candidate.
    try:
        candidate_model = VariantCandidate.model_validate(candidate_dict)
    except Exception as exc:
        await save_problem_variant_result(
            database,
            user_id,
            problem_id,
            session_id,
            claimed_revision=claimed_revision,
            verdict="fail",
            validation={
                "verdict": "fail",
                "failures": [
                    {
                        "kind": "invalid-candidate",
                        "evidence": (
                            f"Persisted candidate failed schema validation: {exc}"
                        ),
                    }
                ],
                "reports": [],
            },
            candidate_present=True,
            now=_utc_now(),
        )
        return
    try:
        result = await generate_and_validate(
            mode=mode,
            source=source,
            generator=generator,
            validators=validators,
            helper=helper,
            candidate=candidate_model,
        )
    except Exception as exc:
        await save_problem_variant_result(
            database,
            user_id,
            problem_id,
            session_id,
            claimed_revision=claimed_revision,
            verdict="fail",
            validation=_failed_validation_evidence("variation validation", exc),
            candidate_present=True,
            now=_utc_now(),
        )
        return
    await save_problem_variant_result(
        database,
        user_id,
        problem_id,
        session_id,
        claimed_revision=claimed_revision,
        verdict=result.assessment.verdict,
        validation=serialize_generation_result(result),
        candidate_present=True,
        now=_utc_now(),
    )


async def _run_session_generation_guarded(*args: Any, **kwargs: Any) -> None:
    """Task body: a crashed attempt must not take the process down.

    A hard crash leaves the session in-flight; the Generate override
    self-heals it (new revision supersedes the dead attempt).
    """
    try:
        await _run_session_generation(*args, **kwargs)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Problem variant generation task failed")


async def start_problem_variant_generation(
    database: Any,
    settings: Any,
    *,
    user_id: Any,
    problem_id: str,
    session_id: Any,
    clients: tuple[Any, Any, Any, Any] | None = None,
) -> None:
    """(Re)start the generation task for one session."""
    cancel_problem_variant_task(session_id)
    key = str(session_id)
    task = asyncio.create_task(
        _run_session_generation_guarded(
            database,
            settings,
            user_id,
            problem_id,
            session_id,
            clients,
        )
    )
    _tasks[key] = task

    def _forget(done: asyncio.Task[Any], key: str = key) -> None:
        # Completed attempts leave the registry unless a newer attempt
        # already replaced the entry (regenerate/discard pop their own).
        if _tasks.get(key) is done:
            _tasks.pop(key, None)

    task.add_done_callback(_forget)
