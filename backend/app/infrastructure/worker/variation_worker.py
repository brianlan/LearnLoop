"""Durable bounded worker for per-item variant generation and validation.

Mirrors the extraction worker's scheduling shape (poll → claim → bounded
concurrent tasks) but claims items in variant ingestion batches that have
queued or lease-expired in-flight variation work. The candidate checkpoint
(``save_variation_candidate_checkpoint``) snapshots the generated candidate
before any validator call, so a crash or reclaim resumes validation from the
persisted candidate instead of regenerating.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.domain.ingestion import BatchState
from app.infrastructure.config.settings import Settings
from app.infrastructure.ingestion.repository import (
    INGESTION_BATCHES_COLLECTION,
    claim_variation_work,
    renew_variation_lease,
    save_variation_candidate_checkpoint,
    save_variation_result,
)
from app.infrastructure.vlm.base_client import BaseVLMError
from app.infrastructure.vlm.variant_client import (
    VariantCandidate,
    VariantGenerationResult,
    generate_and_validate,
)
from app.problem_variation import (
    IngestionMode,
    VariationStatus,
    problem_content_from_snapshot,
    serialize_generation_result,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _run_with_lease_heartbeat(
    call: Callable[[], Any],
    *,
    database: Any,
    batch_id: Any,
    user_id: Any,
    item_id: str,
    token: str,
    lease_timeout_seconds: int,
) -> Any:
    """Run one model call while a heartbeat keeps the variation lease alive.

    Returns the call's result, or None when a heartbeat renewal lost the
    claim (another worker reclaimed the item): the caller must discard the
    stale result instead of racing a fenced write it cannot win.
    """
    lost = asyncio.Event()

    async def heartbeat() -> None:
        # Renew a third of the lease before expiry; the 1s floor keeps tiny
        # test leases from spinning.
        interval = max(lease_timeout_seconds / 3, 1)
        while True:
            await asyncio.sleep(interval)
            renewed = await renew_variation_lease(
                database,
                batch_id,
                user_id,
                item_id,
                token=token,
                lease_timeout_seconds=lease_timeout_seconds,
                now=_utc_now(),
            )
            if not renewed:
                lost.set()
                return

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        result = await call()
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
    if lost.is_set():
        return None
    return result


def _failed_validation_evidence(
    clients_identity: str, exc: BaseVLMError
) -> dict[str, Any]:
    return {
        "verdict": "fail",
        "failures": [
            {
                "kind": exc.code,
                "evidence": f"{clients_identity} failed: {exc}",
            }
        ],
        "reports": [],
    }


def _is_variation_claimable(item: dict[str, Any], now: datetime) -> bool:
    variation = item.get("variation") or {}
    status = variation.get("status")
    if status == VariationStatus.QUEUED.value:
        return True
    if status in (
        VariationStatus.GENERATING.value,
        VariationStatus.VALIDATING.value,
    ):
        lease_until = variation.get("leaseUntil")
        if lease_until is None:
            return True
        if isinstance(lease_until, datetime):
            if lease_until.tzinfo is None:
                lease_until = lease_until.replace(tzinfo=UTC)
            return lease_until <= now
    return False


async def claim_next_variation_work(
    database: Any,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Find and atomically claim the next item with pending variation work."""
    current = now or _utc_now()
    cursor = database[INGESTION_BATCHES_COLLECTION].find(
        {
            "status": BatchState.ACTIVE.value,
            "expiresAt": {"$gt": current},
            "ingestionMode": {
                "$in": [
                    IngestionMode.DATA_ONLY.value,
                    IngestionMode.DATA_AND_WORDING.value,
                ]
            },
            "items.variation.status": {
                "$in": [
                    VariationStatus.QUEUED.value,
                    VariationStatus.GENERATING.value,
                    VariationStatus.VALIDATING.value,
                ]
            },
        }
    )
    batches = await cursor.to_list(length=None)
    for batch in batches:
        batch_user_id = batch.get("userId")
        for item in batch.get("items", []):
            if not _is_variation_claimable(item, current):
                continue
            claimed = await claim_variation_work(
                database,
                batch["_id"],
                batch_user_id,
                item["itemId"],
                lease_timeout_seconds=settings.variation_lease_timeout_seconds,
                now=current,
            )
            if claimed is not None:
                return batch, claimed
    return None


