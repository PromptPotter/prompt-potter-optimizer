from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from promptpotter.application.datasets.authored import (
    load_dataset_campaign_config,
    with_optimization,
)
from promptpotter.application.datasets.draft_build import draft_task_context
from promptpotter.application.datasets.origin_readiness import FieldGap, origin_readiness
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.coupon import Send
from promptpotter.application.jobs.launcher.admission import (
    RunStart,
    admit_and_hold,
    campaign_sends,
    claim_cycle,
)
from promptpotter.application.jobs.launcher.run_job import HeldRun, open_held_cycle
from promptpotter.application.jobs.mint import (
    declare,
    fresh_campaign_id,
    mint_framed_cycle,
)
from promptpotter.application.jobs.quota import land_job, sends_on
from promptpotter.application.pipeline_resolve import cycle_config, draft_declared
from promptpotter.domain.campaign import Campaign, Optimize
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.checkin_draft import DraftFile
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import UNRESOLVED_HOP
from promptpotter.domain.spend import Budget, HeldBudget
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    own_definition,
    resolve_dataset,
    runnable_definition,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.application.pipeline_resolve import DraftPipeline

logger = logging.getLogger(__name__)


class OriginIncompleteError(PayloadInvalidError):
    code = "origin_incomplete"

    def __init__(self, gaps: tuple[FieldGap, ...]) -> None:
        self.gaps = gaps
        fields = ", ".join(gap.field for gap in gaps) or "<none>"
        super().__init__(
            f"origin incomplete, nothing minted — unresolved fields: {fields}",
            details={"gaps": [gap.model_dump() for gap in gaps]},
        )


def assert_origin_ready(draft: DraftFile, pipeline: DraftPipeline) -> None:
    readiness = origin_readiness(draft, pipeline)
    if not readiness.complete:
        raise OriginIncompleteError(readiness.gaps)


@dataclass(frozen=True, slots=True)
class FreshMint:
    verb: ClassVar[str] = "mint"

    # Resolved once, where the request is built (`commands/launching.py::mint_campaign`).
    dataset: ResolvedDataset
    budget: Budget
    optimization: Mapping[str, Any]
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
        return runnable_definition(self.dataset).backend_type

    def campaign_config(self) -> CampaignConfig:
        declared = self.declared or load_dataset_campaign_config(self.dataset)
        return with_optimization(declared, self.optimization)

    def declared_budget(self, _stores: Stores) -> Budget:
        return self.campaign_config().optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return sends_on(stores, self.campaign_config(), self.backend_type(stores))


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
        return runnable_definition(resolve_dataset(stores, self.dataset_name)).backend_type

    def declared_budget(self, stores: Stores) -> Budget:
        return cycle_config(stores, self.campaign, self.hop).config.optimization.budget

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return campaign_sends(stores, self.hop)


def land_and_claim(start: RunStart, hop: CycleHop, held: HeldBudget) -> None:
    """A job onto the cycle its hold just minted, the claim taken off the job as it stands now:
    promoted out of the queue, its stage the one the cycle reads."""
    stores, job_registry, job_id = start.stores, start.job_registry, start.job_id
    land_job(stores, job_registry, job_id, hop, held.share)
    promoted = job_registry.get(job_id)
    if promoted is None:
        raise RuntimeError(f"job {job_id} was cleared while its cycle was minted")
    claim_cycle(stores, job_registry, promoted, hop)


async def hold_fresh_mint(request: FreshMint, start: RunStart) -> HeldRun:
    """Admit, then session, then mint: nothing is written before admission, so a refusal leaves none."""
    dataset_name, stores = request.dataset_name, start.stores
    # Bound only after the mint, so the failure path knows whether a cycle exists to stamp.
    hop: CycleHop | None = None
    try:
        held = await admit_and_hold(request, start)

        _t0 = time.perf_counter()
        session = await init_services(
            dataset_name=dataset_name,
            identity=stores.identity,
            stores=stores,
        )
        logger.info("mint[%s]: init_services=%.2fs", dataset_name, time.perf_counter() - _t0)

        minted = await mint_framed_cycle(
            session,
            declare(session, request.campaign_config(), purpose=Optimize()),
            session.bank.samples,
            campaign_id=fresh_campaign_id(session),
            task_text=request.task_text,
        )
        hop = CycleHop(campaign_id=minted.campaign_id, cycle_id=minted.cycle_id)
        land_and_claim(start, hop, held)
        logger.info(
            "mint: minted %s/%s for user %s (job %s)",
            minted.campaign_id,
            minted.cycle_id,
            stores.identity.user_id,
            start.job_id,
        )
        return HeldRun.of(start, minted, held)
    except BaseException as exc:
        start.release(exc, stamps=hop)
        raise


def commit_upload_definition(
    stores: Stores,
    draft: DraftFile,
    *,
    source: dict[str, Any],
    pipeline: DraftPipeline,
) -> None:
    """Called once the cut *source* names passed every mint refusal: a refused Start takes no slug.
    Writes the two halves its check-in read: the definition *pipeline* laid, and the campaign
    half (no free tier's layer, which a Start lays per account)."""
    stores.write_own_definition(
        draft.slug,
        source=source,
        pipeline=pipeline.local,
        campaign=draft_declared(stores, draft).model_dump(mode="json", exclude_defaults=True),
        description=draft.confirmed_task(),
        framing=draft_task_context(draft),
        origin=draft.committed_prompts(pipeline.chain),
    )
    persist_origin_candidate_library(stores, draft.slug, draft)


def persist_origin_candidate_library(stores: Stores, slug: str, draft: DraftFile) -> None:
    """Tenant datasets only: a reopened repo benchmark is not ours to mutate."""
    if draft.candidate_library and own_definition(stores, slug) is not None:
        stores.tenant_datasets.write_candidate_library(slug, draft.candidate_library)


async def hold_existing_cycle(request: ExistingCycle, start: RunStart) -> HeldRun:
    try:
        held = await admit_and_hold(request, start)
        session, cycle, optimizer = await open_held_cycle(
            start.stores, request.campaign, request.hop
        )
    except BaseException as exc:
        # Unstamped: a refusal writes nothing on the cycle, so it stays as resumable as it was.
        start.release(exc)
        raise
    return HeldRun(start, session, cycle, held, optimizer)
