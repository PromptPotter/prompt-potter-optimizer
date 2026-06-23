"""On-disk size — the figures the sidebar hover card, the Files-view cake, and the
account storage panel show. Read-only.

ONE taxonomy, MECE: every byte in a campaign tree lands in exactly one of six leaves
that sum to the on-disk total. The top-level axis is the operator's mental model —
**Connector vs Loop vs Dataset** — and **Loop** breaks into four:

- ``dataset``   — the langfuse ground-truth mirror (the input-data copy; biggest chunk)
- ``connector`` — what the backend produced/consumed (its per-node I/O cache + the
                  per-sample result arrays it wrote into the public round files)
- ``state``     — the loop's resume point (round searchpoint state + ``.overrides``)
- ``trace``     — loop telemetry (``streams``, rendered ``prompts``, the langfuse loop trace)
- ``history``   — the durable event spine (``ledger.jsonl``)
- ``reports``   — the readable output (manifest + dashboard/index/log/review + hard_samples)

The "keepsake" that ``delete --keep-results`` spares (``reports`` + the langfuse loop
trace) is a cross-cutting subset, not a leaf — surfaced as a UI note, never a summed
figure, so the partition stays MECE.

The workspace rollup (`GET /workspace/storage`) additionally accounts for the shared,
cross-campaign caches (``measurements/`` + ``archive/optimizer_calls/``) and a residual
``other`` slice (sessions, the workspace ledger, dataset/backend stores, …) so the grand
total equals the tenant's real on-disk footprint — nothing excluded.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import NotFoundError

# Readable-output files (anywhere in the tree) → the ``reports`` leaf.
_REPORT_NAMES = frozenset(
    {"campaign.json", "dashboard.json", "index.json", "review.md", "log.md", "hard_samples.json"}
)
# Per-sample arrays inside a public round file that the backend produced → ``connector``.
_CONNECTOR_ROUND_KEYS = ("results", "all_candidate_results")

# Loop's four leaves, then the full six (top-level Connector / Loop / Dataset flattened).
_LOOP_LEAVES = ("state", "trace", "history", "reports")
_LEAVES = ("dataset", "connector", *_LOOP_LEAVES)


def _is_public_round(rel: Path) -> bool:
    """True for ``cycles/{cid}/rounds/round_*.json`` — the resume file (not the
    ``.runtime/cache`` audit copy). The lone intra-file straddler."""
    return (
        ".runtime" not in rel.parts
        and "rounds" in rel.parts
        and rel.name.startswith("round_")
        and rel.suffix == ".json"
    )


def _round_connector_bytes(path: Path) -> int:
    """Serialized size of the per-sample arrays the backend produced in one round file."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(doc, dict):
        return 0
    return sum(len(json.dumps(doc[k])) for k in _CONNECTOR_ROUND_KEYS if k in doc)


def _leaf(rel: Path) -> str:
    """Classify one file (path relative to the campaign root) into its MECE leaf.

    First match wins; the final ``else`` makes the partition exhaustive. Public round
    files are handled by the caller (their connector arrays split out, remainder ``state``)
    and never reach here.
    """
    parts = rel.parts
    if "langfuse" in parts:
        i = parts.index("langfuse")
        sub = parts[i + 1] if i + 1 < len(parts) else ""
        return "dataset" if sub == "datasets" else "trace"
    if ".runtime" in parts:
        j = parts.index(".runtime")
        sub = parts[j + 1] if j + 1 < len(parts) else ""
        if sub == "cache":
            return "connector"
        if rel.name == "ledger.jsonl":
            return "history"
        return "trace"  # streams/ + anything else under .runtime → loop telemetry
    if rel.name in _REPORT_NAMES:
        return "reports"
    if ".overrides" in parts:
        return "state"
    return "trace"  # prompts/, sweeps/, residual → loop telemetry


def _campaign_split(root: Path) -> dict[str, int]:
    """One walk of a campaign tree → ``{leaf: bytes}`` over the six MECE leaves.

    The six values sum exactly to the tree's on-disk total (the public round file's
    connector arrays go to ``connector``, its remainder to ``state``).
    """
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
        rel = p.relative_to(root)
        if _is_public_round(rel):
            conn = min(_round_connector_bytes(p), size)
            acc["connector"] += conn
            acc["state"] += size - conn
        else:
            acc[_leaf(rel)] += size
    return acc


def _dir_size(root: Path) -> int:
    """Recursive on-disk byte total of a directory (0 if absent)."""
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
    """``{leaf: n}`` → ``{leaf_bytes: n}`` for the response models."""
    return {f"{k}_bytes": acc[k] for k in _LEAVES}


