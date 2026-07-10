"""Campaign registry — real Campaign manifests, not cycles."""

from __future__ import annotations

from typing import Any

from fastapi import Query
from pydantic import BaseModel, Field

from promptpotter.application.bootstrap.wiring import (
    backend_type_of_dataset,
    resolve_dataset_config_dir,
)
from promptpotter.application.config import CampaignConfig, MechanismConfig
from promptpotter.application.config_coupling import (
    COUPLINGS,
    Estimand,
    check_couplings,
    estimand_doc,
    knob_label,
    resolve_knob_states,
)
from promptpotter.application.jobs.launcher import draft_wire_with_locks, load_checkin_draft
from promptpotter.application.meta_champion import ChampionRegistry, reduce_corpus
from promptpotter.application.resource_matrix import ResourceMatrix, read_matrix
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.paths import REPO_ROOT
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError
from promptpotter.shared.identity import L4_LAB_CAP


class CampaignSummary(BaseModel):
    campaign_id: str = Field(description="Stable campaign id ({dataset}__{origin hash})")
    dataset_name: str = Field(description="Dataset this campaign optimizes")
    label: str = Field(default="", description="Operator-supplied campaign label")
    status: str = Field(description="Status of the campaign's most-recent session")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    root_cycle_id: str = Field(description="The campaign's first session's root cycle id")
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
    session_count: int = Field(
        default=1, description="Number of sessions (re-runs of the declaration) in the campaign"
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


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignSummary] = Field(description="List of campaign summaries")
    total: int = Field(description="Total number of campaigns")


class SessionSummary(BaseModel):
    """One session in a campaign's forest — a root cycle + its forks."""

    root_cycle_id: str = Field(description="The session's root cycle id")
    status: str = Field(default="", description="Session root cycle status")
    n_rounds: int = Field(default=0, description="Rounds completed on the session root cycle")
    best_accuracy: float | None = Field(default=None, description="Best round accuracy")
    created_at: str = Field(default="", description="ISO 8601 session creation timestamp")
    updated_at: str = Field(default="", description="ISO 8601 last write")


class CampaignDetailResponse(CampaignSummary):
    root_content_hash: str = Field(
        default="", description="Content hash of the origin search point — the campaign identity"
    )
    config: dict[str, Any] = Field(description="Frozen CampaignConfig snapshot for this campaign")
    sessions: list[SessionSummary] = Field(
        description="Every session in the campaign's forest, ordered by root cycle id"
    )


def _backend_type(store: Stores, dataset_name: str, memo: dict[str, str]) -> str:
    """Memo over ``backend_type_of_dataset`` — the listing endpoint answers it once per DATASET,
    not once per campaign, since a workspace holds many campaigns per dataset."""
    if dataset_name not in memo:
        memo[dataset_name] = backend_type_of_dataset(store, REPO_ROOT, dataset_name)
    return memo[dataset_name]


def _campaign_summary(
    campaign: Any, session_count: int, status: str, backend_type: str
) -> CampaignSummary:
    return CampaignSummary(
        campaign_id=campaign.campaign_id,
        dataset_name=campaign.dataset_name,
        label=campaign.label,
        status=status,
        created_at=campaign.created_at,
        root_cycle_id=campaign.root_cycle_id,
        backend_id=campaign.backend_id,
        backend_type=backend_type,
        session_count=session_count,
        owner_user_id=campaign.owner_user_id,
        lifecycle_status=campaign.lifecycle_status,
        lifecycle_changed_at=campaign.lifecycle_changed_at,
        lifecycle_reason=campaign.lifecycle_reason,
    )


