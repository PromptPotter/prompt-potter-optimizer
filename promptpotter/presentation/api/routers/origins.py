"""An *origin* is a content identity, distinct from a campaign (a run of one) and a dataset (raw material). Derived,
not stored — an origin drops off the list when the last campaign using it is archived."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import Field

from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.ingest import draft_from_dataset
from promptpotter.application.datasets.loaders import resolve_dataset_items
from promptpotter.application.datasets.prompts import has_dataset_prompts
from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks
from promptpotter.application.optimization.task_context import committed_task_context
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.pipeline_resolve import resolve_pipeline_config_params
from promptpotter.application.runner.campaign_ids import build_origin_cycle_id
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.sample import Sample
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.campaign_store.store import origin_accuracy_of
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    dataset_pipeline_path,
    is_dataset_dir,
    list_readable_datasets,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.shared.errors import NotFoundError, StoredConfigInvalidError

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


def _dataset_origin_id(stores: Stores, dataset_dir: Path, dataset_name: str) -> str | None:
    """The dataset's CURRENT committed config-aware origin id — the same hash a fresh mint would stamp, computed from disk with no
    Session. The merge runs through the SHARED resolver, so this prospective id cannot diverge from what a real run stamps."""
    try:
        raw = read_yaml(dataset_pipeline_path(dataset_dir))
        schema = parse_pipeline_response(raw)
        cfg = load_dataset_campaign_config(dataset_campaign_path(dataset_dir))
        active = schema.active_steps_excluding(cfg.exclude_nodes)
        if not active:
            return None
        base_pp = resolve_pipeline_config_params(
            active, cfg.pipeline_overrides, dataset_dir, schema, judge=cfg.judge
        )
        opt_sp = resolve_origin_opt_search_point(
            prompt_node_names=schema.prompt_node_names(),
            dataset_dir=dataset_dir,
            task_context=committed_task_context(stores, dataset_name),
        )
        items = resolve_dataset_items(stores, dataset_name)
        if not items:
            return None
        samples = [Sample(**it) for it in items]
        return build_origin_cycle_id(opt_sp, schema, samples, base_pp).removeprefix("cycle_")
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        StoredConfigInvalidError,
    ):
        # StoredConfigInvalidError included deliberately: this is a SURVEY over every
        # tenant dataset, so one unreadable neighbour drops itself, never the list.
        # The dataset's own direct reads still 500 with the restamp remedy.
        logger.exception("origins: prospective origin id failed for %s", dataset_name)
        return None


def _prepared_origins(stores: Stores, campaign_ids: set[str]) -> list[OriginEntry]:
    """Each ready tenant dataset as its CURRENT config-aware origin, marked *prepared* when that exact config has no campaign
    yet — so an edited-but-unrun config surfaces beside the dataset's older origins and folds in once run."""
    out: list[OriginEntry] = []
    for ref in list_readable_datasets(stores):
        if ref.tier != "yours" or not ref.n_samples:
            continue
        d = stores.tenant_datasets.dataset_dir(ref.name)
        # Ready = ships a prompts/ dir (any node-named or default.yaml prompt — the
        # origin OSP resolves the per-node file like the mint does, see
        # `resolve_origin_opt_search_point`) + a pipeline.yaml. Hardcoding
        # `default.json` here wrongly dropped datasets whose prompt is node-named
        # (e.g. termnorm's `entity_profiling.json`).
        if not has_dataset_prompts(d) or not is_dataset_dir(d):
            continue
        origin_id = _dataset_origin_id(stores, d, ref.name)
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
def draft_from_origin(origin_id: str, stores: StoresDep) -> dict[str, Any]:
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
    seed = stores.campaigns.read_cycle_seed(match.root_hop)
    overrides: dict[str, Any] = {"reused_origin_id": origin_id}
    if seed is not None and seed.origin_prompt_fields:
        overrides["origin_prompt_fields"] = dict(seed.origin_prompt_fields)
    draft = draft_from_dataset(
        stores=stores,
        dataset_dir=dataset_dir,
        dataset_name=match.dataset_name,
        overrides=overrides,
    )
    return draft_wire_with_locks(draft)
