from __future__ import annotations

from pydantic import Field

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.pipeline_resolve import (
    CampaignRunsWith,
    campaign_runs_with,
    resolve_root_config,
)
from promptpotter.application.runner.campaign_result import read_campaign_bench, read_line_spend
from promptpotter.application.runner.inner.tasks import is_self_optimization
from promptpotter.application.served_dashboard import binding_run_limits
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.campaign import (
    Campaign,
    LifecycleFilter,
    LifecycleStatus,
    ceiling_meter,
    comparison_line,
)
from promptpotter.domain.cycle_listing import (
    LineStanding,
    RunStatus,
    rounds_cap_note,
    rounds_line,
)
from promptpotter.domain.cycle_paths import CyclePath
from promptpotter.domain.spend import MeteredSpend
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.account_spend import LifetimeSpend, campaign_spend
from promptpotter.infrastructure.store.stores import Stores, descend_store, owned_campaign

__all__ = [
    "CampaignDetailResponse",
    "CampaignListResponse",
    "CampaignSummary",
    "campaign_detail",
    "list_campaigns",
    "owned_campaign",
]


class CampaignSummary(StrictModel):
    campaign_id: str = Field(description="Campaign id ({dataset}__{rand6}) — one RUN of an origin")
    dataset_name: str = Field(description="Dataset this campaign optimizes")
    label: str = Field(default="", description="Operator-supplied campaign label")
    display_name: str = Field(
        description="What every surface calls the campaign: its label, else its dataset."
    )
    id_suffix: str | None = Field(
        description=(
            "The id's `__xxxxxx` tail while it is all that tells one dataset's campaigns apart; "
            "null once the campaign carries a label, or where the id has no tail."
        )
    )
    line: LineStanding | None = Field(
        description=(
            "The campaign's line: the cycle answering for it now and how that cycle stands — its "
            "run state, its standing, its rounds and the cap binding them. Null while the root "
            "cycle is not on disk yet."
        )
    )
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(
        description=(
            "The newest activity on any of the campaign's cycles, `created_at` where none has "
            "any. The list is served in this order, newest first."
        )
    )
    root_cycle_id: str = Field(
        description=(
            "The campaign's root cycle id — `cycle_<root_content_hash>`, so it IS the "
            "campaign's ORIGIN identity. Campaigns on one declaration share it and differ "
            "only in the random `campaign_id` suffix, which is what makes them separate RUNS "
            "of that origin; the sidebar groups the forest by this key."
        )
    )
    backend_id: str = Field(default="", description="Backend this campaign optimizes against")
    backend_type: str = Field(
        default="",
        description=(
            "Connector KIND this campaign runs against ('termnorm' / 'promptpotter' / …), FROZEN "
            "on the manifest at mint. Re-pointing or deleting the dataset dir never changes it: a "
            "campaign outlives its dataset dir, and what it RAN is a fact about the campaign."
        ),
    )
    self_optimization: bool = Field(
        description=(
            "This campaign optimizes the optimizer itself (L4), answered off `backend_type` by "
            "the connector registry. The one test a surface draws the 'inner loops' disclosure "
            "and the self-optimization panel variants on — never a comparison of `backend_type`."
        ),
    )
    owner_user_id: str = Field(
        default="default", description="UserId of the operator who minted the campaign"
    )
    lifecycle_status: LifecycleStatus = Field(
        default="active",
        description="Operator visibility intent: 'active' (default sidebar), 'archived' (hidden), 'deleted' (soft-marked, data retained)",
    )
    lifecycle_changed_at: str = Field(
        default="", description="ISO 8601 timestamp of last lifecycle transition"
    )
    lifecycle_reason: str = Field(
        default="",
        description="Optional operator-supplied reason for the last lifecycle transition",
    )
    spend_lifetime: LifetimeSpend = Field(
        description=(
            "This campaign's share of `QuotaStatus.spend_lifetime`: every cycle's ledger, forks "
            "and forwarded L4 inner spend included, plus spend banked when one of its cycles was "
            "deleted. A wider scope than `spend_metered`, which reads the campaign's line alone."
        )
    )
    spend_metered: MeteredSpend = Field(
        description=(
            "What the campaign's spend cap counts along its LINE — the root and every cycle a "
            "rebase handed it to — by bucket: the bill, or the search's incurred USD for a "
            "controlled arm. The number a surface sets beside a cap, live."
        )
    )
    bench: BenchScore = Field(
        description=(
            "The headline (`architecture.md` § The bench score is not an optimizer's selection), "
            "read off the campaign's result (`result.json`) under the formula its line runs — "
            "whichever cycle rebases handed the line to. `status` says where it stands and "
            "whether its pass may be asked for; `selected` is null until the line grades its pick."
        )
    )
    runs_with: CampaignRunsWith | None = Field(
        description=(
            "What the ROOT course runs with — a second transport of the answer "
            "`GET /campaigns/{id}/pipeline` gives at the root, never a second source. Null when "
            "the root pipeline did not resolve. `max_rounds` is the DECLARED rounds cap; 0 means "
            "origin only."
        )
    )
    comparison: str = Field(
        description=(
            "Whether the campaign is a CONTROLLED arm of a head-to-head (`campaign.json::arm`, "
            "frozen at mint), as one sentence (`domain/campaign.py::comparison_line`): an arm "
            "reads no other campaign's measurements, refuses a steer and spends its declared "
            "budget; an ordinary campaign optimizes with everything that helps."
        )
    )


