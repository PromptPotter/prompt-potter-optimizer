from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from promptpotter.application.jobs.launcher.admission import (
    RunStart,
    admit_and_hold,
    refuse_as_busy,
    request_jobs,
)
from promptpotter.application.jobs.launcher.checkin import CheckinStart
from promptpotter.application.jobs.launcher.mint_and_start import ExistingCycle, FreshMint
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.reaper import sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import UNRESOLVED_HOP
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import LaunchedJob
from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

# Detached queued launches, held so the event loop cannot collect one mid-wait.
_DETACHED: set[asyncio.Task[object]] = set()


type LaunchRequest = FreshMint | ExistingCycle | CheckinStart


@dataclass(frozen=True, slots=True)
class Inline:
    no_wait: bool = False


class LaunchOutcome:
    """What a launch kind's act answers in process; every door serves it as a `LaunchAnswer`."""

    __slots__ = ()

    def as_launches(self) -> list[LaunchedJob]:
        """Each job it launched, as its ack records them."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Launched(LaunchOutcome):
    """What the launch answers, as its ack records it; ``held`` is ``None`` where the run goes to
    its own process."""

    launch: LaunchedJob
    held: HeldRun | None

    def as_launches(self) -> list[LaunchedJob]:
        return [self.launch]


async def hold(request: LaunchRequest, start: RunStart, mode: RunMode) -> HeldRun:
    """THE way into a held run: past the queue and the money admission, then the request's cycle
    opened. One with no hop yet mints it, so a refusal before then leaves nothing written and one
    after stamps the minted cycle; an existing cycle's is never stamped, so it stays as resumable
    as it was. A check-in's claim ends with this hold however it ends."""
    stamps: CycleHop | None = None
    closing = AsyncExitStack()
    try:
        held = await admit_and_hold(request, start)
        opened = await closing.enter_async_context(request.open(start.stores))
        if request.hop == UNRESOLVED_HOP:
            stamps = opened.hop
            await asyncio.to_thread(start.land, opened.hop, held)
            logger.info(
                "%s: minted %s/%s (job %s)",
                request.verb,
                opened.hop.campaign_id,
                opened.hop.cycle_id,
                start.job_id,
            )
        return HeldRun.of(start, opened, held, closing, mode)
    except BaseException as exc:
        await asyncio.to_thread(start.release, exc, stamps=stamps)
        await closing.aclose()
        raise
    finally:
        if isinstance(request, CheckinStart):
            request.claim.release()


@dataclass(frozen=True, slots=True)
class Admitted(LaunchOutcome):
    """A job holding its slot or its place in the queue; nothing of its cycle is written yet."""

    start: RunStart
    request: LaunchRequest
    mode: RunMode
    inline: Inline | None

    def as_launches(self) -> list[LaunchedJob]:
        return [LaunchedJob.of(self.start.job)]

    async def withdraw(self, exc: BaseException) -> None:
        """Its slot given back, where the act that admitted it raised before any run held it."""
        logger.warning("admitted launch %s withdrawn unrun: %s", self.start.job_id, exc)
        await asyncio.to_thread(self.start.release, exc)

    async def held(self) -> HeldRun:
        """Its run held: past the queue, the money admission and the cycle's session."""
        return await hold(self.request, self.start, self.mode)

    async def launched(self) -> Launched:
        return await self.hand_over(self.held())

    async def hand_over(self, holding: Coroutine[Any, Any, HeldRun]) -> Launched:
        """*holding* — `held`, or a held step that acts once the run is held (a fork's cut on its
        parent) — awaited where the run goes: inline, detached, or in the queue's own task."""
        job = self.start.job
        if self.inline is not None:
            held = await holding
            return Launched(LaunchedJob.of(job, held.session.hop), held)

        async def hold_and_detach() -> CycleHop:
            held = await holding
            await held.detach()
            return held.session.hop

        if job.queued:
            what = f"queued launch {job.job_id} ({self.request.dataset_name})"
            _detach(hold_and_detach(), what=what)
            return Launched(LaunchedJob.of(job), None)
        return Launched(LaunchedJob.of(job, await hold_and_detach()), None)


def admit(
    request: LaunchRequest,
    mode: RunMode,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    inline: Inline | None,
) -> Admitted:
    """Call AFTER everything a person types: a slot held across an interactive check-in is lost.
    Every door's launch first reclaims what a dead producer left, so the machine it counts is live;
    its sweep and its admission wait on the gate, so every async door offloads it once."""
    sweep(stores.projects_root)
    (job,) = request_jobs(
        stores=stores,
        job_registry=job_registry,
        holder=request.holder,
        hop=request.hop,
    )
    if inline is not None and job.queued:
        if inline.no_wait:
            refuse_as_busy(job_registry, job)
        oldest = next(iter(job_registry.list_running()), None)
        order = job_registry.queue_order()
        logger.warning(
            "Machine full — queued at position %d (oldest run: %s). It starts by itself; "
            "Ctrl+C to leave the queue.",
            next((i for i, q in enumerate(order, 1) if q.job_id == job.job_id), 1),
            "?" if oldest is None else oldest.what,
        )
    return Admitted(RunStart(stores, job_registry, job), request, mode, inline)


async def launch(
    request: LaunchRequest,
    mode: RunMode,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    inline: Inline | None,
) -> Launched:
    admitted = await asyncio.to_thread(
        admit, request, mode, stores=stores, job_registry=job_registry, inline=inline
    )
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
