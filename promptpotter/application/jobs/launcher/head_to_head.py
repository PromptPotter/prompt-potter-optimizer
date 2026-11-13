"""A head-to-head's one act: every arm planned, priced and admitted before any is minted, then
each detached to its own process — or, for an embedded host, held for it to run."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from promptpotter.application.datasets.authored import ConfigLayers, effective_config
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import RunStart, admit_arms, probe_backend
from promptpotter.application.jobs.launcher.checkin import OpenedCheckin, open_checkin
from promptpotter.application.jobs.launcher.launch import LaunchOutcome
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.mint import (
    CyclePlan,
    OpenedCycle,
    arm_plans,
    mint_arms,
    mint_framed_arms,
)
from promptpotter.application.jobs.quota import sends_on
from promptpotter.application.jobs.reaper import sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.job import CampaignLaunch, EmbeddedRun, Job
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import LaunchedJob
from promptpotter.infrastructure.store.dataset_access import runnable_definition

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.pipeline_resolve import ServedPipeline
    from promptpotter.domain.command_kinds import HeadToHeadRequest
    from promptpotter.domain.spend import Budget, HeldBudget
    from promptpotter.infrastructure.llm.wallet import Send
    from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
    from promptpotter.infrastructure.store.draft_store import DraftClaim
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


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


async def _probe(backend_type: str) -> None:
    if (down := await probe_backend(backend_type)) is not None:
        raise down


async def dataset_arms(stores: Stores, dataset: ResolvedDataset) -> DatasetArms:
    await _probe(runnable_definition(dataset).backend_type)
    closing = AsyncExitStack()
    session = await closing.enter_async_context(
        init_services(dataset_name=dataset.name, identity=stores.identity, stores=stores)
    )
    return DatasetArms(session, ConfigLayers.of(dataset, None), closing)


async def checkin_arms(
    stores: Stores, claim: DraftClaim, request: HeadToHeadRequest, served: ServedPipeline
) -> CheckinArms:
    await _probe(served.connector)
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


async def _mint(source: ArmSource, plans: list[CyclePlan]) -> list[OpenedCycle]:
    match source:
        case DatasetArms(session=session):
            return await mint_framed_arms(session, plans)
        case CheckinArms(opened=opened):
            drafts = opened.session.store.drafts
            minted = mint_arms(plans, adopting=opened.draft.draft_id)
            drafts.close(opened.draft.draft_id)
            return minted
        case _:
            assert_never(source)


@dataclass(frozen=True, slots=True)
class HeldArm:
    arm_key: str
    held: HeldRun


async def run_each[T](arms: list[HeldArm], act: Callable[[HeldArm], Awaitable[T]]) -> list[T]:
    """*act* on each minted arm in order; a raise releases every arm after it, whose share no other
    process would ever hand back."""
    done: list[T] = []
    for i, arm in enumerate(arms):
        try:
            done.append(await act(arm))
        except BaseException as exc:
            await asyncio.to_thread(_release_rest, arms[i + 1 :], exc)
            raise
    return done


def _release_rest(rest: list[HeldArm], exc: BaseException) -> None:
    for arm in rest:
        arm.held.start.release(exc, stamps=arm.held.session.hop)


@dataclass(frozen=True, slots=True)
class AdmittedHeadToHead(LaunchOutcome):
    """Every arm planned and admitted; nothing of any arm's cycle is written yet."""

    source: ArmSource
    request: HeadToHeadRequest
    plans: list[CyclePlan]
    admitted: list[tuple[Job, HeldBudget]]
    job_registry: JobRegistry

    def as_launches(self) -> list[LaunchedJob]:
        return [LaunchedJob.of(job) for job, _ in self.admitted]

    @property
    def starts(self) -> list[RunStart]:
        stores = self.plans[0].session.store
        return [RunStart(stores, self.job_registry, job) for job, _ in self.admitted]

    async def withdraw(self, exc: BaseException) -> None:
        """Every arm's slot given back and the source closed, where the act that admitted them
        raised before any minted."""
        async with self.source.closing:
            for start in self.starts:
                logger.warning("admitted arm %s withdrawn unrun: %s", start.job_id, exc)
                await asyncio.to_thread(start.release, exc)

    async def minted(self) -> list[HeldArm]:
        """All arms minted, each job landed on its cycle; a failure stamps every minted cycle."""
        minted: list[OpenedCycle] = []
        try:
            minted = await _mint(self.source, self.plans)
            await asyncio.to_thread(self._land, minted)
        except BaseException as exc:
            await asyncio.to_thread(self._unmint, minted, exc)
            raise
        finally:
            if isinstance(self.source, CheckinArms):
                self.source.claim.release()
        logger.info(
            "head-to-head %s: minted %s",
            self.request.head_to_head_id,
            ", ".join(
                f"{arm.arm_key}={cycle.hop.campaign_id}"
                for arm, cycle in zip(self.request.arms, minted, strict=True)
            ),
        )
        # Each arm's session is the source's, which `launched` closes past every hand-off.
        return [
            HeldArm(arm.arm_key, HeldRun.of(start, cycle, held, AsyncExitStack()))
            for arm, start, (_, held), cycle in zip(
                self.request.arms, self.starts, self.admitted, minted, strict=True
            )
        ]

    def _land(self, minted: list[OpenedCycle]) -> None:
        for start, (_, held), cycle in zip(self.starts, self.admitted, minted, strict=True):
            start.land(cycle.hop, held)

    def _unmint(self, minted: list[OpenedCycle], exc: BaseException) -> None:
        for i, start in enumerate(self.starts):
            start.release(exc, stamps=minted[i].hop if minted else None)

    async def launched(self) -> HeadToHeadLaunched:
        """Each arm in its own process, as every server launch."""
        async with self.source.closing:
            arms = await self.minted()
            exits = await run_each(arms, lambda arm: arm.held.detach(mode=RunMode()))
        launches = [
            LaunchedJob.of(job, arm.held.session.hop)
            for (job, _), arm in zip(self.admitted, arms, strict=True)
        ]
        return head_to_head_answer(self.request, launches, exits)


async def admit_head_to_head(
    source: ArmSource,
    request: HeadToHeadRequest,
    *,
    job_registry: JobRegistry,
    holder: CampaignLaunch | EmbeddedRun,
) -> AdmittedHeadToHead:
    """Each arm's budget is its own declaration's, so an arm binds on what it declared alone."""
    session, layers, opened = _opened(source)
    plans = await arm_plans(
        session,
        layers,
        request,
        session.bank.samples,
        origin_override=None if opened is None else opened.origin_override,
    )
    configs = [
        (effective_config(plan.declaration), plan.declaration.environment.connector)
        for plan in plans
    ]
    asks = [
        (config.optimization.budget, sends_on(session.store, config, connector))
        for config, connector in configs
    ]
    # Off the loop, once: the sweep and the admission wait on the gate and scan every ledger.
    admitted = await asyncio.to_thread(_admit, asks, session.store, job_registry, holder)
    return AdmittedHeadToHead(source, request, plans, admitted, job_registry)


def _admit(
    asks: list[tuple[Budget, tuple[Send, ...]]],
    stores: Stores,
    job_registry: JobRegistry,
    holder: CampaignLaunch | EmbeddedRun,
) -> list[tuple[Job, HeldBudget]]:
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