class CampaignListResponse(StrictModel):
    campaigns: list[CampaignSummary] = Field(description="List of campaign summaries")
    total: int = Field(description="Total number of campaigns")


class CampaignDetailResponse(CampaignSummary):
    root_content_hash: str | None = Field(
        description="Content hash of the origin search point — the campaign identity; null on "
        "an unstarted check-in"
    )
    config: CampaignConfig = Field(
        description=(
            "The config the campaign's root runs under — its draft's while it is still authoring, "
            "and for an arm the manifest with its head-to-head record's split and budget laid on."
        )
    )


def _line(
    stores: Stores, campaign: Campaign, runs_with: CampaignRunsWith | None
) -> LineStanding | None:
    holder = stores.campaigns.line_holder(campaign.root_hop)
    index = stores.campaigns.load(holder)
    if index is None:
        return None
    run = derive_run_state(stores.campaigns.cycle_dir(holder))
    limits = binding_run_limits(stores, holder, campaign, run)
    max_rounds = (
        (None if runs_with is None else runs_with.max_rounds)
        if limits is None
        else limits.max_rounds
    )
    return LineStanding(
        holder=holder,
        status=RunStatus.of(run.run_phase, index.stop_reason, lifecycle=campaign.lifecycle_status),
        run_phase=run.run_phase,
        producer_attached=run.producer.attached,
        stop_reason=index.stop_reason,
        standing=index.standing,
        rounds_closed=index.rounds_closed,
        max_rounds=max_rounds,
        rounds_line=rounds_line(index.rounds_closed, max_rounds),
        rounds_cap_note=rounds_cap_note(max_rounds),
        human_intervened=index.human_intervened,
    )


def _updated_at(stores: Stores, campaign: Campaign) -> str:
    stamps = (
        index.updated_at
        for cycle_dir in stores.campaigns.campaign_cycle_dirs(campaign.campaign_id)
        if (index := read_cycle_index(cycle_dir)) is not None
    )
    return max(stamps, default=campaign.created_at) or campaign.created_at


def _summary(campaign: Campaign, stores: Stores) -> CampaignSummary:
    runs_with = campaign_runs_with(stores, campaign)
    tail = campaign.campaign_id.rpartition("__")[2] if "__" in campaign.campaign_id else ""
    return CampaignSummary(
        campaign_id=campaign.campaign_id,
        dataset_name=campaign.dataset_name,
        label=campaign.label,
        display_name=campaign.label or campaign.dataset_name or campaign.campaign_id,
        id_suffix=None if campaign.label or not tail else tail,
        line=_line(stores, campaign, runs_with),
        created_at=campaign.created_at,
        updated_at=_updated_at(stores, campaign),
        root_cycle_id=campaign.root_cycle_id,
        backend_id=campaign.backend_id,
        backend_type=campaign.backend_type,
        self_optimization=is_self_optimization(campaign.backend_type),
        owner_user_id=campaign.owner_user_id,
        lifecycle_status=campaign.lifecycle_status,
        lifecycle_changed_at=campaign.lifecycle_changed_at,
        lifecycle_reason=campaign.lifecycle_reason,
        spend_lifetime=LifetimeSpend.of(campaign_spend(stores.campaigns, campaign.campaign_id)),
        spend_metered=MeteredSpend.of(
            read_line_spend(stores, campaign), ceiling_meter(campaign.arm)
        ),
        bench=read_campaign_bench(stores, campaign),
        runs_with=runs_with,
        comparison=comparison_line(campaign.arm),
    )


def list_campaigns(
    stores: Stores, *, inside: CyclePath, dataset: str | None, lifecycle: LifecycleFilter
) -> CampaignListResponse:
    leaf = descend_store(stores, inside)
    campaigns = leaf.campaigns.list_campaigns(
        dataset, lifecycle=lifecycle, owner_user_id=str(leaf.identity.user_id)
    )
    summaries = [_summary(c, leaf) for c in campaigns]
    summaries.sort(key=lambda s: s.updated_at, reverse=True)
    return CampaignListResponse(campaigns=summaries, total=len(summaries))


def campaign_detail(stores: Stores, campaign_id: str) -> CampaignDetailResponse:
    campaign = owned_campaign(stores, campaign_id)
    return CampaignDetailResponse(
        **_summary(campaign, stores).model_dump(),
        root_content_hash=campaign.root_content_hash,
        config=resolve_root_config(stores, campaign),
    )