def _session_summaries(store: Stores, campaign_id: str) -> list[SessionSummary]:
    """Build the campaign's session list from its root cycles' index.json."""
    out: list[SessionSummary] = []
    for e in store.campaigns.enumerate_cycles():
        if e["campaign_id"] != campaign_id or not e["is_root"]:
            continue
        out.append(
            SessionSummary(
                root_cycle_id=e["cycle_id"],
                status=e["status"],
                n_rounds=e["n_rounds"],
                best_accuracy=e["best_accuracy"],
                created_at=e["created_at"],
                updated_at=e["updated_at"],
            )
        )
    out.sort(key=lambda s: s.root_cycle_id)
    return out


class MechanismToggle(BaseModel):
    key: str = Field(description="Field key under its group (e.g. 'epsilon_elimination')")
    label: str = Field(description="Human-readable toggle name")
    description: str = Field(description="What the mechanism does, on vs off")
    default: bool = Field(description="Default value (preserves stock loop behavior)")


class MechanismGroup(BaseModel):
    key: str = Field(description="Group key under optimization.mechanisms (e.g. 'elimination')")
    label: str = Field(description="Human-readable group name")
    description: str = Field(description="What this group of mechanisms governs")
    toggles: list[MechanismToggle] = Field(description="Toggles in this group, in declared order")


