"""Campaign registry — real Campaign manifests, not cycles."""

from __future__ import annotations

from typing import Any

from fastapi import Query
from pydantic import Field

from promptpotter.application.bootstrap.wiring import backend_type_of_dataset
from promptpotter.application.config import (
    CampaignConfig,
    Estimand,
    MechanismConfig,
    estimand_doc,
    knob_label,
)
from promptpotter.application.jobs.launcher.checkin import load_checkin_draft
from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks
from promptpotter.application.knobs import (
    COUPLINGS,
    check_couplings,
    resolve_knob_states,
)
from promptpotter.application.meta_champion import ChampionRegistry, reduce_corpus
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores, descend_store
from promptpotter.presentation.api.deps import StoreDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError


class CampaignSummary(StrictModel):
    campaign_id: str = Field(description="Campaign id ({dataset}__{rand6}) — one RUN of an origin")
    dataset_name: str = Field(description="Dataset this campaign optimizes")
    label: str = Field(default="", description="Operator-supplied campaign label")
    status: str = Field(description="Status of the campaign's run (its root cycle)")
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
            "Connector KIND of the campaign's dataset ('termnorm' / 'promptpotter' / …), read off "
            "`{dataset}/pipeline.json::backend_type`. The webapp's ONE test for a self-optimizing "
            "(L4) campaign — it renders the 'inner loops' disclosure and the pp-self panel "
            "variants on it. Empty when the dataset config is gone (a campaign outlives its "
            "dataset dir); callers treat empty as 'not self-optimizing'."
        ),
    )
    owner_user_id: str = Field(
        default="default", description="UserId of the operator who minted the campaign"
    )
    lifecycle_status: str = Field(
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


class CampaignListResponse(StrictModel):
    campaigns: list[CampaignSummary] = Field(description="List of campaign summaries")
    total: int = Field(description="Total number of campaigns")


class CampaignDetailResponse(CampaignSummary):
    root_content_hash: str = Field(
        default="", description="Content hash of the origin search point — the campaign identity"
    )
    config: dict[str, Any] = Field(description="Frozen CampaignConfig snapshot for this campaign")


def _backend_type(store: Stores, dataset_name: str, memo: dict[str, str]) -> str:
    """Memo over ``backend_type_of_dataset`` — the listing endpoint answers it once per DATASET,
    not once per campaign, since a workspace holds many campaigns per dataset."""
    if dataset_name not in memo:
        memo[dataset_name] = backend_type_of_dataset(store, dataset_name)
    return memo[dataset_name]


def _campaign_summary(campaign: Campaign, status: str, backend_type: str) -> CampaignSummary:
    return CampaignSummary(
        campaign_id=campaign.campaign_id,
        dataset_name=campaign.dataset_name,
        label=campaign.label,
        status=status,
        created_at=campaign.created_at,
        root_cycle_id=campaign.root_cycle_id,
        backend_id=campaign.backend_id,
        backend_type=backend_type,
        owner_user_id=campaign.owner_user_id,
        lifecycle_status=campaign.lifecycle_status,
        lifecycle_changed_at=campaign.lifecycle_changed_at,
        lifecycle_reason=campaign.lifecycle_reason,
    )


class MechanismToggle(StrictModel):
    key: str = Field(description="Field key under its group (e.g. 'epsilon_elimination')")
    label: str = Field(description="Human-readable toggle name")
    description: str = Field(description="What the mechanism does, on vs off")
    default: bool = Field(description="Default value (preserves stock loop behavior)")


class MechanismGroup(StrictModel):
    key: str = Field(description="Group key under optimization.mechanisms (e.g. 'elimination')")
    label: str = Field(description="Human-readable group name")
    description: str = Field(description="What this group of mechanisms governs")
    toggles: list[MechanismToggle] = Field(description="Toggles in this group, in declared order")


class MechanismSchemaResponse(StrictModel):
    """Self-describing descriptor for the campaign-config mechanism toggles.

    Derived live from ``MechanismConfig``'s JSON schema, so a new toggle (a bool
    added to a group model) surfaces here — and in the webapp — with no edit.
    Pair the active value off the campaign's frozen ``config`` snapshot
    (``optimization.mechanisms.{group}.{key}``).
    """

    groups: list[MechanismGroup] = Field(description="Mechanism groups, in declared order")


def _ref_name(prop: dict[str, Any]) -> str:
    """Resolve a property's ``$ref`` def name (Pydantic may wrap it in ``allOf``)."""
    ref = prop.get("$ref") or prop["allOf"][0]["$ref"]
    return str(ref).rsplit("/", 1)[-1]


@campaigns_router.get("/campaigns/mechanisms-schema", response_model=MechanismSchemaResponse)
async def get_mechanisms_schema() -> MechanismSchemaResponse:
    """The mechanism-toggle descriptor — groups, labels, descriptions, defaults.

    Read-only and campaign-independent: the webapp zips this with a campaign's
    frozen ``config`` to render the active toggle states.
    """
    schema = MechanismConfig.model_json_schema()
    defs = schema["$defs"]
    groups: list[MechanismGroup] = []
    for group_key, group_prop in schema["properties"].items():
        gdef = defs[_ref_name(group_prop)]
        toggles = [
            MechanismToggle(
                key=tk,
                label=tprop.get("title", tk),
                description=tprop.get("description", ""),
                default=bool(tprop.get("default", False)),
            )
            for tk, tprop in gdef["properties"].items()
        ]
        groups.append(
            MechanismGroup(
                key=group_key,
                label=group_key.replace("_", " ").title(),
                description=gdef.get("description", ""),
                toggles=toggles,
            )
        )
    return MechanismSchemaResponse(groups=groups)


_LIFECYCLE_FILTERS = ("active", "archived", "deleted", "checkin", "all")


@campaigns_router.get("/campaigns", response_model=CampaignListResponse)
def list_campaigns(
    store: StoreDep,
    dataset: str | None = Query(default=None, description="Filter to one dataset"),
    lifecycle: str = Query(
        default="active",
        description="Operator visibility filter — 'active' (default, includes "
        "in-progress 'checkin' campaigns), 'archived', 'deleted', 'checkin', or 'all'",
    ),
    descend: str | None = Query(None),
) -> CampaignListResponse:
    """Every campaign in one store owned by the caller, newest first.

    Filters: optional ``?dataset=`` for one dataset, ``?lifecycle=`` for the
    visibility intent (defaults to ``active``; ``archived`` and ``deleted`` drop
    out of the default surface). The default ``active`` view ALSO surfaces
    in-progress ``checkin`` campaigns (origin authoring is resumable progress, so
    it belongs in the sidebar beside running work) — the only surface that unions
    them, so origin/campaign-backed lists that ask the store for ``active``
    directly stay free of the empty-hash check-ins. Cross-user campaigns are
    invisible — the ``owner_user_id`` gate filters on ``store.identity.user_id``.

    ``descend`` names the chain of cycles to descend INTO (see ``GET /cycles``);
    absent/empty is the tenant's own tree. It is the twin of the cycle list: a
    forest is campaigns × cycles, and the sidebar groups runs by
    ``root_cycle_id`` (their origin), so BOTH lists must be available at every
    depth for one tree builder to serve L4, L5, and the top level alike.
    """
    if lifecycle not in _LIFECYCLE_FILTERS:
        raise PayloadInvalidError(
            f"Invalid lifecycle filter: {lifecycle!r}. Expected one of {_LIFECYCLE_FILTERS}."
        )
    leaf = descend_store(store, decode_descend(descend))
    owner = str(leaf.identity.user_id)
    campaigns = leaf.campaigns.list_campaigns(dataset, lifecycle=lifecycle, owner_user_id=owner)
    if lifecycle == "active":
        campaigns += leaf.campaigns.list_campaigns(
            dataset, lifecycle="checkin", owner_user_id=owner
        )
    campaigns.sort(key=lambda c: c.created_at, reverse=True)
    memo: dict[str, str] = {}
    return CampaignListResponse(
        campaigns=[
            _campaign_summary(
                c,
                leaf.campaigns.run_status(c.campaign_id, c.root_cycle_id),
                _backend_type(leaf, c.dataset_name, memo),
            )
            for c in campaigns
        ],
        total=len(campaigns),
    )


@campaigns_router.get("/campaigns/{campaign_id}/checkin")
def get_campaign_checkin(store: StoreDep, campaign_id: str) -> dict[str, Any]:
    """Re-open a durable check-in campaign — its draft wire + last resolver turn.

    The sidebar opens a ``checkin``-lifecycle campaign through here instead of the
    dashboard (no ``dashboard.json`` exists pre-loop): the webapp rebuilds the
    ingest "ready" panel from ``draft`` and shows the prior ``resolution`` recap.
    Tenant-scoped (the check-in store is rooted at the tenant dir) — a cross-tenant
    id 404s. 404 when this campaign has no check-in working state (already Started
    or never a check-in). Wire contract pinned in
    ``docs/specs/m12-api-openapi.yaml::GET /campaigns/{id}/checkin``.
    """
    draft = load_checkin_draft(store, campaign_id)
    if draft is None:
        raise NotFoundError(
            f"No check-in working state for campaign {campaign_id}",
            code="command_target_not_found",
        )
    bank = store.checkin.load_bank(campaign_id) or {}
    block = bank.get("resolution") or {}
    return {
        "draft": draft_wire_with_locks(draft),
        "resolution": block.get("last_resolution"),
        # Proposals the last turn left unclicked. Without these a re-opened check-in
        # would drop the operator's outstanding actions on the floor.
        "raised": block.get("raised") or [],
    }


@campaigns_router.get("/campaigns/{campaign_id}", response_model=CampaignDetailResponse)
def get_campaign(store: StoreDep, campaign_id: str) -> CampaignDetailResponse:
    """Campaign manifest detail. 404 on cross-user reads."""
    # Cross-user reads return 404 (not 403) — existence leakage is itself a
    # violation. Ownership rule lives in `load_owned`.
    campaign = store.campaigns.load_owned(campaign_id, str(store.identity.user_id))
    if campaign is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    return CampaignDetailResponse(
        campaign_id=campaign.campaign_id,
        dataset_name=campaign.dataset_name,
        label=campaign.label,
        status=store.campaigns.run_status(campaign_id, campaign.root_cycle_id),
        created_at=campaign.created_at,
        root_cycle_id=campaign.root_cycle_id,
        backend_id=campaign.backend_id,
        backend_type=_backend_type(store, campaign.dataset_name, {}),
        owner_user_id=campaign.owner_user_id,
        lifecycle_status=campaign.lifecycle_status,
        lifecycle_changed_at=campaign.lifecycle_changed_at,
        lifecycle_reason=campaign.lifecycle_reason,
        root_content_hash=campaign.root_content_hash,
        config=campaign.config,
    )


class ConfigKnob(StrictModel):
    path: str = Field(
        description="Dotted CampaignConfig path (or const.<NAME> for a hardcoded floor)"
    )
    label: str = Field(description="Short display name (prefix-stripped)")
    value: Any = Field(description="Effective value in this campaign's frozen config")
    source: str = Field(
        description="Where the value came from: default | campaign (operator-set) | required | constant"
    )


class ConfigEstimandGroup(StrictModel):
    key: str = Field(description="Estimand key (selection, difficulty, ability, …)")
    label: str = Field(description="Human-readable estimand name")
    doc: str = Field(description="Plain-language one-liner of what this estimand is")
    knobs: list[ConfigKnob] = Field(description="Knobs that move this estimand, in declared order")


class ConfigCoupling(StrictModel):
    name: str = Field(description="Coupling id")
    knobs: list[str] = Field(description="Dotted knob paths the coupling relates")
    labels: list[str] = Field(description="Short display names for those knobs")
    estimand: str = Field(description="The shared estimand the knobs co-determine")
    relation: str = Field(description="The relationship rule, plain language")
    consequence: str = Field(description="What goes wrong when the combination is violated")
    severity: str = Field(
        description="collision (soundness) | inert (wasted knob) | info (relationship)"
    )
    active: bool = Field(
        description="True when this campaign's config is in the violating combination"
    )


class ConfigMapResponse(StrictModel):
    """The config-map for one campaign: every knob grouped by the statistical
    estimand it moves (with effective value + source layer), plus every declared
    coupling flagged active/inactive against this campaign's frozen config.

    Server-authored from the single ``application.knobs`` registry — the same source
    the pre-run preflight warning reads, so the webapp panel never disagrees with the
    engine on which knobs collide.
    """

    groups: list[ConfigEstimandGroup] = Field(description="Estimand groups, in declared order")
    couplings: list[ConfigCoupling] = Field(description="Declared couplings, active ones flagged")


@campaigns_router.get("/campaigns/{campaign_id}/config-map", response_model=ConfigMapResponse)
def get_campaign_config_map(store: StoreDep, campaign_id: str) -> ConfigMapResponse:
    """The knob coupling/provenance map for one campaign — what moves which
    statistical estimand, what overwrites what, and which knobs currently collide.

    Read-only: resolves the frozen ``CampaignConfig`` snapshot against the declared
    ``knobs`` registry. 404 on cross-user reads.
    """
    campaign = store.campaigns.load_owned(campaign_id, str(store.identity.user_id))
    if campaign is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    config = CampaignConfig.model_validate(campaign.config)

    states = resolve_knob_states(config)
    knob_models = {
        s.path: ConfigKnob(
            path=s.path,
            label=knob_label(s.path),
            value=s.value,
            source=s.source,
        )
        for s in states
    }
    groups: list[ConfigEstimandGroup] = []
    for estimand in Estimand:
        knobs = [knob_models[s.path] for s in states if estimand in s.estimands]
        if not knobs:
            continue
        groups.append(
            ConfigEstimandGroup(
                key=estimand.value,
                label=estimand.value.replace("_", " ").title(),
                doc=estimand_doc(estimand),
                knobs=knobs,
            )
        )

    active = {c.name for c in check_couplings(config)}
    couplings = [
        ConfigCoupling(
            name=c.name,
            knobs=list(c.knobs),
            labels=[knob_label(k) for k in c.knobs],
            estimand=c.estimand.value,
            relation=c.relation,
            consequence=c.consequence,
            severity=c.severity,
            active=c.name in active,
        )
        for c in COUPLINGS
    ]
    return ConfigMapResponse(groups=groups, couplings=couplings)


# The L4 read surface is tenant-scoped (a tenant sees only its own pp-self cycles) and
# consumed by the outer-loop dashboard box, which renders only when the operator is
# viewing their own self-optimizing campaign — so the read is self-gating on data. No
# capability gate: whitelabeled users never run a pp-self campaign, so it returns empty
# for them, and there is nothing to hide.
@campaigns_router.get("/champion-registry", response_model=ChampionRegistry)
def get_champion_registry(store: StoreDep) -> ChampionRegistry:
    """The L4 champion table — every candidate meta-prompt state on disk, ranked by
    anchor-to-origin effect. Reduced fresh from the tenant's pp-self cycles on each
    fetch (on-demand, not the 2 s poll); zero LLM. Tenant-scoped; empty for a tenant
    with no pp-self cycles."""
    return reduce_corpus(store)
