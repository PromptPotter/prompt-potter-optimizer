"""Origins — the runnable starting points the operator reuses.

An *origin* is a content identity (resolved origin prompt + pipeline config),
distinct from a campaign (a run of an origin) and from a dataset (raw material).
This list is what the webapp "Reuse an origin" picker shows. Today it derives
**campaign-backed** origins: every distinct origin that ≥1 active campaign
references, deduped by ``Campaign.root_content_hash``. Prepared origins (a ready
dataset config with no campaign yet — potter-run / edited config) are unioned in
by a later phase; the ``prepared`` flag marks them.

Derived, not stored: an origin drops from the list when the last campaign using
it is archived/deleted (the ``lifecycle="active"`` filter), so there is no
separate origin to clean up.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import Field

from promptpotter.application.config import load_campaign_config, resolve_pipeline_config_params
from promptpotter.application.datasets.authored import read_campaign_config_file
from promptpotter.application.datasets.csv_ingest import IngestError
from promptpotter.application.datasets.ingest import draft_from_dataset
from promptpotter.application.datasets.loaders import resolve_dataset_items
from promptpotter.application.datasets.prompts import has_dataset_prompts
from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.runner.identity import build_origin_cycle_id
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.sample import Sample
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.campaign_store.store import origin_accuracy_of
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    list_readable_datasets,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_json
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError

logger = logging.getLogger(__name__)

origins_router = APIRouter(prefix="/origins", tags=["Origins"])


class OriginEntry(StrictModel):
    origin_id: str = Field(
        description="Origin content identity — a campaign's root_content_hash (or the "
        "dataset's prospective origin hash for a prepared origin)"
    )
    dataset_name: str = Field(description="Dataset this origin starts from")
    label: str = Field(default="", description="Operator-supplied label, if any")
    n_samples: int = Field(default=0, description="Dataset sample count (0 if unmaterialized)")
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


def _campaign_backed_origins(store: Stores) -> list[OriginEntry]:
    """Group the caller's active campaigns by origin identity → one entry each."""
    samples_by_dataset = {r.name: r.n_samples for r in list_readable_datasets(store)}
    # Tenant-scoped (no owner_user_id filter), matching the dashboard's `/cycles`
    # surface: a CLI-minted campaign is owned by the registered-developer user_id,
    # which differs from a browser OIDC session's user_id even within the SAME
    # tenant — owner-filtering would hide the operator's own origins in the web UI.
    # `Stores.identity` still enforces tenant isolation (no cross-tenant leak).
    campaigns = store.campaigns.list_campaigns(None, lifecycle="active", owner_user_id=None)
    by_origin: dict[str, list[Campaign]] = {}
    for c in campaigns:
        # Empty hash (a `checkin` campaign still authoring its origin) → keep the campaign as
        # its own origin rather than collapsing every blank into one bogus group.
        by_origin.setdefault(c.root_content_hash or c.campaign_id, []).append(c)

    out: list[OriginEntry] = []
    for origin_id, group in by_origin.items():
        canonical = min(group, key=lambda c: c.created_at)
        # Best origin score across the origin's campaigns (origin scoring is
        # nondeterministic at the backend, so runs of one origin vary); None when
        # no campaign recorded an origin_accuracy yet.
        accs = [
            a
            for c in group
            if (a := origin_accuracy_of(store.campaigns.load(c.campaign_id, c.root_cycle_id) or {}))
            is not None
        ]
        out.append(
            OriginEntry(
                origin_id=origin_id,
                dataset_name=canonical.dataset_name,
                label=canonical.label,
                n_samples=samples_by_dataset.get(canonical.dataset_name, 0),
                n_campaigns=len(group),
                origin_accuracy=float(max(accs)) if accs else None,
                prepared=False,
                created_at=canonical.created_at,
            )
        )
    return out


def _dataset_origin_id(store: Stores, dataset_dir: Path, dataset_name: str) -> str | None:
    """The dataset's CURRENT committed config-aware origin id — the same hash a fresh mint
    would stamp (``configure_and_apply_pipeline`` merge → ``build_origin_cycle_id``),
    computed from disk without a Session. Returns the bare hash, or ``None`` if the dataset
    can't be resolved. The node-config merge runs through the SHARED
    ``resolve_pipeline_config_params`` — the exact definition the live setup path uses — so
    this prospective id can never silently diverge from what a real run stamps. Steps get
    overwritten by ``to_job_search_point`` with the schema's active_steps, so only the
    per-node config (model included) drives the config-aware hash."""
    try:
        raw = read_json(dataset_dir / "pipeline.json")
        schema = parse_pipeline_response(raw)
        cfg = load_campaign_config(read_campaign_config_file(dataset_dir / "campaign.json"))
        active = schema.active_steps_excluding(cfg.exclude_nodes)
        if not active:
            return None
        base_pp = resolve_pipeline_config_params(
            active, cfg.pipeline_overrides, dataset_dir, schema
        )
        osp = resolve_origin_opt_search_point(
            prompt_node_names=schema.prompt_node_names(), dataset_dir=dataset_dir
        )
        items = resolve_dataset_items(store, dataset_name)
        if not items:
            return None
        samples = [Sample(**it) for it in items]
        return build_origin_cycle_id(osp, schema, samples, base_pp).removeprefix("cycle_")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        logger.exception("origins: prospective origin id failed for %s", dataset_name)
        return None


