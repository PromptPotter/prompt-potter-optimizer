from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, assert_never

from promptpotter.application.jobs.launcher.admission import refuse_as_busy, request_job
from promptpotter.application.jobs.launcher.checkin import CheckinStart, hold_checkin_start
from promptpotter.application.jobs.launcher.mint_and_start import (
    ExistingCycle,
    FreshMint,
    hold_existing_cycle,
    hold_fresh_mint,
)
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.registry import CampaignLaunch, Job, JobRegistry
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.run_mode import RunMode
from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

# Detached queued launches, held so the event loop cannot collect one mid-wait.
_DETACHED: set[asyncio.Task[object]] = set()


type LaunchRequest = FreshMint | ExistingCycle | CheckinStart


@dataclass(frozen=True, slots=True)
class Inline:
    no_wait: bool = False


@dataclass(frozen=True, slots=True)
class Launched:
    """``held`` is ``None`` where the run goes to its own process; ``hop`` is ``None`` while queued."""

    job: Job
    held: HeldRun | None
    hop: CycleHop | None


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


@dataclass(frozen=True, slots=True)
class Admitted:
    """A job holding its slot or its place in the queue; nothing of its cycle is written yet."""

    job: Job
    request: LaunchRequest
    mode: RunMode
    stores: Stores
    job_registry: JobRegistry
    inline: Inline | None

    async def launched(self) -> Launched:
        request, job, stores, job_registry = self.request, self.job, self.stores, self.job_registry
        if self.inline is not None:
            held = await _hold(request, stores=stores, job_registry=job_registry, job=job)
            return Launched(job=job, held=held, hop=held.session.hop)

        async def hold_and_detach() -> CycleHop:
            held = await _hold(request, stores=stores, job_registry=job_registry, job=job)
            hop = held.session.hop
            held.detach(mode=self.mode)
            return hop

        if job.queued:
            _detach(hold_and_detach(), what=f"queued launch {job.job_id} ({request.dataset_name})")
            return Launched(job=job, held=None, hop=None)
        return Launched(job=job, held=None, hop=await hold_and_detach())


def admit(
    request: LaunchRequest,
    mode: RunMode,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    inline: Inline | None,
) -> Admitted:
    """Call AFTER everything a person types: a slot held across an interactive check-in is lost."""
    job = request_job(
        stores=stores,
        job_registry=job_registry,
        holder=CampaignLaunch(request.dataset_name, draft_id=request.draft_id),
        hop=request.hop,
    )
    if inline is not None and job.queued:
        if inline.no_wait:
            refuse_as_busy(stores, job_registry, job)
        oldest = next(iter(job_registry.list_running()), None)
        order = job_registry.queue_order()
        logger.warning(
            "Machine full — queued at position %d (oldest run: %s). It starts by itself; "
            "Ctrl+C to leave the queue.",
            next((i for i, q in enumerate(order, 1) if q.job_id == job.job_id), 1),
            "?" if oldest is None else oldest.what,
        )
    return Admitted(job, request, mode, stores, job_registry, inline)


async def launch(
    request: LaunchRequest,
    mode: RunMode,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    inline: Inline | None,
) -> Launched:
    admitted = admit(request, mode, stores=stores, job_registry=job_registry, inline=inline)
    return await admitted.launched()


def _detach(coro: Coroutine[Any, Any, object], *, what: str) -> None:
    task = asyncio.ensure_future(coro)
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)
    # Nobody awaits it, so the result is read here: an unread exception warns at shutdown.
    task.add_done_callback(lambda t: _report_detached(t, what))


def _report_detached(task: asyncio.Task[object], what: str) -> None:
    if task.cancelled():
        logger.info("%s was cancelled", what)
        return
    exc = task.exception()
    if exc is not None:
        logger.error("%s failed: %s", what, exc, exc_info=exc)
