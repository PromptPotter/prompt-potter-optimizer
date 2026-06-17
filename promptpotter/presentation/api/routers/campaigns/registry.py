"""Campaign registry — real Campaign manifests, not cycles."""

from __future__ import annotations

from typing import Any

from fastapi import Query
from pydantic import BaseModel, Field

from promptpotter.application.config import MechanismConfig
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.paths import session_index
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError


class CampaignSummary(BaseModel):
    campaign_id: str = Field(description="Stable campaign id ({dataset}__{origin hash})")
    dataset_name: str = Field(description="Dataset this campaign optimizes")
    label: str = Field(default="", description="Operator-supplied campaign label")
    status: str = Field(description="Status of the campaign's most-recent session")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    root_cycle_id: str = Field(description="The campaign's first session's root cycle id")
    backend_id: str = Field(default="", description="Backend this campaign optimizes against")
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
    session_index: int = Field(description="1-based session ordinal within the campaign")
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
        description="Every session in the campaign's forest, ordered by session index"
    )


def _campaign_summary(campaign: Any, session_count: int) -> CampaignSummary:
    return CampaignSummary(
        campaign_id=campaign.campaign_id,
        dataset_name=campaign.dataset_name,
        label=campaign.label,
        status=campaign.status,
        created_at=campaign.created_at,
        root_cycle_id=campaign.root_cycle_id,
        backend_id=campaign.backend_id,
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
                session_index=session_index(e["cycle_id"]),
                status=e["status"],
                n_rounds=e["n_rounds"],
                best_accuracy=e["best_accuracy"],
                created_at=e["created_at"],
                updated_at=e["updated_at"],
            )
        )
    out.sort(key=lambda s: s.session_index)
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


_LIFECYCLE_FILTERS = ("active", "archived", "deleted", "all")


@campaigns_router.get("/campaigns", response_model=CampaignListResponse)
async def list_campaigns(
    store: StoreDep,
    dataset: str | None = Query(default=None, description="Filter to one dataset"),
    lifecycle: str = Query(
        default="active",
        description="Operator visibility filter — 'active' (default), 'archived', 'deleted', or 'all'",
    ),
) -> CampaignListResponse:
    """Every campaign on disk owned by the caller, newest first.

    Filters: optional ``?dataset=`` for one dataset, ``?lifecycle=`` for the
    visibility intent (defaults to ``active``; ``archived`` and ``deleted``
    drop out of the default surface). Cross-user campaigns are invisible —
    the ``owner_user_id`` gate filters on ``store.identity.user_id``.
    """
    if lifecycle not in _LIFECYCLE_FILTERS:
        raise PayloadInvalidError(
            f"Invalid lifecycle filter: {lifecycle!r}. Expected one of {_LIFECYCLE_FILTERS}."
        )
    campaigns = store.campaigns.list_campaigns(
        dataset,
        lifecycle=lifecycle,
        owner_user_id=str(store.identity.user_id),
    )
    campaigns.sort(key=lambda c: c.created_at, reverse=True)
    return CampaignListResponse(
        campaigns=[
            _campaign_summary(c, len(store.campaigns.list_sessions(c.campaign_id)))
            for c in campaigns
        ],
        total=len(campaigns),
    )


@campaigns_router.get("/campaigns/{campaign_id}", response_model=CampaignDetailResponse)
async def get_campaign(store: StoreDep, campaign_id: str) -> CampaignDetailResponse:
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
        status=campaign.status,
        created_at=campaign.created_at,
        root_cycle_id=campaign.root_cycle_id,
        backend_id=campaign.backend_id,
        session_count=len(sessions) or 1,
        owner_user_id=campaign.owner_user_id,
        lifecycle_status=campaign.lifecycle_status,
        lifecycle_changed_at=campaign.lifecycle_changed_at,
        lifecycle_reason=campaign.lifecycle_reason,
        root_content_hash=campaign.root_content_hash,
        config=campaign.config,
        sessions=sessions,
    )
