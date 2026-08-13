"""On-disk size, read-only. ONE taxonomy, MECE: every byte lands in exactly one of six leaves. The ``--keep-results`` keepsake
is a cross-cutting SUBSET — surface it as a note, never a summed figure, or the partition stops being MECE."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import FileKind, classify
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import NotFoundError

# Per-sample arrays inside a public round file that the backend produced → ``connector``.
_CONNECTOR_ROUND_KEYS = ("results", "all_candidate_results")

# Loop's four leaves, then the full six (top-level Connector / Loop / Dataset flattened).
_LOOP_LEAVES = ("state", "trace", "history", "reports")
_LEAVES = ("dataset", "connector", *_LOOP_LEAVES)


def _round_connector_bytes(path: Path) -> int:
    doc = read_json_tolerant(path)
    if not isinstance(doc, dict):
        return 0
    return sum(len(json.dumps(doc[k])) for k in _CONNECTOR_ROUND_KEYS if k in doc)


def _campaign_split(root: Path) -> dict[str, int]:
    """One walk of a campaign tree → ``{leaf: bytes}`` over the six MECE leaves, which sum exactly to the on-disk total.
    ``ROUND_PUBLIC`` is the lone straddler — backend arrays to ``connector``, the searchpoint remainder to ``state``."""
    acc = dict.fromkeys(_LEAVES, 0)
    if not root.is_dir():
        return acc
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
            size = p.stat().st_size
        except OSError:
            continue
        kind = classify(p.relative_to(root))
        if kind is FileKind.ROUND_PUBLIC:
            conn = min(_round_connector_bytes(p), size)
            acc["connector"] += conn
            acc["state"] += size - conn
        else:
            acc[kind.leaf] += size
    return acc


def _dir_size(root: Path) -> int:
    total = 0
    if not root.is_dir():
        return total
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


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


@campaigns_router.get("/campaigns/{campaign_id}/storage", response_model=CampaignStorageResponse)
def get_campaign_storage(stores: StoresDep, campaign_id: str) -> CampaignStorageResponse:
    """On-disk size of the campaign tree, split into the six MECE leaves. 404 on cross-user."""
    campaign = stores.campaigns.load_owned(campaign_id, str(stores.identity.user_id))
    if campaign is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    acc = _campaign_split(stores.campaigns.campaign_root_dir(campaign_id))
    return CampaignStorageResponse(
        campaign_id=campaign_id, on_disk_bytes=sum(acc.values()), **_leaf_fields(acc)
    )


# --- per-dataset tier breakdown (the Files-view "cake") -----------------------


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


@campaigns_router.get("/workspace/storage-by-dataset", response_model=DatasetStorageResponse)
def get_storage_by_dataset(stores: StoresDep) -> DatasetStorageResponse:
    """Per-dataset on-disk leaf breakdown (the Files-view 'cake') — every campaign of a
    dataset pooled, then split into the six MECE leaves. Includes archived campaigns; the
    shared measurement store is excluded (it's not per-dataset-owned)."""
    owner = str(stores.identity.user_id)
    by_dataset: dict[str, dict[str, int]] = {}
    for campaign in stores.campaigns.list_campaigns(lifecycle="all", owner_user_id=owner):
        acc = by_dataset.setdefault(campaign.dataset_name, dict.fromkeys(_LEAVES, 0))
        split = _campaign_split(stores.campaigns.campaign_root_dir(campaign.campaign_id))
        for k in _LEAVES:
            acc[k] += split[k]
    entries = [
        DatasetStorageEntry(dataset_name=name, total_bytes=sum(acc.values()), **_leaf_fields(acc))
        for name, acc in by_dataset.items()
    ]
    entries.sort(key=lambda e: e.total_bytes, reverse=True)
    return DatasetStorageResponse(total_bytes=sum(e.total_bytes for e in entries), datasets=entries)


# --- workspace rollup — accounts for 100% of the tenant's disk ----------------


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
        description="Cross-campaign reuse caches (measurements/ + optimizer_reuse/) — survive delete"
    )
    other_bytes: int = Field(
        description="Everything else under the tenant: sessions, workspace ledger, dataset/backend stores"
    )
    campaigns: list[WorkspaceStorageEntry] = Field(
        description="Fattest-first per-campaign totals (active + archived)"
    )


@campaigns_router.get("/workspace/storage", response_model=WorkspaceStorageResponse)
def get_workspace_storage(stores: StoresDep) -> WorkspaceStorageResponse:
    """Per-campaign on-disk slices across the caller's whole workspace, fattest first,
    plus the shared caches and a residual ``other`` slice so the grand total equals the
    tenant's real footprint — answers "where did the bucket sizes go?", nothing excluded.
    Includes archived campaigns — they stay in ``campaigns/``, flagged, not moved."""
    owner = str(stores.identity.user_id)
    entries: list[WorkspaceStorageEntry] = []
    campaigns_total = 0
    for campaign in stores.campaigns.list_campaigns(lifecycle="all", owner_user_id=owner):
        acc = _campaign_split(stores.campaigns.campaign_root_dir(campaign.campaign_id))
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
    shared = _dir_size(base / "measurements") + _dir_size(base / "optimizer_reuse")
    total = _dir_size(base)
    return WorkspaceStorageResponse(
        total_bytes=total,
        shared_cache_bytes=shared,
        other_bytes=max(0, total - campaigns_total - shared),
        campaigns=entries,
    )