class CampaignStorageResponse(BaseModel):
    campaign_id: str = Field(description="The campaign measured")
    on_disk_bytes: int = Field(description="Whole campaign-dir footprint — sum of the six leaves")
    dataset_bytes: int = Field(description="langfuse ground-truth mirror (the input-data copy)")
    connector_bytes: int = Field(description="Backend-produced: node-I/O cache + per-sample arrays")
    state_bytes: int = Field(description="Loop resume point: round searchpoint state + overrides")
    trace_bytes: int = Field(description="Loop telemetry: streams, prompts, langfuse loop trace")
    history_bytes: int = Field(description="Loop event spine: ledger.jsonl")
    reports_bytes: int = Field(description="Readable output: manifest + reports + hard_samples")


@campaigns_router.get("/campaigns/{campaign_id}/storage", response_model=CampaignStorageResponse)
def get_campaign_storage(store: StoreDep, campaign_id: str) -> CampaignStorageResponse:
    """On-disk size of the campaign tree, split into the six MECE leaves. 404 on cross-user."""
    campaign = store.campaigns.load_campaign(campaign_id)
    if campaign is None or campaign.owner_user_id != str(store.identity.user_id):
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    acc = _campaign_split(store.campaigns.campaign_root_dir(campaign_id))
    return CampaignStorageResponse(
        campaign_id=campaign_id, on_disk_bytes=sum(acc.values()), **_leaf_fields(acc)
    )


# --- per-dataset tier breakdown (the Files-view "cake") -----------------------


class DatasetStorageEntry(BaseModel):
    dataset_name: str
    total_bytes: int = Field(description="On disk — the sum of the six leaves")
    dataset_bytes: int
    connector_bytes: int
    state_bytes: int
    trace_bytes: int
    history_bytes: int
    reports_bytes: int


class DatasetStorageResponse(BaseModel):
    total_bytes: int = Field(description="Grand total across the caller's datasets")
    datasets: list[DatasetStorageEntry] = Field(description="Fattest-first per-dataset leaf splits")


@campaigns_router.get("/workspace/storage-by-dataset", response_model=DatasetStorageResponse)
def get_storage_by_dataset(store: StoreDep) -> DatasetStorageResponse:
    """Per-dataset on-disk leaf breakdown (the Files-view 'cake') — every campaign of a
    dataset pooled, then split into the six MECE leaves. Includes archived campaigns; the
    shared measurement store is excluded (it's not per-dataset-owned)."""
    owner = str(store.identity.user_id)
    by_dataset: dict[str, dict[str, int]] = {}
    for campaign in store.campaigns.list_campaigns(lifecycle="all", owner_user_id=owner):
        acc = by_dataset.setdefault(campaign.dataset_name, dict.fromkeys(_LEAVES, 0))
        split = _campaign_split(store.campaigns.campaign_root_dir(campaign.campaign_id))
        for k in _LEAVES:
            acc[k] += split[k]
    entries = [
        DatasetStorageEntry(dataset_name=name, total_bytes=sum(acc.values()), **_leaf_fields(acc))
        for name, acc in by_dataset.items()
    ]
    entries.sort(key=lambda e: e.total_bytes, reverse=True)
    return DatasetStorageResponse(total_bytes=sum(e.total_bytes for e in entries), datasets=entries)


# --- workspace rollup — accounts for 100% of the tenant's disk ----------------


class WorkspaceStorageEntry(BaseModel):
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


class WorkspaceStorageResponse(BaseModel):
    total_bytes: int = Field(
        description="The tenant's real on-disk total — campaigns + caches + other"
    )
    shared_cache_bytes: int = Field(
        description="Cross-campaign reuse caches (measurements/ + optimizer_calls/) — survive delete"
    )
    other_bytes: int = Field(
        description="Everything else under the tenant: sessions, workspace ledger, dataset/backend stores"
    )
    campaigns: list[WorkspaceStorageEntry] = Field(
        description="Fattest-first per-campaign totals (active + archived)"
    )


@campaigns_router.get("/workspace/storage", response_model=WorkspaceStorageResponse)
def get_workspace_storage(store: StoreDep) -> WorkspaceStorageResponse:
    """Per-campaign on-disk slices across the caller's whole workspace, fattest first,
    plus the shared caches and a residual ``other`` slice so the grand total equals the
    tenant's real footprint — answers "where did the bucket sizes go?", nothing excluded.
    Includes archived campaigns (they live in the ``archive/`` recycle bin)."""
    owner = str(store.identity.user_id)
    entries: list[WorkspaceStorageEntry] = []
    campaigns_total = 0
    for campaign in store.campaigns.list_campaigns(lifecycle="all", owner_user_id=owner):
        acc = _campaign_split(store.campaigns.campaign_root_dir(campaign.campaign_id))
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
    base = store.base_dir
    shared = _dir_size(base / "measurements") + _dir_size(base / "archive" / "optimizer_calls")
    total = _dir_size(base)
    return WorkspaceStorageResponse(
        total_bytes=total,
        shared_cache_bytes=shared,
        other_bytes=max(0, total - campaigns_total - shared),
        campaigns=entries,
    )
