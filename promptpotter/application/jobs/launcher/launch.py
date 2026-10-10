from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, assert_never

from promptpotter.application.jobs.launcher.admission import refuse_as_busy, request_launch
from promptpotter.application.jobs.launcher.checkin import CheckinStart, hold_checkin_start
from promptpotter.application.jobs.launcher.mint_and_start import (
    ExistingCycle,
    FreshMint,
    hold_existing_cycle,
    hold_fresh_mint,
)
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.registry import Job, JobRegistry
from promptpotter.application.runner.entry import RunMode
from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

# Detached queued launches, held so the event loop cannot collect one mid-wait.
_DETACHED: set[asyncio.Task[None]] = set()


type LaunchRequest = FreshMint | ExistingCycle | CheckinStart


@dataclass(frozen=True, slots=True)
class Inline:
    no_wait: bool = False


@dataclass(frozen=True, slots=True)
class Launched:
    """``held`` is ``None`` where the run went — or, queued, will go — to its own process."""

    job: Job
    held: HeldRun | None


async def _hold(
    request: LaunchRequest, *, stores: Stores, job_registry: JobRegistry, job: Job
) -> HeldRun:
    match request:
        case FreshMint():
            return await hold_fresh_mint(request, stores=stores, job_registry=job_registry, job=job)
        case ExistingCycle():
            return await hold_existing_cycle(
                request, stores=stores, job_registry=job_registry, job=job
            )
        case CheckinStart():
            return await hold_checkin_start(
                request, stores=stores, job_registry=job_registry, job=job
            )
        case _:
            assert_never(request)


async def launch(
    request: LaunchRequest,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    mode: RunMode,
    inline: Inline | None,
) -> Launched:
    """Call AFTER everything a person types: a slot held across an interactive check-in is lost."""
    job = request_launch(
        stores=stores,
        job_registry=job_registry,
        dataset_name=request.dataset_name,
        hop=request.hop,
    )
    if inline is not None:
        if job.queued:
            if inline.no_wait:
                refuse_as_busy(stores, job_registry, job)
            holder = job_registry.holder()
            order = job_registry.queue_order()
            logger.warning(
                "Machine full — queued at position %d (oldest run: %s). It starts by itself; "
                "Ctrl+C to leave the queue.",
                next((i for i, q in enumerate(order, 1) if q.job_id == job.job_id), 1),
                holder.campaign_id if holder else "?",
            )
        held = await _hold(request, stores=stores, job_registry=job_registry, job=job)
        return Launched(job=job, held=held)

    async def hold_and_detach() -> None:
        held = await _hold(request, stores=stores, job_registry=job_registry, job=job)
        held.detach(mode=mode)

    if job.queued:
        _detach(hold_and_detach(), what=f"queued launch {job.job_id} ({request.dataset_name})")
    else:
        await hold_and_detach()
    return Launched(job=job, held=None)


def _detach(coro: Coroutine[Any, Any, None], *, what: str) -> None:
    task = asyncio.ensure_future(coro)
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)
    # Nobody awaits it, so the result is read here: an unread exception warns at shutdown.
    task.add_done_callback(lambda t: _report_detached(t, what))


def _report_detached(task: asyncio.Task[None], what: str) -> None:
    if task.cancelled():
        logger.info("%s was cancelled", what)
        return
    exc = task.exception()
    if exc is not None:
        logger.error("%s failed: %s", what, exc, exc_info=exc)


__all__ = [
    "Inline",
    "LaunchRequest",
    "Launched",
    "launch",
]
