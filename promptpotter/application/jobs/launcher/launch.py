from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, assert_never

from promptpotter.application.jobs.launcher.admission import (
    admit_and_hold,
    refuse_as_busy,
    request_jobs,
    slotless_opening,
)
from promptpotter.application.jobs.launcher.checkin import CheckinStart
from promptpotter.application.jobs.launcher.mint_and_start import LaunchRequest
from promptpotter.application.jobs.launcher.opening import Opening
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.reaper import sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import UNRESOLVED_HOP, CampaignLaunch, EmbeddedRun, InnerRun
from promptpotter.domain.records.run_records import LaunchedJob
from promptpotter.domain.run_mode import RunMode
from promptpotter.infrastructure.store.stores import Stores


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
    """What the launch answers, as its ack records it; ``held`` is the inline run's hold, answered
    before its queue wait, and ``None`` where the run goes to its own process."""

    launch: LaunchedJob
    held: asyncio.Future[HeldRun] | None

    def as_launches(self) -> list[LaunchedJob]:
        return [self.launch]


async def hold(request: LaunchRequest, opening: Opening, mode: RunMode) -> HeldRun:
    """THE way into a held run: past the queue and the money admission, then the request's cycle
    opened, its session and the opening's exit riding the held run's ``closing``. One with no hop
    yet mints it, so a refusal before then leaves nothing written and one after stamps the minted
    cycle; an existing cycle's is never stamped, so it stays as resumable as it was. A check-in's
    claim ends with this hold however it ends."""
    try:
        async with AsyncExitStack() as closing:
            closing.push_async_exit(opening)
            held = await admit_and_hold(request, opening)
            opened = await closing.enter_async_context(request.open(opening.stores))
            if request.at is None:
                await asyncio.to_thread(opening.land, opened.hop, held)
            return HeldRun.of(opening, opened, held, closing.pop_all(), mode)
    finally:
        if isinstance(request, CheckinStart):
            request.claim.release()


@dataclass(frozen=True, slots=True)
class Admitted(LaunchOutcome):
    """A job holding its slot or its place in the queue; nothing of its cycle is written yet."""

    opening: Opening
    request: LaunchRequest
    mode: RunMode
    inline: Inline | None

    def as_launches(self) -> list[LaunchedJob]:
        return [LaunchedJob.of(self.opening.job)]

    async def withdraw(self, exc: BaseException) -> None:
        """Its slot given back, where the act that admitted it raised before any run held it."""
        await self.opening.abandon(exc)

    async def held(self) -> HeldRun:
        """Its run held: past the queue, the money admission and the cycle's session."""
        return await hold(self.request, self.opening, self.mode)

    async def hand_over(self, holding: Coroutine[Any, Any, HeldRun]) -> Launched:
        """*holding* — `held`, or a held step that acts once the run is held (a fork's cut on its
        parent) — awaited where the run goes: inline, detached, or in the queue's own task."""
        job = self.opening.job
        if self.inline is not None:
            # A queued one answers its place first, as a detached one does; its door awaits the rest.
            hold = asyncio.ensure_future(holding)
            if job.queued:
                return Launched(LaunchedJob.of(job), hold)
            return Launched(LaunchedJob.of(job, (await hold).session.hop), hold)

        async def hold_and_detach() -> CycleHop:
            held = await holding
            await held.detach()
            return held.session.hop

        if job.queued:
            self.opening.job_registry.detach(hold_and_detach())
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
    Every slotted launch first reclaims what a dead producer left, so the machine it counts is
    live; its sweep and its admission wait on the gate, so every async door offloads it once."""
    hop = UNRESOLVED_HOP if request.at is None else request.at.hop
    match holder := request.holder:
        case CampaignLaunch():
            sweep(stores.projects_root)
            (job,) = request_jobs(stores=stores, job_registry=job_registry, holder=holder, hop=hop)
            if inline is not None and job.queued and inline.no_wait:
                refuse_as_busy(job_registry, job)
            opening = Opening.of(stores, job_registry, job)
        case EmbeddedRun() | InnerRun():
            opening = slotless_opening(stores, job_registry, holder, hop)
        case _:
            assert_never(holder)
    return Admitted(opening, request, mode, inline)
