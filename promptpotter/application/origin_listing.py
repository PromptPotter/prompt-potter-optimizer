from __future__ import annotations

import json
import logging

from pydantic import Field

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.datasets.authored import load_dataset_campaign_config
from promptpotter.application.datasets.loaders import resolve_bank
from promptpotter.application.datasets.prompts import has_dataset_prompts
from promptpotter.application.datasets.startability import dataset_reading, offered
from promptpotter.application.datasets.tabular_ingest import read_candidate_library_file
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.pipeline_resolve import (
    dataset_pipeline_declaration,
    experiment_outside_run,
    resolve_pipeline_config_params,
)
from promptpotter.application.runner.campaign_ids import origin_content_hash
from promptpotter.domain.arms import PointContext
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.dataset_start import DatasetStartBlock
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import (
    DatasetTier,
    ResolvedDataset,
    list_readable_datasets,
    resolve_dataset,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import (
    DatasetAccessError,
    NotFoundError,
    PayloadInvalidError,
    StoredConfigInvalidError,
)

logger = logging.getLogger(__name__)


def prospective_origin_id(stores: Stores, dataset: ResolvedDataset) -> str | None:
    """Not ``resolve_pipeline_for_campaign``: a dataset with no campaign has no manifest to freeze."""
    dataset_dir = dataset.directory
    try:
        experiment = experiment_outside_run(stores, dataset)
        declared = dataset_pipeline_declaration(stores, dataset, experiment)
        if declared is None:
            return None
        schema = parse_pipeline_response(declared)
        cfg = load_dataset_campaign_config(dataset_dir)
        active = schema.active_steps_excluding(cfg.exclude_nodes)
        if not active:
            return None
        base_pp = resolve_pipeline_config_params(
            active,
            cfg.pipeline_overlay,
            dataset,
            schema,
            judges=cfg.judges,
            experiment=experiment,
            stores=stores,
            workspace=stores.base_dir,
        )
        opt_sp = resolve_origin_opt_search_point(
            dataset_dir=dataset_dir,
            pipeline_params=base_pp,
            schema=schema,
        )
        bank = resolve_bank(stores, dataset)
        if not bank.samples:
            return None
        partition = partition_bank(bank.samples, cfg.dataset_split, seed=cfg.run_seed)
        points = PointContext(
            schema=schema, framing=campaign_framing(stores, cfg, dataset), demo=partition.demo
        )
        return origin_content_hash(
            opt_sp,
            points,
            list(partition.search),
            candidate_library=read_candidate_library_file(dataset_dir),
        )
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
        logger.exception("origins: prospective origin id failed for %s", dataset.name)
        return None


class DatasetIndexEntry(StrictModel):
    """One row in the dataset registry — backs the Dashboard ``New campaign`` view."""

    name: str = Field(description="The definition's path under its tier, `/`-separated.")
    title: str | None = Field(default=None, description="Display title (from `README.md`).")
    tier: DatasetTier = Field(
        description=(
            "``yours`` = user-owned Origin under ``projects/{tenant}/datasets/{slug}/``. "
            "``install`` = content that ships with the product at ``assets/datasets/{name}/`` "
            "(benchmarks, demos, ``promptpotter-self``) — tracked in git, so readable by "
            "anyone using the install. A ``yours`` slug shadows an ``install`` one."
        ),
    )
    summary: str | None = Field(
        default=None,
        description="What the dataset is, in one plain sentence (the first under `README.md`'s title).",
    )
    n_samples: int | None = Field(
        default=None,
        description=(
            "Rows held without a fetch (the connector's panel, else the materialized bank); "
            "``null`` when none are, which is not the same as a dataset holding zero usable rows."
        ),
    )
    start_block: DatasetStartBlock | None = Field(
        description=(
            "``null`` = a check-in can open it here. Otherwise why not: the same reading "
            "``POST /datasets/{name}/draft`` refuses with."
        ),
    )


def list_datasets(stores: Stores) -> list[DatasetIndexEntry]:
    return [
        DatasetIndexEntry(
            name=ref.dataset.name,
            title=ref.title,
            tier=ref.dataset.tier,
            summary=ref.summary,
            n_samples=reading.n_held,
            start_block=reading.block,
        )
        for ref in list_readable_datasets(stores)
        if offered(stores, ref.dataset.name)
        for reading in (dataset_reading(stores, ref.dataset.name),)
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
            resolve_dataset(stores, canonical.dataset_name)
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
            if (index := stores.campaigns.load(c.root_hop, moment=None)) is not None
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
    n_held = {r.dataset.name: dataset_reading(stores, r.dataset.name).n_held for r in datasets}
    campaign_backed = _campaign_backed_origins(stores, n_held)
    taken = {o.origin_id for o in campaign_backed}
    prepared: list[OriginEntry] = []
    for ref in datasets:
        dataset = ref.dataset
        if dataset.tier != "yours" or not n_held[dataset.name]:
            continue
        if not has_dataset_prompts(dataset.directory):
            continue
        origin_id = prospective_origin_id(stores, dataset)
        if origin_id is None or origin_id in taken:
            continue
        prepared.append(
            OriginEntry(
                origin_id=origin_id,
                dataset_name=dataset.name,
                label=ref.title or "",
                n_samples=n_held[dataset.name],
                prepared=True,
            )
        )
    return [*prepared, *campaign_backed]
