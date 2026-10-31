"""A head-to-head's one act: every arm planned, priced and admitted before any is minted, then
each detached to its own process — or, for an embedded host, held for it to run."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from promptpotter.application.datasets.authored import load_dataset_campaign_config
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import (
    admit_arms,
    claim_cycle,
    probe_backend,
    release_slot,
)
from promptpotter.application.jobs.launcher.checkin import (
    OpenedCheckin,
    open_checkin,
    start_ready_draft,
)
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.mint import (
    CyclePlan,
    MintedCycle,
    arm_plans,
    mint_arms,
    mint_framed_arms,
    standing_arms,
)
from promptpotter.application.jobs.quota import land_job
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.campaign import ArmName
from promptpotter.domain.campaign_config import CampaignConfig, effective_config
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import CampaignLaunch, EmbeddedRun, Job
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import declared_backend_type
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.command_kinds import HeadToHeadRequest
    from promptpotter.domain.spend import HeldBudget
    from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
    from promptpotter.infrastructure.store.draft_store import DraftClaim
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


class ArmStarted(StrictModel):
    arm_key: ArmName
    job_id: str
    hop: CycleHop


class HeadToHeadStarted(StrictModel):
    """What a head-to-head's act answers, and its retry under the same key: each arm's job and
    the root cycle it minted. Arms never queue, so every hop is resolved."""

    head_to_head_id: ArmName
    arms: list[ArmStarted]


@dataclass(frozen=True, slots=True)
class HeadToHeadLaunched:
    """``exits`` end as each arm's process does; a replay watches none, since another act owns them."""

    started: HeadToHeadStarted
    exits: list[asyncio.Task[None]]


@dataclass(frozen=True, slots=True)
class DatasetArms:
    """Arms on a committed dataset: a framing it still owes is paid once, for every arm."""

    session: Session
    declared: CampaignConfig


@dataclass(frozen=True, slots=True)
class CheckinArms:
    """Arms a check-in's Start mints: the draft's ledger bills the first, and the mint closes it.
    Carries the claim, which refuses every edit of the draft until then."""

    claim: DraftClaim
    opened: OpenedCheckin


type ArmSource = DatasetArms | CheckinArms


async def _probe(backend_type: str) -> None:
    if (down := await probe_backend(backend_type)) is not None:
        raise down


async def dataset_arms(stores: Stores, dataset: ResolvedDataset) -> DatasetArms:
    await _probe(declared_backend_type(dataset.directory))
    session = await init_services(
        dataset_name=dataset.name, identity=stores.identity, stores=stores
    )
    return DatasetArms(session, load_dataset_campaign_config(dataset.directory))


async def checkin_arms(
    stores: Stores, claim: DraftClaim, request: HeadToHeadRequest
) -> CheckinArms:
    try:
        await _probe(start_ready_draft(stores, claim.draft_id).connector)
    except BackendUnreachableError as exc:
        exc.details["draft_id"] = claim.draft_id
        raise
    overlays = [request.declared_optimization(arm) for arm in request.arms]
    return CheckinArms(claim, await open_checkin(stores, claim.draft_id, overlays=overlays))


def _opened(source: ArmSource) -> tuple[Session, CampaignConfig, OpenedCheckin | None]:
    match source:
        case DatasetArms(session=session, declared=declared):
            return session, declared, None
        case CheckinArms(opened=opened):
            return opened.session, opened.declared, opened
        case _:
            assert_never(source)


async def _mint(source: ArmSource, plans: list[CyclePlan]) -> list[MintedCycle]:
    match source:
        case DatasetArms(session=session):
            return await mint_framed_arms(session, plans)
        case CheckinArms(opened=opened):
            drafts = opened.session.store.drafts
            ledger = drafts.ledger(opened.draft.draft_id)
            minted = mint_arms(plans, billed=ledger if ledger.is_file() else None)
            drafts.close(opened.draft.draft_id)
            return minted
        case _:
            assert_never(source)


@dataclass(frozen=True, slots=True)
class HeldArm:
    arm_key: str
    held: HeldRun


