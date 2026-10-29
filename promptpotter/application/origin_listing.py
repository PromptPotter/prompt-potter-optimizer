from __future__ import annotations

import logging

from pydantic import Field

from promptpotter.application.datasets.startability import dataset_reading, offered
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.dataset_start import DatasetStartBlock
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.dataset_access import (
    DatasetTier,
    list_readable_datasets,
    resolve_dataset,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import DatasetAccessError

logger = logging.getLogger(__name__)


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
    """A MINTED origin, its id the mint's hash; a dataset no campaign ran is a `DatasetIndexEntry`."""

    origin_id: str = Field(description="Origin content identity — its campaigns' root_content_hash")
    dataset_name: str = Field(description="Dataset this origin starts from")
    label: str = Field(default="", description="Operator-supplied label, if any")
    n_samples: int | None = Field(
        default=None, description="Dataset sample count; ``null`` if unmaterialized"
    )
    n_campaigns: int = Field(description="Active campaigns minted from this origin")
    origin_accuracy: float | None = Field(
        default=None,
        description="The origin's C0 score, as the root cycle's standing round 0 closed",
    )
    created_at: str = Field(description="ISO 8601 — earliest campaign on this origin")


def _campaigns_by_origin(stores: Stores) -> dict[str, list[Campaign]]:
    """NOT owner-filtered: a CLI mint's owner id differs from a browser session's in ONE tenant."""
    by_origin: dict[str, list[Campaign]] = {}
    for campaign in stores.campaigns.list_campaigns(None, lifecycle="active", owner_user_id=None):
        by_origin.setdefault(campaign.origin_id, []).append(campaign)
    return by_origin


def _canonical(group: list[Campaign]) -> Campaign:
    return min(group, key=lambda c: c.created_at)


def canonical_origin_campaign(stores: Stores, origin_id: str) -> Campaign | None:
    group = _campaigns_by_origin(stores).get(origin_id)
    return _canonical(group) if group else None


def list_origins(stores: Stores) -> list[OriginEntry]:
    n_samples = {
        r.dataset.name: dataset_reading(stores, r.dataset.name).n_held
        for r in list_readable_datasets(stores)
    }
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
