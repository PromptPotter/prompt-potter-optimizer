"""An *origin* is a content identity, distinct from a campaign (a run of one) and a dataset (raw material). Derived,
not stored — an origin drops off the list when the last campaign using it is archived."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import Field

from promptpotter.application.datasets.ingest import (
    draft_from_dataset,
    fetch_backend_nodes,
    refresh_capabilities,
)
from promptpotter.application.datasets.prompts import has_dataset_prompts
from promptpotter.application.jobs.launcher.draft_build import draft_wire
from promptpotter.application.origin import prospective_origin_id
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.campaign_store.store import origin_accuracy_of
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    is_dataset_dir,
    list_readable_datasets,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.shared.errors import NotFoundError

logger = logging.getLogger(__name__)

origins_router = APIRouter(prefix="/origins", tags=["Origins"])


class OriginEntry(StrictModel):
    origin_id: str = Field(
        description="Origin content identity — a campaign's root_content_hash (or the "
        "dataset's prospective origin hash for a prepared origin)"
    )
    dataset_name: str = Field(description="Dataset this origin starts from")
    label: str = Field(default="", description="Operator-supplied label, if any")
    n_samples: int | None = Field(
        default=None, description="Dataset sample count; ``null`` if unmaterialized"
    )
    n_campaigns: int = Field(
        default=0,
        description="Active campaigns minted from this origin (0 = prepared, not yet run)",
    )
    origin_accuracy: float | None = Field(
        default=None, description="The origin's C0 score, from the canonical campaign's index.json"
    )
    prepared: bool = Field(
        default=False, description="True = a ready dataset config with no campaign yet"
    )
    created_at: str = Field(default="", description="ISO 8601 — earliest campaign on this origin")


class OriginListResponse(StrictModel):
    origins: list[OriginEntry] = Field(description="Runnable origins, newest first")
    total: int = Field(description="Number of origins")


def _dataset_resolves(stores: Stores, name: str) -> bool:
    try:
        readable_dataset_dir(stores, name)
    except DatasetAccessError:
        return False
    return True


def _campaign_backed_origins(stores: Stores) -> list[OriginEntry]:
    samples_by_dataset = {r.name: r.n_samples for r in list_readable_datasets(stores)}
    # Tenant-scoped (no owner_user_id filter), matching the dashboard's `/cycles`
    # surface: a CLI-minted campaign is owned by the registered-developer user_id,
    # which differs from a browser OIDC session's user_id even within the SAME
    # tenant — owner-filtering would hide the operator's own origins in the web UI.
    # `Stores.identity` still enforces tenant isolation (no cross-tenant leak).
    campaigns = stores.campaigns.list_campaigns(None, lifecycle="active", owner_user_id=None)
    by_origin: dict[str, list[Campaign]] = {}
    for c in campaigns:
        # Empty hash (a `checkin` campaign still authoring its origin) → keep the campaign as
        # its own origin rather than collapsing every blank into one bogus group.
        by_origin.setdefault(c.root_content_hash or c.campaign_id, []).append(c)

    out: list[OriginEntry] = []
    for origin_id, group in by_origin.items():
        canonical = min(group, key=lambda c: c.created_at)
        # A campaign outlives its dataset (deleted, replaced, never committed), and
        # reuse has nothing to run without one. Ask the SAME resolver the reuse
        # click asks, so the list and the action cannot disagree.
        if not _dataset_resolves(stores, canonical.dataset_name):
            logger.info(
                "origins: skipping origin %s — dataset %r no longer resolves",
                origin_id,
                canonical.dataset_name,
            )
            continue
        # Best origin score across the origin's campaigns (origin scoring is
        # nondeterministic at the backend, so runs of one origin vary); None when
        # no campaign recorded an origin_accuracy yet.
        accs = [
            a
            for c in group
            if (a := origin_accuracy_of(stores.campaigns.load(c.root_hop) or {})) is not None
        ]
        out.append(
            OriginEntry(
                origin_id=origin_id,
                dataset_name=canonical.dataset_name,
                label=canonical.label,
                n_samples=samples_by_dataset.get(canonical.dataset_name),
                n_campaigns=len(group),
                origin_accuracy=float(max(accs)) if accs else None,
                prepared=False,
                created_at=canonical.created_at,
            )
        )
    return out


def _prepared_origins(stores: Stores, campaign_ids: set[str]) -> list[OriginEntry]:
    """Each ready tenant dataset as its CURRENT config-aware origin, marked *prepared* when that exact config has no campaign
    yet — so an edited-but-unrun config surfaces beside the dataset's older origins and folds in once run."""
    out: list[OriginEntry] = []
    for ref in list_readable_datasets(stores):
        if ref.tier != "yours" or not ref.n_samples:
            continue
        d = stores.tenant_datasets.dataset_dir(ref.name)
        # Ready = ships a prompts/ dir (any node-named or `default.yaml` prompt — the origin OSP
        # resolves the per-node file like the mint does, see `resolve_origin_opt_search_point`)
        # + a pipeline.yaml. Never a hardcoded filename: that drops every dataset whose prompt is
        # node-named, such as termnorm's `entity_profiling.yaml`.
        if not has_dataset_prompts(d) or not is_dataset_dir(d):
            continue
        origin_id = prospective_origin_id(stores, d, ref.name)
        if origin_id is None or origin_id in campaign_ids:
            continue
        out.append(
            OriginEntry(
                origin_id=origin_id,
                dataset_name=ref.name,
                label=ref.title or "",
                n_samples=ref.n_samples,
                n_campaigns=0,
                origin_accuracy=None,
                prepared=True,
                created_at="",
            )
        )
    return out


