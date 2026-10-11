from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import Field

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.loaders import bank_samples, resolve_dataset_items
from promptpotter.application.datasets.prompts import has_dataset_prompts
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.pipeline_resolve import (
    dataset_pipeline_declaration,
    experiment_outside_run,
    resolve_pipeline_config_params,
)
from promptpotter.application.runner.campaign_ids import build_origin_cycle_id
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    is_dataset_dir,
    list_readable_datasets,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import (
    NotFoundError,
    PayloadInvalidError,
    StoredConfigInvalidError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DatasetIndexEntry",
    "OriginEntry",
    "canonical_origin_campaign",
    "list_datasets",
    "list_origins",
    "prospective_origin_id",
]


def prospective_origin_id(stores: Stores, dataset_dir: Path, dataset_name: str) -> str | None:
    """Not ``resolve_pipeline_for_campaign``: a dataset with no campaign has no manifest to freeze."""
    try:
        experiment = experiment_outside_run(dataset_dir)
        schema = parse_pipeline_response(
            dataset_pipeline_declaration(stores, dataset_dir, experiment) or {}
        )
        cfg = load_dataset_campaign_config(dataset_campaign_path(dataset_dir))
        active = schema.active_steps_excluding(cfg.exclude_nodes)
        if not active:
            return None
        base_pp = resolve_pipeline_config_params(
            active,
            cfg.pipeline_overlay,
            dataset_dir,
            schema,
            judges=cfg.judges,
            experiment=experiment,
            stores=stores,
            workspace=stores.base_dir,
        )
        opt_sp = resolve_origin_opt_search_point(
            prompt_node_names=schema.prompt_node_names(),
            dataset_dir=dataset_dir,
            pipeline_params=base_pp,
            schema=schema,
        )
        items = resolve_dataset_items(stores, dataset_name)
        if not items:
            return None
        partition = partition_bank(bank_samples(items), cfg.dataset_split)
        return build_origin_cycle_id(
            opt_sp,
            schema,
            list(partition.search),
            framing=campaign_framing(stores, cfg, dataset_name),
            demo=partition.demo,
        ).removeprefix("cycle_")
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        StoredConfigInvalidError,
        NotFoundError,
        PayloadInvalidError,
    ):
        # A SURVEY: an unreadable neighbour drops itself, never the list. Direct reads still raise.
        logger.exception("origins: prospective origin id failed for %s", dataset_name)
        return None


class DatasetIndexEntry(StrictModel):
    """One row in the dataset registry — backs the Dashboard ``New campaign`` view.

    Wire shape pinned in ``docs/specs/api-openapi.yaml::DatasetIndexEntry``.
    """

    name: str = Field(description="Slug used as the path segment under `datasets/`.")
    title: str | None = Field(default=None, description="Display title (from `dataset.md`).")
    tier: Literal["yours", "install"] = Field(
        description=(
            "``yours`` = user-owned Origin under ``projects/{tenant}/datasets/{slug}/``. "
            "``install`` = content that ships with the product at ``datasets/{slug}/`` "
            "(benchmarks, demos, ``promptpotter-self``) — tracked in git, so readable by "
            "anyone using the install. A ``yours`` slug shadows an ``install`` one."
        ),
    )
    n_samples: int | None = Field(
        default=None,
        description=(
            "Sample bank size from ``cache.json``; ``null`` when the cache has not been "
            "materialized, which is not the same as a dataset holding zero usable rows."
        ),
    )


def list_datasets(stores: Stores) -> list[DatasetIndexEntry]:
    return [
        DatasetIndexEntry(name=ref.name, title=ref.title, tier=ref.tier, n_samples=ref.n_samples)
        for ref in list_readable_datasets(stores)
    ]


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
        default=None,
        description="The origin's C0 score, as the root cycle's standing round 0 closed",
    )
    prepared: bool = Field(
        default=False, description="True = a ready dataset config with no campaign yet"
    )
    created_at: str = Field(default="", description="ISO 8601 — earliest campaign on this origin")


def _campaigns_by_origin(stores: Stores) -> dict[str, list[Campaign]]:
    """NOT owner-filtered: a CLI mint's owner id differs from a browser session's in ONE tenant."""
    by_origin: dict[str, list[Campaign]] = {}
    for campaign in stores.campaigns.list_campaigns(None, lifecycle="active", owner_user_id=None):
        by_origin.setdefault(campaign.origin_id, []).append(campaign)
    return by_origin


def _canonical(group: list[Campaign]) -> Campaign:
    return min(group, key=lambda c: c.created_at)


def canonical_origin_campaign(stores: Stores, origin_id: str) -> Campaign | None:
    """``None`` for a *prepared* origin, which has no campaign yet."""
    group = _campaigns_by_origin(stores).get(origin_id)
    return _canonical(group) if group else None


def _campaign_backed_origins(stores: Stores, n_samples: dict[str, int | None]) -> list[OriginEntry]:
    out: list[OriginEntry] = []
    for origin_id, group in _campaigns_by_origin(stores).items():
        canonical = _canonical(group)
        try:
            readable_dataset_dir(stores, canonical.dataset_name)
        except DatasetAccessError:
            logger.info(
                "origins: skipping origin %s — dataset %r no longer resolves",
                origin_id,
                canonical.dataset_name,
            )
            continue
        # The BEST across the origin's campaigns: origin scoring is nondeterministic at the backend.
        scores = [
            origin.accuracy
            for c in group
            if (index := stores.campaigns.load(c.root_hop)) is not None
            for origin in index.rounds[:1]
            if origin.round == 0 and origin.accuracy is not None
        ]
        out.append(
            OriginEntry(
                origin_id=origin_id,
                dataset_name=canonical.dataset_name,
                label=canonical.label,
                n_samples=n_samples.get(canonical.dataset_name),
                n_campaigns=len(group),
                origin_accuracy=max(scores) if scores else None,
                created_at=canonical.created_at,
            )
        )
    return sorted(out, key=lambda o: o.created_at, reverse=True)


def list_origins(stores: Stores) -> list[OriginEntry]:
    datasets = list_readable_datasets(stores)
    campaign_backed = _campaign_backed_origins(stores, {r.name: r.n_samples for r in datasets})
    taken = {o.origin_id for o in campaign_backed}
    prepared: list[OriginEntry] = []
    for ref in datasets:
        if ref.tier != "yours" or not ref.n_samples:
            continue
        dataset_dir = stores.tenant_datasets.dataset_dir(ref.name)
        if not has_dataset_prompts(dataset_dir) or not is_dataset_dir(dataset_dir):
            continue
        origin_id = prospective_origin_id(stores, dataset_dir, ref.name)
        if origin_id is None or origin_id in taken:
            continue
        prepared.append(
            OriginEntry(
                origin_id=origin_id,
                dataset_name=ref.name,
                label=ref.title or "",
                n_samples=ref.n_samples,
                prepared=True,
            )
        )
    return [*prepared, *campaign_backed]
