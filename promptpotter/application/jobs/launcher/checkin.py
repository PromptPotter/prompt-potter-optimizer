from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from promptpotter.application.datasets.draft_campaign import DraftCampaign, load_checkin_draft
from promptpotter.application.datasets.origin_readiness import save_checkin_draft
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import (
    admit_and_hold,
    release_slot,
)
from promptpotter.application.jobs.launcher.mint_and_start import (
    LaunchError,
    assert_origin_ready,
    materialize_and_write_origin,
    persist_origin_candidate_library,
)
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.mint import (
    finalize_checkin_to_active,
    mint_checkin_skeleton,
    resolve_cycle_plan,
    write_plan_seed,
)
from promptpotter.application.pipeline_resolve import checkin_campaign_config
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.launch_limits import LaunchLimits
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.checkin_draft_store import dataset_source_of
from promptpotter.infrastructure.store.stores import Stores, owned_campaign
from promptpotter.shared.identity import CAMPAIGN_CREATE_CAP, require_capability

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.registry import Job, JobRegistry

logger = logging.getLogger(__name__)


def create_checkin_campaign(
    stores: Stores,
    *,
    draft: DraftCampaign,
    bank_items: list[dict[str, Any]],
    source_file: str = "",
    headers: tuple[str, ...] = (),
) -> tuple[str, str, DraftCampaign]:
    # Gated HERE, at the mint, not on the routes above it: every ingress reaches this one function.
    require_capability(stores.identity, CAMPAIGN_CREATE_CAP, subject="check-in mint")
    hop = mint_checkin_skeleton(stores, slug=draft.slug, backend_type=draft.connector)
    campaign_id = hop.campaign_id
    # The bank lands FIRST: ``write_resolution`` patches ``cache.json`` and no-ops without one.
    stores.checkin.write_bank(
        campaign_id, bank_items, source_file=source_file or draft.source_file, headers=headers
    )
    return campaign_id, hop.cycle_id, save_checkin_draft(stores, draft.patch(draft_id=campaign_id))


class StartCheckinResponse(StrictModel):
    """What a Start answers: the campaign, the cycle its loop runs and the job holding its slot."""

    campaign_id: str
    cycle_id: str
    job_id: str


def load_checkin_for_start(stores: Stores, campaign_id: str) -> tuple[CycleHop, DraftCampaign]:
    campaign = owned_campaign(stores, campaign_id)
    if not stores.campaigns.in_checkin(campaign.root_hop):
        raise LaunchError(f"campaign {campaign_id} is not in check-in — its origin is committed")
    draft = load_checkin_draft(stores, campaign_id)
    if draft is None:
        raise LaunchError(f"campaign {campaign_id} has no check-in working state to start")
    assert_origin_ready(draft)
    return campaign.root_hop, draft


@dataclass(frozen=True, slots=True)
class CheckinStart:
    verb: ClassVar[str] = "start-checkin"

    hop: CycleHop
    draft: DraftCampaign
    limits: LaunchLimits
    backend_url: str
    backend_id: str

    @property
    def dataset_name(self) -> str:
        return self.draft.slug

    def backend(self, stores: Stores) -> tuple[str, str]:
        return self.draft.connector, self.backend_url

    def campaign_config(self, stores: Stores) -> CampaignConfig:
        return checkin_campaign_config(stores, self.draft)


async def _commit_checkin(stores: Stores, request: CheckinStart) -> Session:
    hop, draft = request.hop, request.draft
    canonical = dataset_source_of(draft.source_file)
    if canonical is None:
        if stores.tenant_datasets.slug_exists(draft.slug):
            raise LaunchError(
                f"slug collision at start: {draft.slug!r} already exists in this tenant's collection"
            )
        bank = stores.checkin.load_bank(hop.campaign_id)
        if bank is None:
            raise LaunchError(f"campaign {hop.campaign_id} has no sample bank to materialize")
        materialize_and_write_origin(stores, draft, bank_items=list(bank["items"]))
        dataset_name = draft.slug
        origin_override = None
    else:
        persist_origin_candidate_library(stores, canonical, draft)
        dataset_name = canonical
        # An origin authored over an existing slug writes no dataset: only the override carries it.
        authored = any(str(value).strip() for value in draft.origin_prompt_fields.values())
        # `committed_prompt_fields` rather than the raw dict, or the label enumeration is lost.
        origin_override = draft.committed_prompt_fields() if authored else None

    session = await init_services(
        backend_url=request.backend_url,
        backend_id=request.backend_id,
        dataset_name=dataset_name,
        identity=stores.identity,
        stores=stores,
    )

    campaign_config = checkin_campaign_config(
        stores, draft, session_chain=[n.name for n in session.pipeline_schema.nodes]
    )

    plan = resolve_cycle_plan(
        session, campaign_config, session.samples, origin_override=origin_override
    )

    finalize_checkin_to_active(session, campaign_config, hop=hop, cycle_plan=plan)
    write_plan_seed(stores, hop, plan)
    return session


async def hold_checkin_start(
    request: CheckinStart, *, stores: Stores, job_registry: JobRegistry, job: Job
) -> HeldRun:
    """Nothing before the slot is HELD touches the campaign: a refusal leaves the check-in as it was."""
    hop = request.hop
    # The cycle a failure stamps: none until admission passed, since nothing before it wrote one.
    committing: CycleHop | None = None
    try:
        held = await admit_and_hold(request, stores=stores, job_registry=job_registry, job=job)
        committing = hop
        session = await _commit_checkin(stores, request)
    except BaseException as exc:
        # `_commit_checkin` flips to `active` before its last await: an interrupt stamps the CYCLE too.
        release_slot(job_registry, job.job_id, exc, stores=stores, hop=committing)
        raise

    logger.info("start-checkin: started %s/%s (job %s)", hop.campaign_id, hop.cycle_id, job.job_id)
    return HeldRun.of(job_registry, job.job_id, session, held)


__all__ = [
    "CheckinStart",
    "StartCheckinResponse",
    "create_checkin_campaign",
    "hold_checkin_start",
    "load_checkin_for_start",
]
