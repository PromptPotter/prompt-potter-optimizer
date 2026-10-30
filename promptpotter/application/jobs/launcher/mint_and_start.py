from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from promptpotter.application.datasets.authored import (
    load_dataset_campaign_config,
    with_optimization,
)
from promptpotter.application.datasets.draft_build import (
    default_campaign_block,
    draft_task_context,
)
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    committed_pipeline_json,
)
from promptpotter.application.datasets.origin_readiness import FieldGap, origin_readiness
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import (
    admit_and_hold,
    claim_cycle,
    release_slot,
)
from promptpotter.application.jobs.launcher.run_job import HeldRun, open_held_cycle
from promptpotter.application.jobs.mint import (
    declare,
    fresh_campaign_id,
    mint_framed_cycle,
)
from promptpotter.application.jobs.quota import land_job
from promptpotter.application.jobs.registry import UNRESOLVED_HOP, Job, JobRegistry
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.domain.campaign import Campaign, Purpose
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    declared_backend_type,
    resolve_dataset,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError

logger = logging.getLogger(__name__)


class LaunchError(PayloadInvalidError):
    """A mint-time failure: missing dataset, malformed config."""


class OriginIncompleteError(PayloadInvalidError):
    code = "origin_incomplete"

    def __init__(self, gaps: tuple[FieldGap, ...]) -> None:
        self.gaps = gaps
        fields = ", ".join(gap.field for gap in gaps) or "<none>"
        super().__init__(
            f"origin incomplete — unresolved fields: {fields}",
            details={"gaps": [gap.model_dump() for gap in gaps]},
        )


def assert_origin_ready(draft: DraftCampaign) -> None:
    readiness = origin_readiness(draft)
    if not readiness.complete:
        raise OriginIncompleteError(readiness.gaps)


@dataclass(frozen=True, slots=True)
class FreshMint:
    verb: ClassVar[str] = "mint"

    # Resolved once, where the request is built (`commands/launching.py::mint_campaign`).
    dataset: ResolvedDataset
    budget: Budget
    optimization: Mapping[str, Any]
    purpose: Purpose
    declared: CampaignConfig | None
    task_text: str | None

    @property
    def hop(self) -> CycleHop:
        return UNRESOLVED_HOP

    @property
    def step_rounds(self) -> None:
        return None

    @property
    def dataset_name(self) -> str:
        return self.dataset.name

    @property
    def draft_id(self) -> None:
        return None

    def backend_type(self, _stores: Stores) -> str:
        return declared_backend_type(self.dataset.directory)

    def campaign_config(self, _stores: Stores) -> CampaignConfig:
        declared = self.declared or load_dataset_campaign_config(self.dataset.directory)
        return with_optimization(declared, self.optimization)


@dataclass(frozen=True, slots=True)
class ExistingCycle:
    verb: ClassVar[str] = "start-run"

    hop: CycleHop
    campaign: Campaign
    budget: Budget
    step_rounds: int | None = None

    @property
    def dataset_name(self) -> str:
        return self.campaign.dataset_name

    @property
    def draft_id(self) -> None:
        return None

    def backend_type(self, stores: Stores) -> str:
        return declared_backend_type(resolve_dataset(stores, self.dataset_name).directory)

    def campaign_config(self, stores: Stores) -> CampaignConfig:
        return resolve_campaign_config(stores, self.campaign, self.hop)


async def hold_fresh_mint(
    request: FreshMint, *, stores: Stores, job_registry: JobRegistry, job: Job
) -> HeldRun:
    """Admit, then session, then mint: nothing is written before admission, so a refusal leaves none."""
    dataset_name = request.dataset_name
    # Bound only after the mint, so the failure path knows whether a cycle exists to stamp.
    hop: CycleHop | None = None
    try:
        held = await admit_and_hold(request, stores=stores, job_registry=job_registry, job=job)

        _t0 = time.perf_counter()
        session = await init_services(
            dataset_name=dataset_name,
            identity=stores.identity,
            stores=stores,
        )
        logger.info("mint[%s]: init_services=%.2fs", dataset_name, time.perf_counter() - _t0)

        minted = await mint_framed_cycle(
            session,
            declare(session, request.campaign_config(stores), purpose=request.purpose),
            session.bank.samples,
            campaign_id=fresh_campaign_id(session),
            task_text=request.task_text,
        )
        hop = CycleHop(campaign_id=minted.campaign_id, cycle_id=minted.cycle_id)
        land_job(stores, job_registry, job.job_id, hop, held.share)
        claim_cycle(stores, job_registry, job, hop)

    except BaseException as exc:
        release_slot(job_registry, job.job_id, exc, stores=stores, hop=hop)
        raise

    logger.info(
        "mint: minted %s/%s for user %s (job %s)",
        minted.campaign_id,
        minted.cycle_id,
        stores.identity.user_id,
        job.job_id,
    )
    return HeldRun.of(job_registry, job.job_id, minted, held)


def commit_upload_definition(
    stores: Stores, draft: DraftCampaign, *, source: dict[str, Any]
) -> None:
    """Called once the cut *source* names passed every mint refusal: a refused Start takes no slug."""
    pipeline = committed_pipeline_json(draft)
    stores.tenant_datasets.write_definition(
        draft.slug,
        source=source,
        pipeline=pipeline,
        campaign=default_campaign_block(draft),
        description=draft.raw_task_description,
        framing=draft_task_context(draft),
        origin=draft.committed_prompts(),
    )
    persist_origin_candidate_library(stores, draft.slug, draft)


def persist_origin_candidate_library(stores: Stores, slug: str, draft: DraftCampaign) -> None:
    """Tenant datasets only: a reopened repo benchmark is not ours to mutate."""
    if draft.candidate_library and stores.tenant_datasets.slug_exists(slug):
        stores.tenant_datasets.write_candidate_library(slug, draft.candidate_library)


async def hold_existing_cycle(
    request: ExistingCycle, *, stores: Stores, job_registry: JobRegistry, job: Job
) -> HeldRun:
    campaign, hop = request.campaign, request.hop
    try:
        held = await admit_and_hold(request, stores=stores, job_registry=job_registry, job=job)
        session, campaign_config, optimizer = await open_held_cycle(stores, campaign, hop)
    except BaseException as exc:
        # `hop=None`: a refusal stamps nothing on the cycle, so it stays as resumable as it was.
        release_slot(job_registry, job.job_id, exc, stores=stores, hop=None)
        raise
    return HeldRun(
        job_registry=job_registry,
        job_id=job.job_id,
        session=session,
        campaign_config=campaign_config,
        budget=held,
        optimizer=optimizer,
    )
