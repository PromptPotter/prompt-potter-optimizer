"""Campaign registry + per-cycle live reads.

The campaign registry (``/backends/{backend_id}/campaigns/...``) and the
cycle live reads (``/campaigns/{cycle_id}/...``) ride one ``APIRouter``
instance because main.py mounts it at ``/api/v1`` with a single prefix.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import ResumeCheckpointKind, ResumeCheckpointRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import Stores, campaign_dir_for, root_dir_for
from promptpotter.presentation.api.deps import StoreDep, read_text_or_404

logger = logging.getLogger(__name__)

campaigns_router = APIRouter()

# Family-root file-level artifacts (mirror of tests/test_invariants.py::ROOT_TELEMETRY_ARTIFACTS).
_FAMILY_FILE_LEVEL_ARTIFACTS = ("dashboard.json",)
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024  # 2 MiB
_MAX_FILE_ENTRIES = 5000

_TEXT_SUFFIXES = {".txt", ".jsonl", ""}


class CampaignSummary(BaseModel):
    campaign_id: str = Field(description="Unique campaign identifier")
    name: str = Field(description="Human-readable campaign name")
    status: str = Field(description="Campaign status: active, completed, or stopped")
    n_rounds: int = Field(description="Total number of completed round_data rounds")
    best_accuracy: float = Field(description="Highest accuracy achieved across all rounds")
    origin_accuracy: float = Field(description="Initial accuracy before optimization")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignSummary] = Field(description="List of campaign summaries")
    total: int = Field(description="Total number of campaigns")


class RoundSummary(BaseModel):
    round_id: str = Field(description="Unique round_data identifier")
    round: int = Field(description="Round number within the campaign")
    label: str = Field(description="Human-readable label (e.g. 'round_3')")
    prompt_fields_id: str = Field(description="OptSearchPoint ID for this round_data's prompt")
    accuracy: float = Field(description="Accuracy achieved in this round_data")
    hits: int = Field(description="Number of correct matches")
    total: int = Field(description="Total number of evaluated samples")
    improved: bool = Field(description="Whether this round_data improved over the previous best")
    created_at: str = Field(description="ISO 8601 creation timestamp")


class CampaignDetailResponse(CampaignSummary):
    backend_id: str = Field(description="Backend this campaign optimizes against")
    config: dict[str, Any] = Field(description="Full campaign configuration used for this run")
    best_round_id: str | None = Field(description="Trial ID of the best-performing round")
    rounds: list[RoundSummary] = Field(description="Ordered list of round_data summaries")
    langfuse_trace_id: str | None = Field(
        default=None,
        description="Langfuse trace ID if observability is enabled",
    )


@campaigns_router.get(
    "/backends/{backend_id}/campaigns",
    response_model=CampaignListResponse,
)
async def list_campaigns(
    store: StoreDep,
    backend_id: str,
):
    """List all campaigns for a backend."""
    campaigns = store.campaigns.list_all(backend_id)
    return CampaignListResponse(
        campaigns=[CampaignSummary(**c) for c in campaigns],
        total=len(campaigns),
    )


@campaigns_router.get(
    "/backends/{backend_id}/campaigns/{campaign_id}",
    response_model=CampaignDetailResponse,
)
async def get_campaign(
    store: StoreDep,
    backend_id: str,
    campaign_id: str,
):
    """Get campaign detail with round_data summaries."""
    data = store.campaigns.load(backend_id, campaign_id)
    if data is None:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    return CampaignDetailResponse(**data)


@campaigns_router.get(
    "/backends/{backend_id}/campaigns/{campaign_id}/rounds/{round_num}",
    response_model=dict[str, Any],
)
async def get_trial(
    store: StoreDep,
    backend_id: str,
    campaign_id: str,
    round_num: int,
):
    """Get full round_data detail for a specific round."""
    round_data = store.campaigns.load_round_file(backend_id, campaign_id, round_num)
    if round_data is None:
        raise HTTPException(404, f"Trial round {round_num} not found")
    return round_data


# ---------------------------------------------------------------------------
# Per-cycle live reads — dashboard, log, ledger
# Webapp-facing endpoints that pass through the on-disk artifacts the
# operator workflows already use, plus filtered ledger views (decisions,
# forks) derived from the typed CycleRecord stream.
# ---------------------------------------------------------------------------


class CycleRecordEnvelope(BaseModel):
    """One typed ledger record + its offset.

    ``record_type`` discriminates ResumeCheckpointRecord / PhaseRecord / SnapshotRecord;
    ``payload`` carries the model's fields verbatim (Pydantic
    model_dump). Consumers either route by record_type or filter on
    ResumeCheckpointRecord.kind for gating events.
    """

    offset: int = Field(description="Position in events.jsonl, monotonic per cycle")
    record_type: str = Field(description="One of 'decision' | 'phase' | 'snapshot'")
    payload: dict[str, Any] = Field(description="Verbatim record fields")


class LedgerSliceResponse(BaseModel):
    cycle_id: str = Field(description="Cycle the records belong to")
    records: list[CycleRecordEnvelope] = Field(description="Records since the requested offset")
    next_offset: int = Field(description="Pass back as ?since= for incremental polling")


class DecisionEnvelope(BaseModel):
    offset: int = Field(description="Position in the cycle's ledger")
    kind: str = Field(description="ResumeCheckpointKind value (round_winner, fork_cut, ...)")
    round: int | None = Field(default=None, description="Round the decision was made in")
    inputs_ref: dict[str, Any] = Field(description="Inputs the decision was derived from")
    outcome: Any = Field(description="The decision's outcome")
    data: dict[str, Any] = Field(description="Archival sidecar (LLM output, diagnostics)")
    timestamp: str = Field(description="ISO 8601 emit time")


class DecisionsResponse(BaseModel):
    cycle_id: str = Field(description="Cycle the decisions belong to")
    decisions: list[DecisionEnvelope] = Field(
        description="All ResumeCheckpointRecord records in append order"
    )


class ForkRef(BaseModel):
    fork_cycle_id: str = Field(description="The forked sibling cycle's id")
    from_round: int = Field(description="Round at which the parent was cut")
    forked_at: str = Field(description="ISO 8601 fork time (from FORK_CUT.data.forked_at)")


class ForksResponse(BaseModel):
    parent_cycle_id: str = Field(description="The parent cycle this list applies to")
    forks: list[ForkRef] = Field(description="Children minted from this parent")


class LogMdResponse(BaseModel):
    cycle_id: str = Field(description="Cycle the markdown belongs to")
    markdown: str = Field(description="Verbatim log.md source")


def _open_cycle_ledger_or_404(cycle_id: str, store: Stores) -> CycleEventLog:
    """Open the per-cycle ledger; 404 if the cycle dir doesn't exist."""
    cycle_dir = campaign_dir_for(store.base_dir, cycle_id)
    if not cycle_dir.exists():
        raise HTTPException(404, f"Cycle '{cycle_id}' not found")
    return CycleEventLog.open(CycleDir(cycle_dir))


