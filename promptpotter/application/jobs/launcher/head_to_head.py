"""A head-to-head's one act: every arm planned, priced and admitted before any is minted, then
each detached to its own process — or, for an embedded host, held for it to run."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import assert_never

from promptpotter.application.datasets.authored import ConfigLayers, effective_config
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import admit_arms, refuse_unreachable
from promptpotter.application.jobs.launcher.checkin import OpenedCheckin, open_checkin
from promptpotter.application.jobs.launcher.launch import LaunchOutcome
from promptpotter.application.jobs.launcher.opening import Opening
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.mint import (
    CyclePlan,
    OpenedCycle,
    arm_plans,
    mint_arms,
    mint_framed_arms,
    minted_off_loop,
)
from promptpotter.application.jobs.quota import sends_on
from promptpotter.application.jobs.reaper import sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.pipeline_resolve import ServedPipeline
from promptpotter.domain.command_effects import LaunchedJob
from promptpotter.domain.command_kinds import HeadToHeadRequest
from promptpotter.domain.draft_edit import CampaignLayer
from promptpotter.domain.job import CampaignLaunch, EmbeddedRun
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.spend import Budget, HeldBudget
from promptpotter.infrastructure.llm.wallet import Send
from promptpotter.infrastructure.store.dataset_access import ResolvedDataset, runnable_definition
from promptpotter.infrastructure.store.draft_store import DraftClaim
from promptpotter.infrastructure.store.stores import Stores


@dataclass(frozen=True, slots=True)
class HeadToHeadLaunched(LaunchOutcome):
    """``launches`` is what its ack records, arm by arm in the request's order, each on the root
    cycle it minted; ``exits`` end as each arm's process does, and a replay watches none, since
    another act owns them."""

    launches: list[LaunchedJob]
    exits: list[asyncio.Task[None]]

    def as_launches(self) -> list[LaunchedJob]:
        return self.launches


@dataclass(frozen=True, slots=True)
class DatasetArms:
    """Arms on a committed dataset: a framing it still owes is paid once, for every arm. *closing*
    closes what opened the session every arm shares, once the act ends (`launched`)."""

    session: Session
    layers: ConfigLayers
    closing: AsyncExitStack


@dataclass(frozen=True, slots=True)
class CheckinArms:
    """Arms a check-in's Start mints: the draft's ledger bills the first, and the mint closes it.
    Carries the claim, which refuses every edit of the draft until then."""

    claim: DraftClaim
    opened: OpenedCheckin
    closing: AsyncExitStack


type ArmSource = DatasetArms | CheckinArms


async def dataset_arms(
    stores: Stores, dataset: ResolvedDataset, stated: CampaignLayer | None
) -> DatasetArms:
    """*stated* is a declaration in place of the dataset's own (`ConfigLayers.of`)."""
    definition = runnable_definition(dataset)
    await refuse_unreachable(stores, dataset.name, definition.owner, definition.endpoint)
    closing = AsyncExitStack()
    session = await closing.enter_async_context(
        init_services(dataset_name=dataset.name, identity=stores.identity, stores=stores)
    )
    return DatasetArms(session, ConfigLayers.of(dataset, stated), closing)


async def checkin_arms(
    stores: Stores, claim: DraftClaim, request: HeadToHeadRequest, served: ServedPipeline
) -> CheckinArms:
    await refuse_unreachable(stores, None, served.connector, served.endpoint)
    overlays = [request.declared_optimization(arm) for arm in request.arms]
    closing = AsyncExitStack()
    opened = await closing.enter_async_context(
        open_checkin(stores, claim.draft_id, served, overlays=overlays)
    )
    return CheckinArms(claim, opened, closing)


def _opened(source: ArmSource) -> tuple[Session, ConfigLayers, OpenedCheckin | None]:
    match source:
        case DatasetArms(session=session, layers=layers):
            return session, layers, None
        case CheckinArms(opened=opened):
            return opened.session, opened.layers, opened
        case _:
            assert_never(source)


async def _mint(
    source: ArmSource, plans: list[CyclePlan], openings: list[Opening]
) -> AsyncIterator[OpenedCycle]:
    """Each arm as it lands, its job naming its cycle first."""
    match source:
        case DatasetArms(session=session):
            async for cycle in mint_framed_arms(session, plans, openings=openings):
                yield cycle
        case CheckinArms(opened=opened):
            draft_id = opened.draft.draft_id
            async for cycle in minted_off_loop(
                mint_arms(plans, adopting=draft_id, openings=openings)
            ):
                yield cycle
        case _:
            assert_never(source)


@dataclass(frozen=True, slots=True)
class HeldArm:
    arm_key: str
    held: HeldRun


