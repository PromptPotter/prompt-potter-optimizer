"""Every byte lands in exactly one leaf; a cross-cutting subset is a note, never a summed figure."""

from __future__ import annotations

import functools
import json
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field

from promptpotter.config.paths import SHARED_CACHE_DIRS
from promptpotter.domain.campaign import LifecycleStatus
from promptpotter.domain.results import RoundResult
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.data_root import LEAF_INFO, Leaf, campaign_row
from promptpotter.infrastructure.store.io import iter_files, read_json_tolerant
from promptpotter.infrastructure.store.layout import ArchiveHomeOwner
from promptpotter.infrastructure.store.stores import Stores, owned_campaign

_SAMPLE_ROWS = list[dict[str, Any]]

# Read off the round file's own model, so a row field declared there is counted here.
_CONNECTOR_ROUND_KEYS = tuple(
    name
    for name, field in RoundResult.model_fields.items()
    if field.annotation in (_SAMPLE_ROWS, dict[str, _SAMPLE_ROWS])
)


def _leaf_of(path: Path, campaign_dir: Path) -> Leaf:
    """A file no campaign row names is unclassified."""
    row = campaign_row(PurePosixPath(path.relative_to(campaign_dir).as_posix()))
    return Leaf.UNCLASSIFIED if row is None else row.leaf


@functools.lru_cache(maxsize=2048)
def _connector_bytes_of(_path: str, _mtime_ns: int, _size: int) -> int:
    """Keyed on ``(path, mtime, size)`` so a rewritten round is re-read, never served stale."""
    doc = read_json_tolerant(Path(_path))
    if not isinstance(doc, dict):
        return 0
    return sum(len(json.dumps(doc[k])) for k in _CONNECTOR_ROUND_KEYS if k in doc)


class LeafShare(StrictModel):
    """One leaf's bytes in a campaign tree."""

    leaf: Leaf
    bytes: int


class LeafBytes(StrictModel):
    """One campaign tree on disk, every byte in exactly one leaf; `data_root.py::LEAF_INFO` words them."""

    total: int = Field(description="The whole campaign dir: the sum of `leaves`.")
    loop: int = Field(description="What the loop wrote: the leaves `LEAF_INFO` marks `loop`.")
    leaves: list[LeafShare] = Field(
        description="Every leaf once, in `Leaf` order, an empty one at 0."
    )


def _campaign_split(root: Path) -> LeafBytes:
    acc = dict.fromkeys(Leaf, 0)
    if root.is_dir():
        # Walked per read, uncached: a directory's mtime stands still while a file below it grows.
        for path, st in iter_files(root):
            leaf = _leaf_of(path, root)
            if leaf is Leaf.STATE:
                conn = min(_connector_bytes_of(str(path), st.st_mtime_ns, st.st_size), st.st_size)
                acc[Leaf.CONNECTOR] += conn
                acc[Leaf.STATE] += st.st_size - conn
            else:
                acc[leaf] += st.st_size
    return LeafBytes(
        total=sum(acc.values()),
        loop=sum(n for leaf, n in acc.items() if LEAF_INFO[leaf].loop),
        leaves=[LeafShare(leaf=leaf, bytes=acc[leaf]) for leaf in Leaf],
    )


def _dir_size(root: Path, *, skip: frozenset[str] = frozenset()) -> int:
    return sum(st.st_size for _, st in iter_files(root, skip=skip))


def campaign_storage(stores: Stores, campaign_id: str) -> LeafBytes:
    """Raises ``NotFoundError`` on a campaign the caller does not own, as on a missing one."""
    owned_campaign(stores, campaign_id)
    return _campaign_split(stores.campaigns.campaign_root_dir(campaign_id))


class WorkspaceStorageEntry(StrictModel):
    campaign_id: str
    dataset_name: str
    lifecycle_status: LifecycleStatus
    on_disk: LeafBytes


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
    entries = [
        WorkspaceStorageEntry(
            campaign_id=campaign.campaign_id,
            dataset_name=campaign.dataset_name,
            lifecycle_status=campaign.lifecycle_status,
            on_disk=_campaign_split(stores.campaigns.campaign_root_dir(campaign.campaign_id)),
        )
        for campaign in stores.campaigns.list_campaigns(lifecycle="all")
    ]
    entries.sort(key=lambda e: e.on_disk.total, reverse=True)
    campaigns_total = sum(e.on_disk.total for e in entries)
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