def _record_to_envelope(record: Any, offset: int) -> CycleRecordEnvelope:
    return CycleRecordEnvelope(
        offset=offset,
        record_type=record.record_type,
        payload=record.model_dump(),
    )


@campaigns_router.get(
    "/campaigns/{cycle_id}/log_md",
    response_model=LogMdResponse,
)
async def get_cycle_log_md(store: StoreDep, cycle_id: str) -> LogMdResponse:
    """Pre-rendered log.md (per-cycle audit) — markdown source in a typed envelope."""
    cycle_dir = campaign_dir_for(store.base_dir, cycle_id)
    text = read_text_or_404(cycle_dir / "log.md", "log.md")
    return LogMdResponse(cycle_id=cycle_id, markdown=text)


@campaigns_router.get(
    "/campaigns/{cycle_id}/ledger",
    response_model=LedgerSliceResponse,
)
async def get_cycle_ledger(
    store: StoreDep,
    cycle_id: str,
    since: int = Query(0, ge=0, description="Skip records before this offset"),
    limit: int = Query(1000, ge=1, le=50_000, description="Max records to return"),
):
    """Typed CycleRecord stream from the cycle's events.jsonl, since the given offset.

    Pass back ``next_offset`` as ``?since=`` for incremental polling —
    the webapp's primary live-state read path. Up to ``limit`` records
    per call so the operator can stream a long history without one-shot
    huge responses.
    """
    ledger = _open_cycle_ledger_or_404(cycle_id, store)
    records: list[CycleRecordEnvelope] = []
    offset = since
    for rec in ledger.iter(since=since):
        records.append(_record_to_envelope(rec, offset))
        offset += 1
        if len(records) >= limit:
            break
    return LedgerSliceResponse(
        cycle_id=cycle_id,
        records=records,
        next_offset=offset,
    )


