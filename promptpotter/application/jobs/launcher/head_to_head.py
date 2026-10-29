"""A head-to-head's one act: its id claimed, every arm admitted, planned and priced before any is
minted, then all minted and started — or none — each in its own process, or, for an embedded host,
held for it to run."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, assert_never

from promptpotter.application.datasets.authored import load_dataset_campaign_config
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import (
    admit_and_hold,
    claim_cycle,
    probe_backend,
    release_slot,
    request_job,
)
from promptpotter.application.jobs.launcher.checkin import open_checkin, start_ready_draft
from promptpotter.application.jobs.launcher.run_job import HeldRun, detach_runs
from promptpotter.application.jobs.mint import (
    ClaimedArms,
    CyclePlan,
    MintedCycle,
    arm_config,
    arm_plans,
    claim_arms,
    mint_arms,
    mint_framed_arms,
    standing_arms,
    unmint,
)
from promptpotter.application.jobs.quota import land_job
from promptpotter.application.jobs.registry import (
    UNRESOLVED_HOP,
    CampaignLaunch,
    EmbeddedRun,
    Job,
    JobRegistry,
)
from promptpotter.application.pipeline_resolve import draft_config
from promptpotter.domain.campaign import HeadToHeadArmKey, HeadToHeadId
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import declared_backend_type
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.command_kinds import HeadToHeadRequest
    from promptpotter.domain.opt_search_point import NodePrompt
    from promptpotter.domain.spend import HeldBudget
    from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
    from promptpotter.infrastructure.store.draft_store import DraftClaim
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


class ArmStarted(StrictModel):
    arm_key: HeadToHeadArmKey
    job_id: str
    hop: CycleHop


class HeadToHeadStarted(StrictModel):
    """What a head-to-head's act answers, and its retry under the same key: each arm's job and
    the root cycle it minted. Arms never queue, so every hop is resolved."""

    head_to_head_id: HeadToHeadId
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
    """Arms a check-in's Start mints. Carries the draft's claim, which refuses every edit until the
    mint closes the draft; the draft lands only once every arm is admitted, as a single Start's."""

    claim: DraftClaim


type ArmSource = DatasetArms | CheckinArms


@dataclass(frozen=True, slots=True)
class ArmAdmission:
    """One arm as ``admit_and_hold`` reads it: its own config, asking nothing past what it declared."""

    verb: ClassVar[str] = "mint-head-to-head"

    dataset_name: str
    config: CampaignConfig
    backend: str

    @property
    def hop(self) -> CycleHop:
        return UNRESOLVED_HOP

    @property
    def budget(self) -> Budget:
        return Budget()

    @property
    def step_rounds(self) -> None:
        return None

    def backend_type(self, _stores: Stores) -> str:
        return self.backend

    def campaign_config(self, _stores: Stores) -> CampaignConfig:
        return self.config


async def dataset_arms(stores: Stores, dataset: ResolvedDataset) -> DatasetArms:
    if (down := await probe_backend(declared_backend_type(dataset.directory))) is not None:
        raise down
    session = await init_services(
        dataset_name=dataset.name, identity=stores.identity, stores=stores
    )
    return DatasetArms(session, load_dataset_campaign_config(dataset.directory))


def _admissions(
    source: ArmSource, stores: Stores, request: HeadToHeadRequest
) -> list[ArmAdmission]:
    """Off what each arm declares before anything of a check-in lands."""
    match source:
        case DatasetArms(session=session, declared=declared):
            assert session.dataset is not None, "arms declare on the dataset the session opened"
            name, backend = (
                session.measured_dataset,
                declared_backend_type(session.dataset.directory),
            )
        case CheckinArms(claim=claim):
            draft = start_ready_draft(stores, claim.draft_id)
            name, backend, declared = (
                draft.dataset_name,
                draft.connector,
                draft_config(stores, draft),
            )
        case _:
            assert_never(source)
    return [ArmAdmission(name, arm_config(declared, request, arm), backend) for arm in request.arms]


async def _opened(
    source: ArmSource, stores: Stores, request: HeadToHeadRequest
) -> tuple[Session, CampaignConfig, dict[str, NodePrompt] | None]:
    match source:
        case DatasetArms(session=session, declared=declared):
            return session, declared, None
        case CheckinArms(claim=claim):
            overlays = [request.declared_optimization(arm) for arm in request.arms]
            opened = await open_checkin(stores, claim.draft_id, overlays=overlays)
            return opened.session, opened.declared, opened.origin_override
        case _:
            assert_never(source)


