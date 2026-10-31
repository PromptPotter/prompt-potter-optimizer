"""Every byte lands in exactly one leaf; a cross-cutting subset is a note, never a summed figure."""

from __future__ import annotations

import functools
import json
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from pydantic import Field

from promptpotter.config.paths import SHARED_CACHE_DIRS
from promptpotter.domain.results import RoundResult
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.data_root import CAMPAIGN_ROWS, Leaf, classify_in_workspace
from promptpotter.infrastructure.store.io import iter_files, read_json_tolerant
from promptpotter.infrastructure.store.layout import ArchiveHomeOwner
from promptpotter.infrastructure.store.stores import Stores, owned_campaign

if TYPE_CHECKING:
    from promptpotter.domain.campaign import Campaign


_SAMPLE_ROWS = list[dict[str, Any]]

# Read off the round file's own model, so a row field declared there is counted here.
_CONNECTOR_ROUND_KEYS = tuple(
    name
    for name, field in RoundResult.model_fields.items()
    if field.annotation in (_SAMPLE_ROWS, dict[str, _SAMPLE_ROWS])
)

_LEAVES = tuple(leaf.value for leaf in Leaf)


def _leaf_of(path: Path, workspace: Path) -> Leaf:
    """A file no campaign row names counts as loop trace."""
    found = classify_in_workspace(PurePosixPath(path.relative_to(workspace).as_posix()))
    row = None if found is None else CAMPAIGN_ROWS.get(found.kind)
    return Leaf.TRACE if row is None else row.leaf


@functools.lru_cache(maxsize=2048)
def _connector_bytes_of(_path: str, _mtime_ns: int, _size: int) -> int:
    """Keyed on ``(path, mtime, size)`` so a rewritten round is re-read, never served stale."""
    doc = read_json_tolerant(Path(_path))
    if not isinstance(doc, dict):
        return 0
    return sum(len(json.dumps(doc[k])) for k in _CONNECTOR_ROUND_KEYS if k in doc)


def _campaign_split(root: Path) -> dict[str, int]:
    acc = dict.fromkeys(_LEAVES, 0)
    if not root.is_dir():
        return acc
    # Walked per read, uncached: a directory's mtime stands still while a file below it grows.
    for path, st in iter_files(root):
        leaf = _leaf_of(path, root.parent.parent)
        if leaf is Leaf.STATE:
            conn = min(_connector_bytes_of(str(path), st.st_mtime_ns, st.st_size), st.st_size)
            acc[Leaf.CONNECTOR.value] += conn
            acc[Leaf.STATE.value] += st.st_size - conn
        else:
            acc[leaf.value] += st.st_size
    return acc


def _dir_size(root: Path, *, skip: frozenset[str] = frozenset()) -> int:
    return sum(st.st_size for _, st in iter_files(root, skip=skip))


def _owned_campaign_splits(stores: Stores) -> list[tuple[Campaign, dict[str, int]]]:
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
    state_bytes: int = Field(description="Round checkouts less their per-sample rows")
    trace_bytes: int = Field(description="Loop telemetry: streams, prompts, langfuse loop trace")
    history_bytes: int = Field(description="Loop event spine: ledger.jsonl")
    reports_bytes: int = Field(description="Readable output: manifest + reports + hard_samples")
    loop_bytes: int = Field(
        description="What the loop wrote: the state, trace, history and reports leaves together"
    )


_LOOP_LEAVES = ("state", "trace", "history", "reports")
assert set(_LOOP_LEAVES) <= set(_LEAVES)


def campaign_storage(stores: Stores, campaign_id: str) -> CampaignStorageResponse:
    """Raises ``NotFoundError`` on a campaign the caller does not own, as on a missing one."""
    owned_campaign(stores, campaign_id)
    acc = _campaign_split(stores.campaigns.campaign_root_dir(campaign_id))
    return CampaignStorageResponse(
        campaign_id=campaign_id,
        on_disk_bytes=sum(acc.values()),
        loop_bytes=sum(acc[leaf] for leaf in _LOOP_LEAVES),
        **_leaf_fields(acc),
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
    dataset_bytes: int = Field(description="Each leaf below: that leaf summed over `datasets`")
    connector_bytes: int
    state_bytes: int
    trace_bytes: int
    history_bytes: int
    reports_bytes: int
    datasets: list[DatasetStorageEntry] = Field(description="Fattest-first per-dataset leaf splits")


def storage_by_dataset(stores: Stores) -> DatasetStorageResponse:
    """The archive home is excluded: it is filed by content, owned by no dataset."""
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
    whole = {leaf: sum(acc[leaf] for acc in by_dataset.values()) for leaf in _LEAVES}
    return DatasetStorageResponse(
        total_bytes=sum(whole.values()), datasets=entries, **_leaf_fields(whole)
    )


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
        description="What this account holds on disk — campaigns + other, + the archive home "
        "when the account owns it"
    )
    archive_home_id: str = Field(
        description="The archive home this account files into and replays from: its own tenant "
        "id, or the install's shared archive"
    )
    archive_home_owner: ArchiveHomeOwner = Field(
        description="`tenant`: the account's own home, counted in `total_bytes`; `install`: one "
        "every account on the install shares, counted in no account's total"
    )
    archive_home_bytes: int = Field(
        description="The home's cross-campaign reuse caches ("
        + ", ".join(f"{d}/" for d in SHARED_CACHE_DIRS)
        + ") — survive delete; under an install-owned home, every account's"
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
    home = stores.archive_home
    home_bytes = sum(_dir_size(home.root / name) for name in SHARED_CACHE_DIRS)
    # Both halves read the ONE declaration: a tenant's own home is its workspace, so a cache in the
    # sum but not the skip set counts twice.
    other = _dir_size(stores.base_dir, skip=frozenset({"campaigns", *SHARED_CACHE_DIRS}))
    owned = home_bytes if home.owner == "tenant" else 0
    return WorkspaceStorageResponse(
        total_bytes=campaigns_total + owned + other,
        archive_home_id=home.id,
        archive_home_owner=home.owner,
        archive_home_bytes=home_bytes,
        other_bytes=other,
        campaigns=entries,
    )