class MechanismSchemaResponse(BaseModel):
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
) -> CampaignListResponse:
    """Every campaign on disk owned by the caller, newest first.

    Filters: optional ``?dataset=`` for one dataset, ``?lifecycle=`` for the
    visibility intent (defaults to ``active``; ``archived`` and ``deleted`` drop
    out of the default surface). The default ``active`` view ALSO surfaces
    in-progress ``checkin`` campaigns (origin authoring is resumable progress, so
    it belongs in the sidebar beside running work) — the only surface that unions
    them, so origin/campaign-backed lists that ask the store for ``active``
    directly stay free of the empty-hash check-ins. Cross-user campaigns are
    invisible — the ``owner_user_id`` gate filters on ``store.identity.user_id``.
    """
    if lifecycle not in _LIFECYCLE_FILTERS:
        raise PayloadInvalidError(
            f"Invalid lifecycle filter: {lifecycle!r}. Expected one of {_LIFECYCLE_FILTERS}."
        )
    owner = str(store.identity.user_id)
    campaigns = store.campaigns.list_campaigns(dataset, lifecycle=lifecycle, owner_user_id=owner)
    if lifecycle == "active":
        campaigns += store.campaigns.list_campaigns(
            dataset, lifecycle="checkin", owner_user_id=owner
        )
    campaigns.sort(key=lambda c: c.created_at, reverse=True)
    memo: dict[str, str] = {}
    return CampaignListResponse(
        campaigns=[
            _campaign_summary(
                c,
                len(store.campaigns.list_sessions(c.campaign_id)),
                store.campaigns.latest_session_status(c.campaign_id),
                _backend_type(store, c.dataset_name, memo),
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
    """Campaign manifest detail + its session forest. 404 on cross-user reads."""
    campaign = store.campaigns.load_campaign(campaign_id)
    # Cross-user reads return 404 (not 403) — existence leakage is itself a
    # violation.
    if campaign is None or campaign.owner_user_id != str(store.identity.user_id):
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    sessions = _session_summaries(store, campaign_id)
    return CampaignDetailResponse(
        campaign_id=campaign.campaign_id,
        dataset_name=campaign.dataset_name,
        label=campaign.label,
        status=sessions[-1].status if sessions else "active",
        created_at=campaign.created_at,
        root_cycle_id=campaign.root_cycle_id,
        backend_id=campaign.backend_id,
        backend_type=_backend_type(store, campaign.dataset_name, {}),
        session_count=len(sessions) or 1,
        owner_user_id=campaign.owner_user_id,
        lifecycle_status=campaign.lifecycle_status,
        lifecycle_changed_at=campaign.lifecycle_changed_at,
        lifecycle_reason=campaign.lifecycle_reason,
        root_content_hash=campaign.root_content_hash,
        config=campaign.config,
        sessions=sessions,
    )


class ConfigKnob(BaseModel):
    path: str = Field(
        description="Dotted CampaignConfig path (or const.<NAME> for a hardcoded floor)"
    )
    label: str = Field(description="Short display name (prefix-stripped)")
    value: Any = Field(description="Effective value in this campaign's frozen config")
    source: str = Field(
        description="Where the value came from: default | campaign (operator-set) | required | constant"
    )


class ConfigEstimandGroup(BaseModel):
    key: str = Field(description="Estimand key (selection, difficulty, ability, …)")
    label: str = Field(description="Human-readable estimand name")
    doc: str = Field(description="Plain-language one-liner of what this estimand is")
    knobs: list[ConfigKnob] = Field(description="Knobs that move this estimand, in declared order")


class ConfigCoupling(BaseModel):
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


class ConfigMapResponse(BaseModel):
    """The config-map for one campaign: every knob grouped by the statistical
    estimand it moves (with effective value + source layer), plus every declared
    coupling flagged active/inactive against this campaign's frozen config.

    Server-authored from the single ``application.config_coupling`` registry — the
    same source the CLI ``config_map`` diagnostic and the pre-run preflight warning
    read, so the webapp panel never disagrees with the engine on which knobs collide.
    """

    groups: list[ConfigEstimandGroup] = Field(description="Estimand groups, in declared order")
    couplings: list[ConfigCoupling] = Field(description="Declared couplings, active ones flagged")


@campaigns_router.get("/campaigns/{campaign_id}/config-map", response_model=ConfigMapResponse)
def get_campaign_config_map(store: StoreDep, campaign_id: str) -> ConfigMapResponse:
    """The knob coupling/provenance map for one campaign — what moves which
    statistical estimand, what overwrites what, and which knobs currently collide.

    Read-only: resolves the frozen ``CampaignConfig`` snapshot against the declared
    ``config_coupling`` registry. 404 on cross-user reads.
    """
    campaign = store.campaigns.load_campaign(campaign_id)
    if campaign is None or campaign.owner_user_id != str(store.identity.user_id):
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


def _require_l4_lab(store: Stores) -> None:
    """Gate the L4 Lab routes on :data:`L4_LAB_CAP`. Without it, 404 (not 403) —
    the whole Lab surface is invisible to a non-developer identity, matching the
    existence-hiding convention the cross-user reads use. The webapp reads the same
    capability off ``/auth/me`` and never renders the tab, so a 404 here is pure
    defense-in-depth against a direct hit, never a path the UI walks into."""
    if L4_LAB_CAP not in store.identity.capabilities:
        raise NotFoundError("Not found", code="not_found")


@campaigns_router.get("/champion-registry", response_model=ChampionRegistry)
def get_champion_registry(store: StoreDep) -> ChampionRegistry:
    """The L4 champion table — every candidate meta-prompt state on disk, ranked by
    anchor-to-origin effect. Reduced fresh from the tenant's pp-self cycles on each
    fetch (dev on-demand surface, not the 2 s poll); zero LLM. Dev-only —
    :data:`L4_LAB_CAP`-gated (404 without it); empty for a dev with no pp-self cycles."""
    _require_l4_lab(store)
    return reduce_corpus(store)


@campaigns_router.get("/resource-matrix", response_model=ResourceMatrix)
def get_resource_matrix(store: StoreDep) -> ResourceMatrix:
    """The L4 resource matrix — the (target-model × dataset) capability grid the
    operator built with ``matrix measure``. Read-only from the committed pp-self
    ``resource_matrix.json``. Dev-only — :data:`L4_LAB_CAP`-gated (404 without it);
    empty when it has never been measured."""
    _require_l4_lab(store)
    pp_self_dir = resolve_dataset_config_dir(store, REPO_ROOT, "promptpotter-self")
    matrix = read_matrix(pp_self_dir)
    return matrix or ResourceMatrix(generated_at="", cells=[])