async def run_each[T](arms: list[HeldArm], act: Callable[[HeldArm], Awaitable[T]]) -> list[T]:
    """*act* on each minted arm in order, each closing its own arm (`HeldRun.closing`); a raise
    closes every arm after it, whose share no other process would ever hand back."""
    async with AsyncExitStack() as unrun:
        for arm in arms:
            # One *act* closed is empty here: the stack closes only the arms it never reached.
            unrun.push_async_exit(arm.held.closing)
        return [await act(arm) for arm in arms]


@dataclass(frozen=True, slots=True)
class AdmittedHeadToHead(LaunchOutcome):
    """Every arm planned and admitted; nothing of any arm's cycle is written yet."""

    source: ArmSource
    request: HeadToHeadRequest
    plans: list[CyclePlan]
    admitted: list[tuple[Opening, HeldBudget]]

    def as_launches(self) -> list[LaunchedJob]:
        return [LaunchedJob.of(opening.job) for opening, _ in self.admitted]

    async def withdraw(self, exc: BaseException) -> None:
        """Every arm's slot given back and the source closed, where the act that admitted them
        raised before any minted."""
        async with self.source.closing:
            for opening, _ in self.admitted:
                await opening.abandon(exc)

    async def minted(self) -> list[HeldArm]:
        """All arms minted, each job landed on its cycle as it lands, each arm's opening riding its
        own `HeldRun.closing`; a failure closes every arm, stamping every cycle minted before it."""
        closings = [AsyncExitStack() for _ in self.admitted]
        minted: list[OpenedCycle] = []
        try:
            async with AsyncExitStack() as every:
                for closing, (opening, _) in zip(closings, self.admitted, strict=True):
                    closing.push_async_exit(opening)
                    every.push_async_exit(closing)
                async for cycle in _mint(self.source, self.plans, [o for o, _ in self.admitted]):
                    opening, held = self.admitted[len(minted)]
                    minted.append(cycle)
                    await asyncio.to_thread(opening.land, cycle.hop, held)
                every.pop_all()
        finally:
            if isinstance(self.source, CheckinArms):
                self.source.claim.release()
        # Each arm's session is the source's, which `launched` closes past every hand-off.
        return [
            HeldArm(arm.arm_key, HeldRun.of(opening, cycle, held, closing, RunMode()))
            for arm, (opening, held), cycle, closing in zip(
                self.request.arms, self.admitted, minted, closings, strict=True
            )
        ]

    async def launched(self) -> HeadToHeadLaunched:
        """Each arm in its own process, as every server launch."""
        async with self.source.closing:
            arms = await self.minted()
            exits = await run_each(arms, lambda arm: arm.held.detach())
        launches = [
            LaunchedJob.of(opening.job, arm.held.session.hop)
            for (opening, _), arm in zip(self.admitted, arms, strict=True)
        ]
        return head_to_head_answer(self.request, launches, exits)


async def admit_head_to_head(
    source: ArmSource,
    request: HeadToHeadRequest,
    *,
    job_registry: JobRegistry,
    holder: CampaignLaunch | EmbeddedRun,
) -> AdmittedHeadToHead:
    """Each arm's budget is its own declaration's, so an arm binds on what it declared alone. A
    refusal closes *source*, whose session nothing else would close."""
    session, layers, opened = _opened(source)
    async with AsyncExitStack() as refused:
        refused.push_async_exit(source.closing)
        plans = await arm_plans(
            session,
            layers,
            request,
            session.bank.samples,
            origin_override=None if opened is None else opened.origin_override,
        )
        # The arms share the session's dataset, so its connector.
        connector = session.backend_client.connector
        configs = [effective_config(plan.spec) for plan in plans]
        asks = [
            (config.optimization.budget, sends_on(session.store, config, connector))
            for config in configs
        ]
        # Off the loop, once: the sweep and the admission wait on the gate and scan every ledger.
        admitted = await asyncio.to_thread(_admit, asks, session.store, job_registry, holder)
        refused.pop_all()
    return AdmittedHeadToHead(source, request, plans, admitted)


def _admit(
    asks: list[tuple[Budget, tuple[Send, ...]]],
    stores: Stores,
    job_registry: JobRegistry,
    holder: CampaignLaunch | EmbeddedRun,
) -> list[tuple[Opening, HeldBudget]]:
    sweep(stores.projects_root)
    return admit_arms(asks, stores=stores, job_registry=job_registry, holder=holder)


def head_to_head_answer(
    request: HeadToHeadRequest,
    launches: list[LaunchedJob],
    exits: list[asyncio.Task[None]] | None = None,
) -> HeadToHeadLaunched:
    """What the act answers from what its ack records, arm by arm in the request's order: first
    as it launched, and again for a retry under its key, which watches no exit. Arms never queue,
    so every arm names its cycle."""
    if len(launches) != len(request.arms) or any(launch.hop is None for launch in launches):
        raise ValueError(f"head-to-head {request.head_to_head_id}: an arm launched no cycle")
    return HeadToHeadLaunched(launches, exits or [])