async def _mint(source: ArmSource, plans: list[CyclePlan]) -> list[MintedCycle]:
    match source:
        case DatasetArms(session=session):
            return await mint_framed_arms(session, plans)
        case CheckinArms(claim=claim):
            ledger = plans[0].session.store.drafts.ledger(claim.draft_id)
            return mint_arms(plans, billed=ledger if ledger.is_file() else None)
        case _:
            assert_never(source)


@dataclass(frozen=True, slots=True)
class HeldArm:
    arm_key: str
    held: HeldRun


@dataclass(frozen=True, slots=True)
class AdmittedHeadToHead:
    """Every arm admitted and planned under its act's claim; nothing of any arm's cycle is written yet."""

    source: ArmSource
    claimed: ClaimedArms
    plans: list[CyclePlan]
    admitted: list[tuple[Job, HeldBudget]]
    job_registry: JobRegistry

    def _release_claims(self) -> None:
        self.claimed.release()
        if isinstance(self.source, CheckinArms):
            self.source.claim.release()

    async def minted(self) -> list[HeldArm]:
        """Every arm minted and its job landed on its cycle, or none: a refusal takes back each
        slot and each arm minted."""
        stores, job_registry = self.plans[0].session.store, self.job_registry
        minted: list[MintedCycle] = []
        try:
            minted = await _mint(self.source, self.plans)
            for (job, held), cycle in zip(self.admitted, minted, strict=True):
                land_job(stores, job_registry, job.job_id, cycle.session.hop, held.share)
                if isinstance(job.holder, CampaignLaunch):
                    claim_cycle(stores, job_registry, job, cycle.session.hop)
            if isinstance(self.source, CheckinArms):
                stores.drafts.close(self.source.claim.draft_id)
        except BaseException as exc:
            for job, _ in self.admitted:
                release_slot(job_registry, job.job_id, exc, stores=stores, hop=None)
            unmint(stores, [cycle.campaign_id for cycle in minted])
            raise
        finally:
            self._release_claims()
        logger.info(
            "head-to-head %s: minted %s",
            self.claimed.request.head_to_head_id,
            ", ".join(
                f"{arm.arm_key}={cycle.campaign_id}"
                for arm, cycle in zip(self.claimed.request.arms, minted, strict=True)
            ),
        )
        return [
            HeldArm(arm.arm_key, HeldRun.of(job_registry, job.job_id, cycle, held))
            for arm, (job, held), cycle in zip(
                self.claimed.request.arms, self.admitted, minted, strict=True
            )
        ]

    async def launched(self) -> HeadToHeadLaunched:
        """Each arm in its own process, as every server launch; all start or none, and none
        started takes back every arm."""
        arms = await self.minted()
        stores = self.plans[0].session.store
        try:
            exits = detach_runs([arm.held for arm in arms], mode=RunMode())
        except BaseException:
            unmint(stores, [arm.held.session.hop.campaign_id for arm in arms])
            raise
        started = HeadToHeadStarted(
            head_to_head_id=self.claimed.request.head_to_head_id,
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
    stores: Stores,
    job_registry: JobRegistry,
    holder: CampaignLaunch | EmbeddedRun,
) -> AdmittedHeadToHead:
    """Claimed, then every arm admitted as one launch is (``admit_and_hold``) on its own
    declaration, then a check-in's draft landed, then every arm planned and priced. A refusal
    takes back every slot and the claim; a check-in's is its caller's to end."""
    claimed = claim_arms(stores, request)
    jobs: list[Job] = []
    try:
        arms = _admissions(source, stores, request)
        jobs = await asyncio.to_thread(
            request_job,
            stores=stores,
            job_registry=job_registry,
            holder=holder,
            launches=len(arms),
        )
        admitted = [
            (job, await admit_and_hold(arm, stores=stores, job_registry=job_registry, job=job))
            for arm, job in zip(arms, jobs, strict=True)
        ]
        session, declared, origin_override = await _opened(source, stores, request)
        plans = await arm_plans(
            session, declared, claimed, session.bank.samples, origin_override=origin_override
        )
    except BaseException as exc:
        if isinstance(exc, BackendUnreachableError) and isinstance(source, CheckinArms):
            exc.details["draft_id"] = source.claim.draft_id
        for job in jobs:
            release_slot(job_registry, job.job_id, exc, stores=stores, hop=None)
        claimed.release()
        raise
    return AdmittedHeadToHead(source, claimed, plans, admitted, job_registry)


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