def _prepared_origins(store: Stores, campaign_ids: set[str]) -> list[OriginEntry]:
    """Each ready tenant dataset as its CURRENT config-aware origin, shown as *prepared*
    when that exact config has no campaign yet (its prospective origin id isn't
    campaign-backed). So an edited-but-unrun config (a model swap, a potter-run prep)
    surfaces as a distinct prepared origin beside the dataset's older campaign-origins, and
    folds into campaign-backed once run. Only the operator's own (``tier="yours"``) datasets
    — a benchmark stub is not a prepared origin to run."""
    out: list[OriginEntry] = []
    for ref in list_readable_datasets(store):
        if ref.tier != "yours" or ref.n_samples <= 0:
            continue
        d = store.tenant_datasets.dataset_dir(ref.name)
        # Ready = ships a prompts/ dir (any node-named or default.json prompt — the
        # origin OSP resolves the per-node file like the mint does, see
        # `resolve_origin_opt_search_point`) + a pipeline.json. Hardcoding
        # `default.json` here wrongly dropped datasets whose prompt is node-named
        # (e.g. termnorm's `entity_profiling.json`).
        if not has_dataset_prompts(d) or not (d / "pipeline.json").is_file():
            continue
        origin_id = _dataset_origin_id(store, d, ref.name)
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
def list_origins(store: StoreDep) -> OriginListResponse:
    """Every runnable origin in the caller's tenant — campaign-backed + prepared, newest first.

    Tenant-scoped (like the dashboard's ``/cycles``), NOT owner-filtered: a
    CLI-minted campaign and a browser OIDC session can share a tenant yet differ
    in ``user_id``, so owner-filtering would hide the operator's own origins.
    ``Stores.identity`` still blocks cross-tenant reads. Prepared origins (ready
    datasets with no campaign yet) sort to the top so freshly-prepared work is
    seen first.
    """
    campaign_backed = _campaign_backed_origins(store)
    prepared = _prepared_origins(store, {o.origin_id for o in campaign_backed})
    campaign_backed.sort(key=lambda o: o.created_at, reverse=True)
    return OriginListResponse(
        origins=[*prepared, *campaign_backed], total=len(prepared) + len(campaign_backed)
    )


def _campaign_for_origin(store: Stores, origin_id: str) -> Campaign | None:
    """The canonical (earliest) active campaign whose origin identity is ``origin_id``.

    Mirrors :func:`_campaign_backed_origins`' grouping key (``root_content_hash``, or
    ``campaign_id`` for a blank-hash ``checkin`` manifest). ``None`` for a *prepared*
    origin id (no campaign yet) — those reuse the dataset-draft path, not this one.
    """
    matches = [
        c
        for c in store.campaigns.list_campaigns(None, lifecycle="active", owner_user_id=None)
        if (c.root_content_hash or c.campaign_id) == origin_id
    ]
    return min(matches, key=lambda c: c.created_at) if matches else None


@origins_router.post("/{origin_id}/draft")
def draft_from_origin(origin_id: str, store: StoreDep) -> dict[str, Any]:
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
    match = _campaign_for_origin(store, origin_id)
    if match is None:
        raise NotFoundError(f"Origin '{origin_id}' not found", code="command_target_not_found")
    try:
        dataset_dir = readable_dataset_dir(store, match.dataset_name)
    except DatasetAccessError as exc:
        raise NotFoundError(f"Dataset '{match.dataset_name}' not found") from exc
    seed = store.campaigns.read_cycle_seed(match.campaign_id, match.root_cycle_id)
    overrides: dict[str, Any] = {"reused_origin_id": origin_id}
    if seed is not None and seed.origin_prompt_fields:
        overrides["origin_prompt_fields"] = dict(seed.origin_prompt_fields)
    try:
        draft = draft_from_dataset(
            stores=store,
            dataset_dir=dataset_dir,
            dataset_name=match.dataset_name,
            overrides=overrides,
        )
    except IngestError as exc:
        raise PayloadInvalidError(
            exc.message, code="ingest_failed", details={"reason": exc.reason}
        ) from None
    return draft_wire_with_locks(draft)