@campaigns_router.get(
    "/campaigns/{cycle_id}/decisions",
    response_model=DecisionsResponse,
)
async def get_cycle_decisions(store: StoreDep, cycle_id: str):
    """All ResumeCheckpointRecord records from the cycle's ledger, in append order.

    Filtered view over ``GET /ledger`` for the common gating-event use
    case. Includes archival kinds (probe, fork_cut) — consumers filter
    by ``kind`` if they only want REPLAYED ones.
    """
    ledger = _open_cycle_ledger_or_404(cycle_id, store)
    out: list[DecisionEnvelope] = []
    for offset, rec in enumerate(ledger.iter()):
        if isinstance(rec, ResumeCheckpointRecord):
            out.append(
                DecisionEnvelope(
                    offset=offset,
                    kind=rec.kind.value,
                    round=rec.round,
                    inputs_ref=rec.inputs_ref,
                    outcome=rec.outcome,
                    data=rec.data,
                    timestamp=rec.timestamp,
                )
            )
    return DecisionsResponse(cycle_id=cycle_id, decisions=out)


@campaigns_router.get(
    "/campaigns/{cycle_id}/forks",
    response_model=ForksResponse,
)
async def get_cycle_forks(store: StoreDep, cycle_id: str):
    """Sibling forks minted from this cycle, derived from FORK_CUT records.

    Each fork's metadata comes from a single ``ResumeCheckpointRecord(kind=FORK_CUT)``
    in the parent's ledger — ``outcome`` is the child cycle_id,
    ``inputs_ref.from_round`` is the cut point, ``data.forked_at`` is
    the wall-clock fork time.
    """
    ledger = _open_cycle_ledger_or_404(cycle_id, store)
    forks: list[ForkRef] = []
    for rec in ledger.iter():
        if isinstance(rec, ResumeCheckpointRecord) and rec.kind is ResumeCheckpointKind.FORK_CUT:
            forks.append(
                ForkRef(
                    fork_cycle_id=str(rec.outcome),
                    from_round=int(rec.inputs_ref.get("from_round", -1)),
                    forked_at=str(rec.data.get("forked_at", "")),
                )
            )
    return ForksResponse(parent_cycle_id=cycle_id, forks=forks)


# ---------------------------------------------------------------------------
# File-tree reads — recursive listing + content for one file under the
# cycle dir or the family-root telemetry artifacts.
# ---------------------------------------------------------------------------


class FileEntry(BaseModel):
    path: str = Field(description="Path relative to the scope root, forward slashes")
    scope: Literal["cycle", "family"] = Field(description="Which root the path is under")
    size: int = Field(description="File size in bytes")
    mtime: str = Field(description="ISO 8601 UTC modification time")


class FilesResponse(BaseModel):
    cycle_id: str
    is_fork: bool = Field(description="True when cycle dir != family-root dir")
    entries: list[FileEntry]


class FileContentResponse(BaseModel):
    cycle_id: str
    scope: Literal["cycle", "family"]
    path: str
    size: int
    mtime: str
    content_type: Literal["json", "markdown", "log", "text", "binary"]
    content: str | None = Field(
        default=None,
        description="UTF-8 text content; None when binary or oversized",
    )


def _walk_files(root: Path) -> Iterator[Path]:
    """Walk *root* recursively, yielding files. Skip dotfiles except ``.cache/``."""
    if not root.exists():
        return
    for entry in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name.startswith(".") and entry.name != ".cache":
            continue
        if entry.is_dir():
            yield from _walk_files(entry)
        elif entry.is_file():
            yield entry