async def process_variation(
    item: dict[str, Any],
    batch: dict[str, Any],
    database: Any,
    generator: Any,
    validators: list[Any],
    helper: Any,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> None:
    """Run (or resume) one claimed variation attempt to a fenced verdict."""
    batch_id = batch["_id"]
    user_id = batch["userId"]
    item_id = item["itemId"]
    variation = item.get("variation") or {}
    token = variation.get("claimToken")
    claimed_revision = item.get("contentRevision")
    mode = batch.get("ingestionMode")

    if not token or not isinstance(claimed_revision, int):
        logger.info("Discarding variation work for %s: missing claim fencing", item_id)
        return
    snapshot = variation.get("original")
    if not snapshot:
        logger.info("Discarding variation work for %s: no source snapshot", item_id)
        return

    source = problem_content_from_snapshot(snapshot)
    status = variation.get("status")
    candidate_dict: dict[str, Any] | None = variation.get("candidate")

    if status == VariationStatus.GENERATING.value:
        try:
            candidate = await _run_with_lease_heartbeat(
                lambda: generator.generate_candidate(mode=mode, source=source),
                database=database,
                batch_id=batch_id,
                user_id=user_id,
                item_id=item_id,
                token=token,
                lease_timeout_seconds=settings.variation_lease_timeout_seconds,
            )
        except BaseVLMError as exc:
            saved = await save_variation_result(
                database,
                batch_id,
                user_id,
                item_id,
                token=token,
                claimed_revision=claimed_revision,
                verdict="fail",
                validation=_failed_validation_evidence(
                    f"generator {generator.identity['provider']}/{generator.identity['model']}",
                    exc,
                ),
                now=_utc_now(),
            )
            if not saved:
                logger.info(
                    "Discarding variation failure for %s: claim no longer owned",
                    item_id,
                )
            return
        if candidate is None:
            logger.info(
                "Discarding variation generation for %s: lease lost mid-call", item_id
            )
            return
        # Candidate checkpoint before any validator call: the attempt resumes
        # validation from this snapshot after a crash or reclaim.
        candidate_dict = candidate.model_dump(by_alias=True)
        checkpointed = await save_variation_candidate_checkpoint(
            database,
            batch_id,
            user_id,
            item_id,
            token=token,
            claimed_revision=claimed_revision,
            candidate=candidate_dict,
            now=_utc_now(),
        )
        if not checkpointed:
            logger.info(
                "Discarding variation candidate for %s: claim no longer owned",
                item_id,
            )
            return
        status = VariationStatus.VALIDATING.value

    if status != VariationStatus.VALIDATING.value:
        logger.info(
            "Discarding variation work for %s: unexpected claim status %s",
            item_id,
            status,
        )
        return

    # Long validation phase: renew the lease before starting model calls.
    renewed = await renew_variation_lease(
        database,
        batch_id,
        user_id,
        item_id,
        token=token,
        lease_timeout_seconds=settings.variation_lease_timeout_seconds,
        now=_utc_now(),
    )
    if not renewed:
        logger.info("Discarding variation work for %s: lease renewal lost", item_id)
        return

    # A persisted candidate edited into a malformed shape (or written by an
    # older schema) must fail closed with evidence, never crash the task and
    # leave the item in-flight forever.
    try:
        candidate = VariantCandidate.model_validate(candidate_dict)
    except ValidationError as exc:
        saved = await save_variation_result(
            database,
            batch_id,
            user_id,
            item_id,
            token=token,
            claimed_revision=claimed_revision,
            verdict="fail",
            validation={
                "verdict": "fail",
                "failures": [
                    {
                        "kind": "invalid-candidate",
                        "evidence": f"Persisted candidate failed schema validation: {exc}",
                    }
                ],
                "reports": [],
            },
            now=_utc_now(),
        )
        if not saved:
            logger.info(
                "Discarding variation failure for %s: claim no longer owned",
                item_id,
            )
        return

    # Long validation phase: renew the lease before starting model calls.
    renewed = await renew_variation_lease(
        database,
        batch_id,
        user_id,
        item_id,
        token=token,
        lease_timeout_seconds=settings.variation_lease_timeout_seconds,
        now=_utc_now(),
    )
    if not renewed:
        logger.info("Discarding variation work for %s: lease renewal lost", item_id)
        return

    try:
        result: VariantGenerationResult | None = await _run_with_lease_heartbeat(
            lambda: generate_and_validate(
                mode=mode,
                source=source,
                generator=generator,
                validators=validators,
                helper=helper,
                candidate=candidate,
            ),
            database=database,
            batch_id=batch_id,
            user_id=user_id,
            item_id=item_id,
            token=token,
            lease_timeout_seconds=settings.variation_lease_timeout_seconds,
        )
    except BaseVLMError as exc:
        # Provider/transport failure during an orchestration step that does
        # not already fail closed: record structured evidence.
        saved = await save_variation_result(
            database,
            batch_id,
            user_id,
            item_id,
            token=token,
            claimed_revision=claimed_revision,
            verdict="fail",
            validation=_failed_validation_evidence("variation validation", exc),
            now=_utc_now(),
        )
        if not saved:
            logger.info(
                "Discarding variation failure for %s: claim no longer owned",
                item_id,
            )
        return
    if result is None:
        logger.info(
            "Discarding variation validation for %s: lease lost mid-call", item_id
        )
        return
    verdict = result.assessment.verdict
    saved = await save_variation_result(
        database,
        batch_id,
        user_id,
        item_id,
        token=token,
        claimed_revision=claimed_revision,
        verdict=verdict,
        validation=serialize_generation_result(result),
        now=_utc_now(),
    )
    if not saved:
        logger.info(
            "Discarding variation result for %s: claim no longer owned "
            "(invalidated, deleted, expired or superseded)",
            item_id,
        )


async def run_variation_worker(
    database: Any,
    settings: Settings,
    generator: Any,
    validators: list[Any],
    helper: Any,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Loop claiming and processing variation work up to configured concurrency."""
    poll_interval = settings.variation_worker_poll_interval_seconds

    logger.info("Variation worker started")

    while True:
        if stop_event and stop_event.is_set():
            break

        tasks: list[asyncio.Task[Any]] = []
        for _ in range(settings.variation_worker_concurrency):
            claimed = await claim_next_variation_work(database, settings, now=_utc_now())
            if claimed is None:
                break
            batch, item = claimed
            task = asyncio.create_task(
                process_variation(
                    item,
                    batch,
                    database,
                    generator,
                    validators,
                    helper,
                    settings,
                )
            )
            tasks.append(task)

        if not tasks:
            if stop_event:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                except asyncio.TimeoutError:
                    continue
                break
            await asyncio.sleep(poll_interval)
            continue

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.exception("Variation task failed")
