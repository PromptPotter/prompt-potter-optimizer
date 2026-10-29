from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from promptpotter.application.datasets.draft_campaign import DraftCampaign, owned_draft
from promptpotter.application.datasets.loaders import cut_bank
from promptpotter.application.initialization.wiring import init_services
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
    upload_source,
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
from promptpotter.application.jobs.quota import on_free_tier
from promptpotter.application.jobs.registry import UNRESOLVED_HOP
from promptpotter.application.pipeline_resolve import draft_config, draft_pipeline
from promptpotter.domain.campaign import Optimize
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.draft_store import DraftClaim
from promptpotter.infrastructure.store.stores import Stores

if TYPE_CHECKING:
    from promptpotter.application.jobs.registry import Job, JobRegistry
    from promptpotter.domain.campaign_config import CampaignConfig

logger = logging.getLogger(__name__)


class StartCheckinResponse(StrictModel):
    """What a Start answers: the job holding its slot, and the cycle it minted."""

    job_id: str
    hop: CycleHop | None = Field(
        description="Null while the job is queued: the campaign is minted once it holds a slot."
    )


def start_ready_draft(stores: Stores, draft_id: str) -> DraftCampaign:
    """The draft as Start reads it, every time it reads it: by id, under the claim."""
    draft = owned_draft(stores, draft_id)
    assert_origin_ready(draft)
    return draft


@dataclass(frozen=True, slots=True)
class CheckinStart:
    """Carries the claim, never the draft: the claim refuses every edit until the mint closes."""

    verb: ClassVar[str] = "start-checkin"

    claim: DraftClaim
    # The queue's label alone; every decision reads the draft.
    slug: str
    budget: Budget

    @property
    def hop(self) -> CycleHop:
        return UNRESOLVED_HOP

    @property
    def step_rounds(self) -> None:
        return None

    @property
    def dataset_name(self) -> str:
        return self.slug

    def backend_type(self, stores: Stores) -> str:
        return owned_draft(stores, self.claim.draft_id).connector

    def campaign_config(self, stores: Stores) -> CampaignConfig:
        return draft_config(stores, owned_draft(stores, self.claim.draft_id))


def _refuse_unrunnable_upload(stores: Stores, draft: DraftCampaign, source: dict[str, Any]) -> None:
    """Before the dataset lands, so a refused Start takes no slug and its retry is a Start like the first."""
    params, _rows, schema = draft_pipeline(stores, draft)
    refuse_unrunnable(
        draft_config(stores, draft),
        cut_bank(stores, source),
        params,
        schema,
        free=on_free_tier(stores),
    )


async def _commit_checkin(stores: Stores, draft_id: str) -> MintedCycle:
    draft = start_ready_draft(stores, draft_id)
    canonical = draft.source_dataset
    if canonical is None:
        if stores.tenant_datasets.slug_exists(draft.slug):
            raise LaunchError(
                f"slug collision at start: {draft.slug!r} already exists in this tenant's collection"
            )
        upload = stores.drafts.upload_of(draft.draft_id)
        if upload is None:
            raise LaunchError(f"draft {draft.draft_id} names no upload to commit")
        source = upload_source(draft, upload)
        _refuse_unrunnable_upload(stores, draft, source)
        commit_upload_definition(stores, draft, source=source)
        dataset_name = draft.slug
        origin_override = None
    else:
        persist_origin_candidate_library(stores, canonical, draft)
        dataset_name = canonical
        # An origin authored over an existing slug writes no dataset: only the override carries it.
        # `committed_prompts` rather than the raw map, or the label enumeration is lost.
        origin_override = (
            {step: NodePrompt(fields=f) for step, f in draft.committed_prompts().items()}
            if draft.authored_prompts()
            else None
        )

    session = await init_services(
        dataset_name=dataset_name, identity=stores.identity, stores=stores
    )
    campaign_config = draft_config(
        stores, draft, session_chain=[n.name for n in session.pipeline_schema.nodes]
    )
    plan = resolve_cycle_plan(
        session,
        declare(session, campaign_config, purpose=Optimize()),
        session.bank.samples,
        origin_override=origin_override,
    )
    ledger = stores.drafts.ledger(draft.draft_id)
    minted = mint(
        plan,
        campaign_id=fresh_campaign_id(session),
        billed=ledger if ledger.is_file() else None,
    )
    relabel_minted(minted.session)
    stores.drafts.close(draft.draft_id)
    return minted


async def hold_checkin_start(
    request: CheckinStart, *, stores: Stores, job_registry: JobRegistry, job: Job
) -> HeldRun:
    """Nothing before the slot is HELD touches the draft, and the claim ends with this Start however it ends."""
    hop: CycleHop | None = None
    try:
        held = await admit_and_hold(request, stores=stores, job_registry=job_registry, job=job)
        minted = await _commit_checkin(stores, request.claim.draft_id)
        hop = CycleHop(campaign_id=minted.campaign_id, cycle_id=minted.cycle_id)
        job_registry.update_target(job.job_id, hop=hop)
        claim_cycle(stores, job_registry, job, hop)
    except BaseException as exc:
        if isinstance(exc, BackendUnreachableError):
            exc.details["draft_id"] = request.claim.draft_id
        release_slot(job_registry, job.job_id, exc, stores=stores, hop=hop)
        raise
    finally:
        request.claim.release()

    logger.info("start-checkin: started %s/%s (job %s)", hop.campaign_id, hop.cycle_id, job.job_id)
    return HeldRun.of(job_registry, job.job_id, minted.session, held)