def _iso_mtime(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat()


def _resolve_safe_file(scope_root: Path, raw_path: str) -> Path:
    """Validate *raw_path* against escape attempts and confine it under *scope_root*."""
    if not raw_path or ".." in raw_path or "\\" in raw_path or raw_path.startswith("/"):
        raise HTTPException(400, "Invalid path")
    scope_root_resolved = scope_root.resolve()
    resolved = (scope_root / raw_path).resolve()
    if not resolved.is_relative_to(scope_root_resolved):
        raise HTTPException(400, "Path escapes scope root")
    if not resolved.is_file():
        raise HTTPException(404, f"File not found: {raw_path}")
    return resolved


def _classify_suffix(suffix: str) -> Literal["json", "markdown", "log", "text"] | None:
    if suffix == ".json":
        return "json"
    if suffix == ".md":
        return "markdown"
    if suffix == ".log":
        return "log"
    if suffix in _TEXT_SUFFIXES:
        return "text"
    return None


@campaigns_router.get(
    "/campaigns/{cycle_id}/files",
    response_model=FilesResponse,
)
async def list_cycle_files(store: StoreDep, cycle_id: str) -> FilesResponse:
    """Recursive file tree for the cycle dir + family-root telemetry artifacts."""
    cycle_dir = campaign_dir_for(store.base_dir, cycle_id)
    root_dir = root_dir_for(store.base_dir, cycle_id)
    if not cycle_dir.exists():
        raise HTTPException(404, f"Cycle '{cycle_id}' not found")

    is_fork = cycle_dir.resolve() != root_dir.resolve()
    entries: list[FileEntry] = []

    for f in _walk_files(cycle_dir):
        entries.append(
            FileEntry(
                path=f.relative_to(cycle_dir).as_posix(),
                scope="cycle",
                size=f.stat().st_size,
                mtime=_iso_mtime(f),
            )
        )
        if len(entries) > _MAX_FILE_ENTRIES:
            raise HTTPException(413, f"Too many entries in cycle dir (>{_MAX_FILE_ENTRIES})")

    if is_fork:
        for name in _FAMILY_FILE_LEVEL_ARTIFACTS:
            f = root_dir / name
            if f.is_file():
                entries.append(
                    FileEntry(
                        path=name,
                        scope="family",
                        size=f.stat().st_size,
                        mtime=_iso_mtime(f),
                    )
                )

    return FilesResponse(cycle_id=cycle_id, is_fork=is_fork, entries=entries)


@campaigns_router.get(
    "/campaigns/{cycle_id}/file",
    response_model=FileContentResponse,
)
async def get_cycle_file(
    store: StoreDep,
    cycle_id: str,
    scope: Literal["cycle", "family"] = Query(..., description="cycle | family"),
    path: str = Query(..., description="Relative path under the chosen scope root"),
) -> FileContentResponse:
    """Read the contents of one file under the cycle or family-root scope."""
    if scope == "cycle":
        scope_root = campaign_dir_for(store.base_dir, cycle_id)
    else:
        scope_root = root_dir_for(store.base_dir, cycle_id)
    if not scope_root.exists():
        raise HTTPException(404, f"Cycle '{cycle_id}' not found")

    # Family-rooted artifacts (currently just ``dashboard.json``) live only
    # at the family root by design — ``LiveDashboardView`` is bound there
    # and forks share that one stream. When a fork is asked for one of
    # these via scope=cycle, auto-fall-back to the family root so the
    # webapp doesn't 404 on every fork in the sidebar.
    if scope == "cycle" and path in _FAMILY_FILE_LEVEL_ARTIFACTS:
        cycle_path = scope_root / path
        if not cycle_path.is_file():
            family_root = root_dir_for(store.base_dir, cycle_id)
            if family_root.exists() and family_root.resolve() != scope_root.resolve():
                scope_root = family_root

    resolved = _resolve_safe_file(scope_root, path)
    size = resolved.stat().st_size
    mtime = _iso_mtime(resolved)
    classification = _classify_suffix(resolved.suffix.lower())

    if size > _MAX_PREVIEW_BYTES:
        return FileContentResponse(
            cycle_id=cycle_id,
            scope=scope,
            path=path,
            size=size,
            mtime=mtime,
            content_type="text",
            content=None,
        )

    if classification is None:
        try:
            text = resolved.read_text(encoding="utf-8")
            return FileContentResponse(
                cycle_id=cycle_id,
                scope=scope,
                path=path,
                size=size,
                mtime=mtime,
                content_type="text",
                content=text,
            )
        except UnicodeDecodeError:
            return FileContentResponse(
                cycle_id=cycle_id,
                scope=scope,
                path=path,
                size=size,
                mtime=mtime,
                content_type="binary",
                content=None,
            )

    if classification == "text":
        try:
            text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return FileContentResponse(
                cycle_id=cycle_id,
                scope=scope,
                path=path,
                size=size,
                mtime=mtime,
                content_type="binary",
                content=None,
            )
        return FileContentResponse(
            cycle_id=cycle_id,
            scope=scope,
            path=path,
            size=size,
            mtime=mtime,
            content_type="text",
            content=text,
        )

    return FileContentResponse(
        cycle_id=cycle_id,
        scope=scope,
        path=path,
        size=size,
        mtime=mtime,
        content_type=classification,
        content=resolved.read_text(encoding="utf-8"),
    )


__all__ = [
    "CampaignDetailResponse",
    "CampaignListResponse",
    "CampaignSummary",
    "CycleRecordEnvelope",
    "DecisionEnvelope",
    "DecisionsResponse",
    "FileContentResponse",
    "FileEntry",
    "FilesResponse",
    "ForkRef",
    "ForksResponse",
    "LedgerSliceResponse",
    "LogMdResponse",
    "RoundSummary",
    "campaigns_router",
]
