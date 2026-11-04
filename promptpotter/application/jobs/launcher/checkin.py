from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field, JsonValue

from promptpotter.application.datasets.authored import with_optimization
from promptpotter.application.datasets.draft_campaign import (
    DatasetBasis,
    DraftCampaign,
    owned_draft,
)
from promptpotter.application.datasets.loaders import Source, cut_bank, definition_source
from promptpotter.application.datasets.startability import upload_source
from promptpotter.application.free_tier import free_fence
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.coupon import Send
from promptpotter.application.jobs.launcher.admission import (
    admit_and_hold,
    claim_cycle,
    release_slot,
)
from promptpotter.application.jobs.launcher.mint_and_start import (
    LaunchError,
    assert_origin_ready,
    commit_upload_definition,
    persist_origin_candidate_library,
)
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.mint import (
    MintedCycle,
    declare,
    fresh_campaign_id,
    mint,
    refuse_unrunnable,
    relabel_minted,
    resolve_cycle_plan,
)
from promptpotter.application.jobs.quota import land_job
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import (
    DraftPipeline,
    ServedPipeline,
    draft_backend_type,
    draft_budget,
    draft_pipeline,
    draft_sends,
    draft_served,
)
from promptpotter.domain.campaign import Optimize
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import UNRESOLVED_HOP
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.draft_store import DraftClaim
from promptpotter.infrastructure.store.stores import Stores

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.job import Job

logger = logging.getLogger(__name__)


class StartCheckinResponse(StrictModel):
    """What a Start answers: the job holding its slot, and the cycle it minted."""

    job_id: str
    hop: CycleHop | None = Field(
        description="Null while the job is queued: the campaign is minted once it holds a slot."
    )


@dataclass(frozen=True, slots=True)
class CheckinStart:
    """Carries the claim, never the draft: the claim refuses every edit until the mint closes."""

    verb: ClassVar[str] = "start-checkin"

    claim: DraftClaim
    # The queue's label alone (`DraftCampaign.dataset_name`); every decision reads the draft.
    dataset_name: str
    budget: Budget

    @property
    def hop(self) -> CycleHop:
        return UNRESOLVED_HOP

    @property
    def step_rounds(self) -> None:
        return None

    @property
    def draft_id(self) -> str:
        return self.claim.draft_id

    def backend_type(self, stores: Stores) -> str:
        return draft_backend_type(stores, owned_draft(stores, self.claim.draft_id))

    def declared_budget(self, stores: Stores) -> Budget:
        return draft_budget(stores, owned_draft(stores, self.claim.draft_id))

    def sends(self, stores: Stores) -> tuple[Send, ...]:
        return draft_sends(stores, owned_draft(stores, self.claim.draft_id))


def _land_draft(
    stores: Stores,
    draft: DraftCampaign,
    pipeline: DraftPipeline,
    overlays: Sequence[Mapping[str, JsonValue]],
) -> dict[str, NodePrompt] | None:
    """An upload's definition lands once its cut passed every mint refusal of each config the
    Start runs (the draft's, under each overlay), so a refused Start takes no slug. One its own
    upload landed is a Start refused after landing: the retry lands the draft again. Answers the
    origin an authored check-in over an existing dataset overrides."""
    params, chain = pipeline.params, pipeline.chain
    basis = draft.basis
    if isinstance(basis, DatasetBasis):
        persist_origin_candidate_library(stores, basis.dataset, draft)
        # An origin authored over an existing slug writes no dataset: only the override carries it.
        # `committed_prompts` rather than the raw map, or the label enumeration is lost.
        return (
            {step: NodePrompt(fields=f) for step, f in draft.committed_prompts(chain).items()}
            if draft.authored_prompts()
            else None
        )
    source = upload_source(draft, basis)
    datasets = stores.tenant_datasets
    if datasets.slug_exists(draft.slug) and definition_source(
        datasets.dataset_dir(draft.slug)
    ) != Source.of(source):
        raise LaunchError(
            f"slug collision at start: {draft.slug!r} already exists in this tenant's collection"
        )
    rows = cut_bank(stores, source)
    for overlay in overlays:
        config = with_optimization(pipeline.config, overlay)
        refuse_unrunnable(
            config,
            select_optimizer(config.optimization),
            rows,
            params,
            chain,
            fence=free_fence(stores, basis.connector),
            shelf=stores.block_packs,
        )
    commit_upload_definition(stores, draft, basis, source=source, chain=chain)
    return None


@dataclass(frozen=True, slots=True)
class OpenedCheckin:
    """A Start's draft past its landing, with the session and config every arm of it declares on."""

    draft: DraftCampaign
    session: Session
    declared: CampaignConfig
    origin_override: dict[str, NodePrompt] | None


async def open_checkin(
    stores: Stores,
    draft_id: str,
    served: ServedPipeline,
    *,
    overlays: Sequence[Mapping[str, JsonValue]],
) -> OpenedCheckin:
    """*overlays* are what each campaign the Start mints lays over the draft's `optimization`;
    *served* is the Start's one backend read, taken once its slot is held."""
    draft = owned_draft(stores, draft_id)
    pipeline = draft_pipeline(stores, draft, served)
    assert_origin_ready(draft, pipeline)
    origin_override = _land_draft(stores, draft, pipeline, overlays)
    session = await init_services(
        dataset_name=draft.dataset_name,
        identity=stores.identity,
        stores=stores,
        served=served,
    )
    return OpenedCheckin(draft, session, pipeline.config, origin_override)


async def _commit_checkin(stores: Stores, request: CheckinStart) -> MintedCycle:
    draft_id = request.claim.draft_id
    served = await draft_served(stores, owned_draft(stores, draft_id))
    opened = await open_checkin(stores, draft_id, served, overlays=[{}])
    session = opened.session
    plan = resolve_cycle_plan(
        session,
        declare(session, opened.declared, purpose=Optimize()),
        session.bank.samples,
        origin_override=opened.origin_override,
    )
    ledger = stores.drafts.ledger(draft_id)
    minted = mint(
        plan,
        campaign_id=fresh_campaign_id(session),
        billed=ledger if ledger.is_file() else None,
    )
    relabel_minted(minted.session)
    stores.drafts.close(draft_id)
    return minted


async def hold_checkin_start(
    request: CheckinStart, *, stores: Stores, job_registry: JobRegistry, job: Job
) -> HeldRun:
    """Nothing before the slot is HELD touches the draft, and the claim ends with this Start however it ends."""
    hop: CycleHop | None = None
    try:
        held = await admit_and_hold(request, stores=stores, job_registry=job_registry, job=job)
        minted = await _commit_checkin(stores, request)
        hop = CycleHop(campaign_id=minted.campaign_id, cycle_id=minted.cycle_id)
        land_job(stores, job_registry, job.job_id, hop, held.share)
        claim_cycle(stores, job_registry, job, hop)
    except BaseException as exc:
        release_slot(job_registry, job.job_id, exc, stores=stores, hop=hop)
        raise
    finally:
        request.claim.release()

    logger.info("start-checkin: started %s/%s (job %s)", hop.campaign_id, hop.cycle_id, job.job_id)
    return HeldRun.of(job_registry, job.job_id, minted, held)
