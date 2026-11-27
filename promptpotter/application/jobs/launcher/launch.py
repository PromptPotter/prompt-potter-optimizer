from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Coroutine
from contextlib import AsyncExitStack, ExitStack
from dataclasses import dataclass
from typing import Any, assert_never

from promptpotter.application.jobs.launcher.admission import (
    admit_and_hold,
    refuse_as_busy,
    refuse_campaign,
    request_jobs,
    slotless_opening,
)
from promptpotter.application.jobs.launcher.checkin import CheckinStart
from promptpotter.application.jobs.launcher.mint_and_start import Hosted, LaunchRequest
from promptpotter.application.jobs.launcher.opening import Opening
from promptpotter.application.jobs.launcher.run_job import HeldLaunch, HeldRun, launched_as
from promptpotter.application.jobs.reaper import sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.command_events import QueuedLaunch
from promptpotter.domain.command_kinds import Door
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import UNRESOLVED_HOP, CampaignLaunch, EmbeddedRun, InnerRun, Job
from promptpotter.domain.records.run_records import LaunchedJob
from promptpotter.domain.run_mode import RunMode
from promptpotter.infrastructure.store.stores import Stores


@dataclass(frozen=True, slots=True)
class Inline:
    """A launch answered held in this process: a person's terminal, a host's run (*hosted*), which
    the launch kinds open slotless, or a queued launch's own process, its job *claimed* for it."""

    no_wait: bool = False
    hosted: Hosted | None = None
    claimed: Job | None = None


@dataclass(frozen=True, slots=True)
class Asked:
    """The command a launch acts for, which a queued one's process acts again (`QueuedLaunch`)."""

    command_id: str
    door: Door


class LaunchOutcome:
    """What a launch kind's act answers in process; every door serves it as a `LaunchAnswer`."""

    __slots__ = ()

    def as_launches(self) -> list[LaunchedJob]:
        """Each job it launched, as its ack records them."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Launched(LaunchOutcome):
    """What the launch answers, as its ack records it; ``held`` is the inline run's hold, and
    ``None`` where the run goes to its own process, a queued one's included."""

    launch: LaunchedJob
    held: asyncio.Future[HeldRun] | None

    def as_launches(self) -> list[LaunchedJob]:
        return [self.launch]


async def hold(
    request: LaunchRequest, opening: Opening, mode: RunMode, *, here: bool
) -> HeldLaunch:
    """THE way into a held run: past the queue and the money admission, then the request's cycle
    opened, its session and the opening's exit riding the held run's ``closing``. One with no hop
    yet mints it, so a refusal before then leaves nothing written and one after stamps the minted
    cycle; an existing one is never stamped, and is opened only *here*, never twice: a detached
    run opens it in its own process. A check-in's claim ends with this hold however it ends."""
    try:
        async with AsyncExitStack() as closing:
            closing.push_async_exit(opening)
            held, standing = await admit_and_hold(request, opening)
            if standing.at is not None and not here:
                return HeldLaunch(opening, standing.at.hop, held, closing.pop_all(), mode)
            opened = await closing.enter_async_context(standing.open(opening))
            if standing.at is None:
                await asyncio.to_thread(opening.land, opened.hop, held)
            return HeldRun.of(opening, opened, held, closing.pop_all(), mode)
    finally:
        if isinstance(request, CheckinStart):
            request.claim.release()


async def _here(holding: Awaitable[HeldLaunch]) -> HeldRun:
    held = await holding
    assert isinstance(held, HeldRun), "a launch held here opens its cycle"
    return held


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
        """Its slot or its place given back, where the act that admitted it raised before any run
        held it."""
        if self.opening.job.queued:
            await asyncio.to_thread(self.opening.job_registry.cancel_queued, self.opening.job_id)
            return
        await self.opening.abandon(exc)

    async def held(self) -> HeldRun:
        """Its run held here: past the queue, the money admission and the cycle's session."""
        return await _here(hold(self.request, self.opening, self.mode, here=True))

    async def handed(self) -> HeldLaunch:
        """Held for where its run goes: inline, its session opened here; else in its own process."""
        return await hold(self.request, self.opening, self.mode, here=self.inline is not None)

    async def hand_over(self, holding: Coroutine[Any, Any, HeldLaunch]) -> Launched:
        """*holding* — `handed`, or a held step that acts once the run is held (a fork's cut on its
        parent) — awaited where the run goes: inline or detached. A queued one answers its place:
        its own process holds it once a slot frees (`JobRegistry.promote`)."""
        job = self.opening.job
        if job.queued:
            holding.close()
            if isinstance(self.request, CheckinStart):
                self.request.claim.release()
            return Launched(LaunchedJob.of(job), None)
        if self.inline is not None:
            hold = asyncio.ensure_future(_here(holding))
            return Launched(LaunchedJob.of(job, (await hold).hop), hold)

        async def hold_and_detach() -> CycleHop:
            held = await holding
            await held.detach()
            return held.hop

        return Launched(LaunchedJob.of(job, await hold_and_detach()), None)


def admit(
    request: LaunchRequest,
    mode: RunMode,
    *,
    stores: Stores,
    job_registry: JobRegistry,
    inline: Inline | None,
    asked: Asked | None = None,
) -> Admitted:
    """Call AFTER everything a person types: a slot held across an interactive check-in is lost.
    Every slotted launch first reclaims what a dead producer left, so the machine it counts is
    live; its sweep and its admission wait on the gate, so every async door offloads it once. A
    queued one files its ask and waits on disk; its process asks its campaign again."""
    hop = UNRESOLVED_HOP if request.at is None else request.at.hop
    match holder := request.holder:
        case CampaignLaunch() if inline is not None and inline.claimed is not None:
            opening = Opening.of(stores, job_registry, inline.claimed)
            with ExitStack() as refused:
                refused.push(opening)
                # The slot door's claim held off an archive or a delete, never a moved definition.
                if (campaign := request.campaign) is not None:
                    refuse_campaign(stores, campaign)
                refused.pop_all()
        case CampaignLaunch():
            sweep(stores.projects_root)
            (job,) = request_jobs(stores=stores, job_registry=job_registry, holder=holder, hop=hop)
            if job.queued:
                if inline is not None and inline.no_wait:
                    refuse_as_busy(job_registry, job)
                assert asked is not None, "a slotted launch acts for a command"
                launch = launched_as(stores, job_registry.jobs_dir, job.job_id)
                ask = QueuedLaunch(**dict(launch), command_id=asked.command_id, door=asked.door)
                job_registry.file_ask(ask)
            opening = Opening.of(stores, job_registry, job)
            if job.queued:
                # Its own process, started when a slot frees, holds it and releases it.
                opening.hand_off()
        case EmbeddedRun():
            if (campaign := request.campaign) is not None:
                refuse_campaign(stores, campaign)
            opening = slotless_opening(stores, job_registry, holder, hop)
        case InnerRun():
            opening = slotless_opening(stores, job_registry, holder, hop)
        case _:
            assert_never(holder)
    return Admitted(opening, request, mode, inline)