@origins_router.get("", response_model=OriginListResponse)
def list_origins(stores: StoresDep) -> OriginListResponse:
    """Every runnable origin in the caller's tenant — campaign-backed + prepared, newest first.

    Tenant-scoped (like the dashboard's ``/cycles``), NOT owner-filtered: a
    CLI-minted campaign and a browser OIDC session can share a tenant yet differ
    in ``user_id``, so owner-filtering would hide the operator's own origins.
    ``Stores.identity`` still blocks cross-tenant reads. Prepared origins (ready
    datasets with no campaign yet) sort to the top so freshly-prepared work is
    seen first.
    """
    campaign_backed = _campaign_backed_origins(stores)
    prepared = _prepared_origins(stores, {o.origin_id for o in campaign_backed})
    campaign_backed.sort(key=lambda o: o.created_at, reverse=True)
    return OriginListResponse(
        origins=[*prepared, *campaign_backed], total=len(prepared) + len(campaign_backed)
    )


def _campaign_for_origin(stores: Stores, origin_id: str) -> Campaign | None:
    """The canonical (earliest) active campaign whose origin identity is ``origin_id``, mirroring the grouping key its
    sibling uses. ``None`` for a *prepared* origin, which reuses the dataset-draft path instead."""
    matches = [
        c
        for c in stores.campaigns.list_campaigns(None, lifecycle="active", owner_user_id=None)
        if (c.root_content_hash or c.campaign_id) == origin_id
    ]
    return min(matches, key=lambda c: c.created_at) if matches else None


@origins_router.post("/{origin_id}/draft")
async def draft_from_origin(origin_id: str, stores: StoresDep) -> dict[str, Any]:
    """Open a chosen prior origin as a prefilled check-in campaign — the picker's
    "Reuse an origin" path for a campaign-backed origin.

    Resolves the origin's EXACT prompt fields: a campaign that was itself minted
    from an origin carries those fields on its root-cycle seed, so reuse them
    verbatim; a normally-minted campaign has no seed and ``draft_from_dataset``
    already loaded the dataset's authored prompt. The draft is marked
    ``reused_origin_id`` so starting it (``/commands/start-checkin``) seeds C0
    via ``origin_override`` and stamps the ``campaign_origin`` lineage. Nothing
    runs until the operator starts the check-in.
    """
    match = _campaign_for_origin(stores, origin_id)
    if match is None:
        raise NotFoundError(f"Origin '{origin_id}' not found", code="command_target_not_found")
    dataset_dir = readable_dataset_dir(stores, match.dataset_name)
    await refresh_capabilities(stores)
    seed = stores.campaigns.read_cycle_seed(match.root_hop)
    overrides: dict[str, Any] = {"reused_origin_id": origin_id}
    if seed is not None and seed.origin_prompt_fields:
        overrides["origin_prompt_fields"] = dict(seed.origin_prompt_fields)
    draft = draft_from_dataset(
        stores=stores,
        dataset_dir=dataset_dir,
        dataset_name=match.dataset_name,
        overrides=overrides,
        # The ORIGIN's own connector and config, off its manifest — reuse means reusing what it
        # RAN, not what the shared dataset file says today. The campaign goes over whole: which of
        # its layers seeds a draft is an application decision.
        backend_nodes=await fetch_backend_nodes(match.backend_type),
        origin_campaign=match,
    )
    return draft_wire(draft, stores.base_dir)
