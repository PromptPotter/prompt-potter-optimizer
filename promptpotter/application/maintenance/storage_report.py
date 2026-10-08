"""On-disk size, read-only. ONE taxonomy, MECE: every byte lands in exactly one of six leaves. The ``--keep-results`` keepsake
is a cross-cutting SUBSET — surface it as a note, never a summed figure, or the partition stops being MECE."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from promptpotter.domain.results import RoundResult
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import iter_files, read_json_tolerant
from promptpotter.infrastructure.store.layout import SHARED_CACHE_DIRS, FileKind, classify
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    from promptpotter.domain.campaign import Campaign
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "CampaignStorageResponse",
    "DatasetStorageEntry",
    "DatasetStorageResponse",
    "WorkspaceStorageEntry",
    "WorkspaceStorageResponse",
    "campaign_storage",
    "storage_by_dataset",
    "workspace_storage",
]

_SAMPLE_ROWS = list[dict[str, Any]]

# The per-sample arrays inside a public round file — what the backend produced → ``connector``.
# Read off the round document's own model, so a row field declared there is counted here.
_CONNECTOR_ROUND_KEYS = tuple(
    name
    for name, field in RoundResult.model_fields.items()
    if field.annotation in (_SAMPLE_ROWS, dict[str, _SAMPLE_ROWS])
)

_LEAVES = tuple(dict.fromkeys(kind.leaf for kind in FileKind))


@functools.lru_cache(maxsize=2048)
def _connector_bytes_of(_path: str, _mtime_ns: int, _size: int) -> int:
    """The backend's share of one round file, memoized on ``(path, mtime, size)``: a round a repair
    rewrote or a rewind swapped is re-read, since a disk-usage figure must never lag the disk."""
    doc = read_json_tolerant(Path(_path))
    if not isinstance(doc, dict):
        return 0
    return sum(len(json.dumps(doc[k])) for k in _CONNECTOR_ROUND_KEYS if k in doc)


def _campaign_split(root: Path) -> dict[str, int]:
    """One walk of a campaign tree → ``{leaf: bytes}`` over the six MECE leaves, which sum exactly to the on-disk total.
    ``ROUND_PUBLIC`` is the lone straddler — backend arrays to ``connector``, the searchpoint remainder to ``state``."""
    acc = dict.fromkeys(_LEAVES, 0)
    if not root.is_dir():
        return acc
    # Walked per read: only the whole listing covers these bytes (a directory's mtime stands
    # still while a file below it grows), and taking that listing IS this walk.
    below = len(root.parts)
    for path, st in iter_files(root):
        kind = classify(path.parts[below:])
        if kind is FileKind.ROUND_PUBLIC:
            conn = min(_connector_bytes_of(str(path), st.st_mtime_ns, st.st_size), st.st_size)
            acc["connector"] += conn
            acc["state"] += st.st_size - conn
        else:
            acc[kind.leaf] += st.st_size
    return acc


def _dir_size(root: Path, *, skip: frozenset[str] = frozenset()) -> int:
    """Bytes under *root*, skipping the top-level names in *skip*."""
    return sum(st.st_size for _, st in iter_files(root, skip=skip))


def _owned_campaign_splits(stores: Stores) -> list[tuple[Campaign, dict[str, int]]]:
    """Every campaign the caller owns, archived included, each with its six-leaf split — the ONE
    scan both workspace reports pool from, so they cannot disagree about the same bytes."""
    owner = str(stores.identity.user_id)
    return [
        (campaign, _campaign_split(stores.campaigns.campaign_root_dir(campaign.campaign_id)))
        for campaign in stores.campaigns.list_campaigns(lifecycle="all", owner_user_id=owner)
    ]


def _leaf_fields(acc: dict[str, int]) -> dict[str, int]:
    return {f"{k}_bytes": acc[k] for k in _LEAVES}


class CampaignStorageResponse(StrictModel):
    campaign_id: str = Field(description="The campaign measured")
    on_disk_bytes: int = Field(description="Whole campaign-dir footprint — sum of the six leaves")
    dataset_bytes: int = Field(description="langfuse ground-truth mirror (the input-data copy)")
    connector_bytes: int = Field(description="Backend-produced: node-I/O cache + per-sample arrays")
    state_bytes: int = Field(description="Loop resume point: round searchpoint state + overrides")
    trace_bytes: int = Field(description="Loop telemetry: streams, prompts, langfuse loop trace")
    history_bytes: int = Field(description="Loop event spine: ledger.jsonl")
    reports_bytes: int = Field(description="Readable output: manifest + reports + hard_samples")


def campaign_storage(stores: Stores, campaign_id: str) -> CampaignStorageResponse:
    """Raises ``NotFoundError`` on a campaign the caller does not own, as on a missing one."""
    if stores.campaigns.load_owned(campaign_id, str(stores.identity.user_id)) is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    acc = _campaign_split(stores.campaigns.campaign_root_dir(campaign_id))
    return CampaignStorageResponse(
        campaign_id=campaign_id, on_disk_bytes=sum(acc.values()), **_leaf_fields(acc)
    )


class DatasetStorageEntry(StrictModel):
    dataset_name: str
    total_bytes: int = Field(description="On disk — the sum of the six leaves")
    dataset_bytes: int
    connector_bytes: int
    state_bytes: int
    trace_bytes: int
    history_bytes: int
    reports_bytes: int


class DatasetStorageResponse(StrictModel):
    total_bytes: int = Field(description="Grand total across the caller's datasets")
    datasets: list[DatasetStorageEntry] = Field(description="Fattest-first per-dataset leaf splits")


def storage_by_dataset(stores: Stores) -> DatasetStorageResponse:
    """The shared measurement store is excluded: it is filed by content, owned by no dataset."""
    by_dataset: dict[str, dict[str, int]] = {}
    for campaign, split in _owned_campaign_splits(stores):
        acc = by_dataset.setdefault(campaign.dataset_name, dict.fromkeys(_LEAVES, 0))
        for k in _LEAVES:
            acc[k] += split[k]
    entries = [
        DatasetStorageEntry(dataset_name=name, total_bytes=sum(acc.values()), **_leaf_fields(acc))
        for name, acc in by_dataset.items()
    ]
    entries.sort(key=lambda e: e.total_bytes, reverse=True)
    return DatasetStorageResponse(total_bytes=sum(e.total_bytes for e in entries), datasets=entries)


class WorkspaceStorageEntry(StrictModel):
    campaign_id: str
    dataset_name: str
    lifecycle_status: str
    on_disk_bytes: int = Field(description="Whole campaign-dir footprint")
    dataset_bytes: int
    connector_bytes: int
    state_bytes: int
    trace_bytes: int
    history_bytes: int
    reports_bytes: int


class WorkspaceStorageResponse(StrictModel):
    total_bytes: int = Field(
        description="The tenant's real on-disk total — campaigns + caches + other"
    )
    shared_cache_bytes: int = Field(
        description="Cross-campaign reuse caches ("
        + ", ".join(f"{d}/" for d in SHARED_CACHE_DIRS)
        + ") — survive delete"
    )
    other_bytes: int = Field(
        description="Everything else under the tenant: sessions, workspace ledger, dataset/backend stores"
    )
    campaigns: list[WorkspaceStorageEntry] = Field(
        description="Fattest-first per-campaign totals (active + archived)"
    )


def workspace_storage(stores: Stores) -> WorkspaceStorageResponse:
    entries: list[WorkspaceStorageEntry] = []
    campaigns_total = 0
    for campaign, acc in _owned_campaign_splits(stores):
        on_disk = sum(acc.values())
        campaigns_total += on_disk
        entries.append(
            WorkspaceStorageEntry(
                campaign_id=campaign.campaign_id,
                dataset_name=campaign.dataset_name,
                lifecycle_status=campaign.lifecycle_status,
                on_disk_bytes=on_disk,
                **_leaf_fields(acc),
            )
        )
    entries.sort(key=lambda e: e.on_disk_bytes, reverse=True)
    base = stores.base_dir
    shared = sum(_dir_size(base / name) for name in SHARED_CACHE_DIRS)
    # Each byte is counted ONCE and the three parts ARE the total. Both halves read the ONE
    # declaration: named in the sum but not the skip set, a cache is counted twice.
    other = _dir_size(base, skip=frozenset({"campaigns", *SHARED_CACHE_DIRS}))
    return WorkspaceStorageResponse(
        total_bytes=campaigns_total + shared + other,
        shared_cache_bytes=shared,
        other_bytes=other,
        campaigns=entries,
    )