@dataclass(frozen=True, slots=True)
class AdmittedHeadToHead:
    """Every arm planned and admitted; nothing of any arm's cycle is written yet."""

    source: ArmSource
    request: HeadToHeadRequest
    plans: list[CyclePlan]
    admitted: list[tuple[Job, HeldBudget]]
    job_registry: JobRegistry

    async def minted(self) -> list[HeldArm]:
        """All arms minted, each job landed on its cycle; a failure stamps every minted cycle."""
        stores, job_registry = self.plans[0].session.store, self.job_registry
        minted: list[MintedCycle] = []
        try:
            minted = await _mint(self.source, self.plans)
            for (job, held), cycle in zip(self.admitted, minted, strict=True):
                land_job(stores, job_registry, job.job_id, cycle.session.hop, held.share)
                if isinstance(job.holder, CampaignLaunch):
                    claim_cycle(stores, job_registry, job, cycle.session.hop)
        except BaseException as exc:
            for i, (job, _) in enumerate(self.admitted):
                stamped = minted[i].session.hop if minted else None
                release_slot(job_registry, job.job_id, exc, stores=stores, hop=stamped)
            raise
        finally:
            if isinstance(self.source, CheckinArms):
                self.source.claim.release()
        logger.info(
            "head-to-head %s: minted %s",
            self.request.head_to_head_id,
            ", ".join(
                f"{arm.arm_key}={cycle.campaign_id}"
                for arm, cycle in zip(self.request.arms, minted, strict=True)
            ),
        )
        return [
            HeldArm(arm.arm_key, HeldRun.of(job_registry, job.job_id, cycle, held))
            for arm, (job, held), cycle in zip(
                self.request.arms, self.admitted, minted, strict=True
            )
        ]

    async def launched(self) -> HeadToHeadLaunched:
        """Each arm in its own process, as every server launch; one that fails to start takes the
        arms after it back, since nothing else would ever release their slots."""
        arms = await self.minted()
        exits: list[asyncio.Task[None]] = []
        for i, arm in enumerate(arms):
            try:
                exits.append(arm.held.detach(mode=RunMode()))
            except BaseException as exc:
                for rest in arms[i + 1 :]:
                    session = rest.held.session
                    release_slot(
                        self.job_registry,
                        rest.held.job_id,
                        exc,
                        stores=session.store,
                        hop=session.hop,
                    )
                raise
        started = HeadToHeadStarted(
            head_to_head_id=self.request.head_to_head_id,
            arms=[
                ArmStarted(arm_key=arm.arm_key, job_id=arm.held.job_id, hop=arm.held.session.hop)
                for arm in arms
            ],
        )
        return HeadToHeadLaunched(started, exits)


async def admit_head_to_head(
    source: ArmSource,
    request: HeadToHeadRequest,
    *,
    job_registry: JobRegistry,
    holder: CampaignLaunch | EmbeddedRun,
) -> AdmittedHeadToHead:
    """Each arm's budget is its own declaration's, so an arm binds on what it declared alone."""
    session, declared, opened = _opened(source)
    plans = await arm_plans(
        session,
        declared,
        request,
        session.bank.samples,
        origin_override=None if opened is None else opened.origin_override,
    )
    # Offloaded: the wallet read scans every cycle ledger and must not block the event loop.
    admitted = await asyncio.to_thread(
        admit_arms,
        [effective_config(plan.declaration).optimization.budget for plan in plans],
        stores=session.store,
        job_registry=job_registry,
        holder=holder,
    )
    return AdmittedHeadToHead(source, request, plans, admitted, job_registry)


def replayed_head_to_head(
    stores: Stores, job_registry: JobRegistry, request: HeadToHeadRequest
) -> HeadToHeadLaunched:
    """The answer an applied act gave, read back off disk: each arm's campaign, and the launch
    that first held its root cycle."""
    arms = standing_arms(stores, request.head_to_head_id)
    launches = sorted(
        (
            job
            for job in job_registry.list_all(user_id=str(stores.identity.user_id))
            if isinstance(job.holder, CampaignLaunch)
        ),
        key=lambda job: job.created_at,
    )
    started: list[ArmStarted] = []
    for arm in request.arms:
        campaign = arms.get(arm.arm_key)
        job = (
            None
            if campaign is None
            else next((j for j in launches if j.hop == campaign.root_hop), None)
        )
        if campaign is None or job is None:
            raise NotFoundError(
                f"arm {arm.arm_key} of head-to-head {request.head_to_head_id} was minted under "
                "this idempotency key and is gone"
            )
        started.append(ArmStarted(arm_key=arm.arm_key, job_id=job.job_id, hop=campaign.root_hop))
    return HeadToHeadLaunched(
        HeadToHeadStarted(head_to_head_id=request.head_to_head_id, arms=started), []
    )
