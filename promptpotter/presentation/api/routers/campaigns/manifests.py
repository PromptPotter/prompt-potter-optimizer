"""Real Campaign manifests, not the cycles under them."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Query, Request, Response
from pydantic import Field

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.config_map import ConfigMapResponse, config_map
from promptpotter.application.datasets.draft_campaign import load_checkin_draft
from promptpotter.application.evidence.subjects import SubjectSpec, parse_subject
from promptpotter.application.jobs.launcher.draft_build import draft_wire
from promptpotter.application.pipeline_resolve import (
    CampaignPipelineResponse,
    CampaignRunsWith,
    campaign_runs_with,
    resolve_pipeline_for_campaign,
    resolve_root_config,
)
from promptpotter.application.runner.campaign_result import read_campaign_bench, read_line_spend
from promptpotter.application.runner.inner.tasks import is_self_optimization
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.campaign import (
    Arm,
    Campaign,
    LifecycleFilter,
    LifecycleStatus,
    ceiling_meter,
)
from promptpotter.domain.pipeline_overlay import (
    permitted_models_for_campaign,
    steers_disallowed_model,
)
from promptpotter.domain.spend import MeteredSpend
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.account_spend import campaign_spend
from promptpotter.infrastructure.store.stores import Stores, descend_store
from promptpotter.presentation.api.deps import StoresDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._conditional import conditional_json
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import BadRequestError, NotFoundError


class CampaignSummary(StrictModel):
    campaign_id: str = Field(description="Campaign id ({dataset}__{rand6}) — one RUN of an origin")
    dataset_name: str = Field(description="Dataset this campaign optimizes")
    label: str = Field(default="", description="Operator-supplied campaign label")
    # Deliberately no run-state field: a campaign's is its answering cycle's `run_phase`.
    created_at: str = Field(description="ISO 8601 creation timestamp")
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
    spend_used_usd: float = Field(
        description=(
            "What this campaign has billed over its whole life — every cycle's ledger, forks and "
            "forwarded L4 inner spend included, plus spend banked when one of its cycles was "
            "deleted. Its share of `QuotaStatus.spend_used_total_usd`. A FLOOR while "
            "`spend_unpriced_tokens` is non-zero."
        )
    )
    spend_unpriced_tokens: int = Field(
        description=(
            "Billed tokens with no resolvable rate, so `spend_used_usd` cannot see them. Zero "
            "means the dollar figure is complete."
        )
    )
    spend_unreported_usd: float = Field(
        description=(
            "The most that this campaign's sends which ended with no bill may have cost, at the "
            "bounds they were admitted on — unknown, never spent. Its share of "
            "`QuotaStatus.spend_unreported_usd`."
        )
    )
    spend_metered: MeteredSpend = Field(
        description=(
            "What the campaign's spend cap counts along its LINE — the root and every cycle a "
            "rebase handed it to — by bucket: the bill, or the search's incurred USD for a "
            "controlled arm. The number a surface sets beside a cap, live."
        )
    )
    bench: BenchScore | None = Field(
        description=(
            "The headline (`architecture.md` § The bench score is not an optimizer's selection), "
            "read off the campaign's result (`result.json`) under the formula its line runs — "
            "whichever cycle rebases handed the line to. `selected` is null until the line grades "
            "its pick. Null until the line first banks one, and where the split holds nothing out "
            "it says so in `missing_reason`."
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
    arm: Arm | None = Field(
        description=(
            "The head-to-head this campaign runs as a CONTROLLED arm of (`campaign.json::arm`, "
            "frozen at mint): it reads no other campaign's measurements, refuses a steer and "
            "spends its declared budget. Null for an ordinary campaign, which optimizes with "
            "everything that helps."
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


def _campaign_summary(campaign: Campaign, stores: Stores) -> CampaignSummary:
    spent = campaign_spend(stores.campaigns, campaign.campaign_id)
    return CampaignSummary(
        campaign_id=campaign.campaign_id,
        dataset_name=campaign.dataset_name,
        label=campaign.label,
        created_at=campaign.created_at,
        root_cycle_id=campaign.root_cycle_id,
        backend_id=campaign.backend_id,
        backend_type=campaign.backend_type,
        self_optimization=is_self_optimization(campaign.backend_type),
        owner_user_id=campaign.owner_user_id,
        lifecycle_status=campaign.lifecycle_status,
        lifecycle_changed_at=campaign.lifecycle_changed_at,
        lifecycle_reason=campaign.lifecycle_reason,
        spend_used_usd=round(spent.used_usd, 6),
        spend_unpriced_tokens=spent.unpriced_tokens,
        spend_unreported_usd=round(spent.unreported_usd, 6),
        spend_metered=MeteredSpend.of(
            read_line_spend(stores, campaign), ceiling_meter(campaign.arm)
        ),
        bench=read_campaign_bench(stores, campaign),
        runs_with=campaign_runs_with(stores, campaign),
        arm=campaign.arm,
    )


@campaigns_router.get("/campaigns", response_model=CampaignListResponse)
def list_campaigns(
    request: Request,
    stores: StoresDep,
    dataset: str | None = Query(default=None, description="Filter to one dataset"),
    lifecycle: Annotated[
        LifecycleFilter,
        Query(
            description="'active' (default), 'archived' or 'deleted' — the visibility intent; "
            "'checkin' — the authoring phase, asked of the root cycle's flag; or 'all'",
        ),
    ] = "active",
    descend: str | None = Query(None),
) -> Response:
    """Every campaign in one store owned by the caller, newest first.

    Filters: optional ``?dataset=`` for one dataset, ``?lifecycle=`` for the
    visibility intent (defaults to ``active``; ``archived`` and ``deleted`` drop
    out of the default surface). A check-in campaign IS ``active`` — origin
    authoring is resumable progress, so it belongs in the sidebar beside running
    work — and ``?lifecycle=checkin`` narrows to that phase by asking the root
    cycle's flag. This route unions nothing. Cross-user campaigns are invisible —
    the ``owner_user_id`` gate filters on ``store.identity.user_id``.

    ``descend`` names the chain of cycles to descend INTO (see ``GET /cycles``);
    absent/empty is the tenant's own tree. It is the twin of the cycle list: a
    forest is campaigns × cycles, and the sidebar groups runs by
    ``root_cycle_id`` (their origin), so BOTH lists must be available at every
    depth for one tree builder to serve L4, L5, and the top level alike.
    """
    leaf = descend_store(stores, decode_descend(descend))
    owner = str(leaf.identity.user_id)
    campaigns = leaf.campaigns.list_campaigns(dataset, lifecycle=lifecycle, owner_user_id=owner)
    campaigns.sort(key=lambda c: c.created_at, reverse=True)
    return conditional_json(
        request,
        CampaignListResponse(
            campaigns=[_campaign_summary(c, leaf) for c in campaigns],
            total=len(campaigns),
        ),
    )


@campaigns_router.get("/campaigns/{campaign_id}/checkin")
def get_campaign_checkin(stores: StoresDep, campaign_id: str) -> dict[str, Any]:
    """Re-open a durable check-in campaign — its draft wire + last resolver turn.

    The sidebar opens a ``checkin``-lifecycle campaign through here instead of the
    dashboard (no ``dashboard.json`` exists pre-loop): the webapp rebuilds the
    ingest "ready" panel from ``draft`` and shows the prior ``resolution`` recap.
    Tenant-scoped (the check-in store is rooted at the tenant dir) — a cross-tenant
    id 404s. 404 when this campaign has no check-in working state (already Started
    or never a check-in). Wire contract pinned in
    ``docs/specs/api-openapi.yaml::GET /campaigns/{id}/checkin``.
    """
    draft = load_checkin_draft(stores, campaign_id)
    if draft is None:
        raise NotFoundError(
            f"No check-in working state for campaign {campaign_id}",
            code="command_target_not_found",
        )
    bank = stores.checkin.load_bank(campaign_id) or {}
    block = bank.get("resolution") or {}
    return {
        "draft": draft_wire(draft, stores.base_dir),
        "resolution": block.get("last_resolution"),
        # Proposals the last turn left unclicked. Without these a re-opened check-in
        # would drop the operator's outstanding actions on the floor.
        "raised": block.get("raised") or [],
    }


@campaigns_router.get("/campaigns/{campaign_id}", response_model=CampaignDetailResponse)
def get_campaign(stores: StoresDep, campaign_id: str) -> CampaignDetailResponse:
    """Campaign manifest detail. 404 on cross-user reads."""
    # Cross-user reads return 404 (not 403) — existence leakage is itself a
    # violation. Ownership rule lives in `load_owned`.
    campaign = stores.campaigns.load_owned(campaign_id, str(stores.identity.user_id))
    if campaign is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    return CampaignDetailResponse(
        **_campaign_summary(campaign, stores).model_dump(),
        root_content_hash=campaign.root_content_hash,
        config=resolve_root_config(stores, campaign),
    )


@campaigns_router.get("/campaigns/{campaign_id}/pipeline", response_model=CampaignPipelineResponse)
def get_campaign_pipeline(
    request: Request,
    stores: StoresDep,
    campaign_id: str,
    at: str = Query(
        default="",
        description="Searchpoint subject (`parse_subject` grammar); defaults to the campaign root",
    ),
) -> Response:
    """What this campaign RUNS — the one server-owned answer (`frontend-surface-contract.md::I9`).
    Ownership-gated by `load_owned`, 404 on cross-tenant: this body carries the operator's own
    model choices."""
    spec = _pipeline_subject(at, campaign_id)
    # An L4 inner searchpoint's MANIFEST lives in the sandbox `;in=` names; read from the tenant
    # tree, the OUTER campaign of the same id answered for it.
    leaf = descend_store(stores, spec.inside) if spec.inside else stores
    campaign = leaf.campaigns.load_owned(campaign_id, str(leaf.identity.user_id))
    if campaign is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    return conditional_json(request, resolve_pipeline_for_campaign(leaf, campaign, at=spec))


class ForkPreviewRequest(StrictModel):
    pipeline_overlay: dict[str, Any] = Field(
        description="The `nodes.*.config` overlay the fork would carry, as its `CycleSeed` sends it"
    )


class ForkPreviewResponse(StrictModel):
    """What `POST /commands/fork-cycle` would decide about this steer, asked without forking."""

    steers_disallowed_model: bool = Field(
        description=(
            "The overlay picks a responder the campaign's frozen `optimizer_narrowing` never "
            "sanctioned, or touches a cost lever, which no permitted set can sanction. True means "
            "the fork is the ADR-0005 babysit act: it needs `campaign.babysit` (404 without it) "
            "and stamps the branch grade C."
        )
    )
    permitted_models: dict[str, list[str]] = Field(
        description=(
            "What the verdict above was compared AGAINST, per node — this campaign's frozen "
            "`optimizer_narrowing[node].param_allowed_values.model`. Served beside the verdict so "
            "a surface naming the permitted models cannot name a different set than the one that "
            "decided. A node absent from it sanctions nothing, which is why any model steer there "
            "counts."
        )
    )


@campaigns_router.post("/campaigns/{campaign_id}/fork-preview", response_model=ForkPreviewResponse)
def preview_fork_steer(
    stores: StoresDep, campaign_id: str, body: ForkPreviewRequest
) -> ForkPreviewResponse:
    """Would this steer take the babysit path? — the fork gate's own verdict, asked without forking.

    A READ despite the POST: the subject is an overlay that exists nowhere on disk yet, so it
    cannot be a query string. Nothing is written — no `CommandRecord`, no ack — which is why this
    is its own endpoint rather than a `dry_run` flag on `fork-cycle`. Capability-free: it reports
    what the gate WOULD say, and `campaign.babysit` is what decides whether the fork lands.
    """
    campaign = stores.campaigns.load_owned(campaign_id, str(stores.identity.user_id))
    if campaign is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    return ForkPreviewResponse(
        steers_disallowed_model=steers_disallowed_model(campaign.config, body.pipeline_overlay),
        permitted_models=permitted_models_for_campaign(campaign.config),
    )


def _pipeline_subject(at: str, campaign_id: str) -> SubjectSpec:
    """One grammar (`parse_subject`), narrowed by two refusals: a scoring mask cannot change what
    config a point RAN, and an `at` naming another campaign than the path is two subjects."""
    if not at:
        return SubjectSpec("campaign", campaign_id)
    try:
        spec = parse_subject(at)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    if spec.lens or spec.samples:
        raise BadRequestError(
            f"Subject {at!r} carries a scoring mask, and a mask cannot change what config a "
            "point RAN. Drop `lens=` / `samples=` — this read is configuration, not scoring."
        )
    if spec.campaign_id != campaign_id:
        raise BadRequestError(
            f"Subject {at!r} addresses campaign {spec.campaign_id!r} but the path names "
            f"{campaign_id!r} — one request, one subject."
        )
    return spec


@campaigns_router.get("/campaigns/{campaign_id}/config-map", response_model=ConfigMapResponse)
def get_campaign_config_map(stores: StoresDep, campaign_id: str) -> ConfigMapResponse:
    """The knob coupling/provenance map for one campaign — what moves which
    statistical estimand, what overwrites what, and which knobs currently collide.

    Read-only: resolves the config the campaign's root runs under — its draft while it is
    still authoring — against the declared ``knobs`` registry. 404 on cross-user reads.
    """
    campaign = stores.campaigns.load_owned(campaign_id, str(stores.identity.user_id))
    if campaign is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    return config_map(resolve_root_config(stores, campaign))
