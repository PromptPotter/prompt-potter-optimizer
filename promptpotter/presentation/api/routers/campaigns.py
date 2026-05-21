"""Campaign registry + per-cycle live reads.

A campaign is a **forest**: ``campaign_id`` identifies one optimization
effort (a dataset + pipeline origin + context), and it holds N *sessions*
(re-runs of the same declaration) plus their fork descendants — every
cycle flat under ``campaigns/{campaign_id}/cycles/``. Every per-cycle path
resolution carries both ids.

``dashboard.json`` is session-scoped — one telemetry stream per session
(the session root + its forks share it) — and resolves at the session's
root cycle dir. The per-cycle dashboard route resolves
``root_cycle_id(cycle_id)`` server-side.
"""

from __future__ import annotations

import json
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
from promptpotter.infrastructure.store import (
    Stores,
    campaign_root_dir_for,
    cycle_dir_for,
    root_cycle_id,
)
from promptpotter.infrastructure.store.base import read_json_optional
from promptpotter.infrastructure.store.paths import session_index, sibling_kind
from promptpotter.presentation.api.deps import StoreDep, read_text_or_404

logger = logging.getLogger(__name__)

campaigns_router = APIRouter()

# Campaign-level file artifacts that live at the campaign dir, not a cycle dir.
# ``dashboard.json`` is NOT here — it is session-scoped and lives in the
# session's root cycle dir under ``cycles/``.
_CAMPAIGN_FILE_LEVEL_ARTIFACTS = ("campaign.json", "log.md", "hard_samples.json")
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024  # 2 MiB
_MAX_FILE_ENTRIES = 5000

_TEXT_SUFFIXES = {".txt", ".jsonl", ""}


# ---------------------------------------------------------------------------
# Campaign registry — real Campaign manifests, not cycles.
# ---------------------------------------------------------------------------


class CampaignSummary(BaseModel):
    campaign_id: str = Field(description="Stable campaign id ({dataset}__{origin hash})")
    dataset_name: str = Field(description="Dataset this campaign optimizes")
    label: str = Field(default="", description="Operator-supplied campaign label")
    status: str = Field(description="Status of the campaign's most-recent session")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    root_cycle_id: str = Field(description="The campaign's first session's root cycle id")
    backend_id: str = Field(default="", description="Backend this campaign optimizes against")
    session_count: int = Field(
        default=1, description="Number of sessions (re-runs of the declaration) in the campaign"
    )


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignSummary] = Field(description="List of campaign summaries")
    total: int = Field(description="Total number of campaigns")


class SessionSummary(BaseModel):
    """One session in a campaign's forest — a root cycle + its forks."""

    root_cycle_id: str = Field(description="The session's root cycle id (cycle_{hash}[_s{N}])")
    session_index: int = Field(description="1-based session ordinal within the campaign")
    status: str = Field(default="", description="Session root cycle status")
    n_rounds: int = Field(default=0, description="Rounds completed on the session root cycle")
    best_accuracy: float | None = Field(default=None, description="Best round accuracy")
    created_at: str = Field(default="", description="ISO 8601 session creation timestamp")
    updated_at: str = Field(default="", description="ISO 8601 last write")


class CampaignDetailResponse(CampaignSummary):
    root_content_hash: str = Field(
        default="", description="Content hash of the origin search point — the campaign identity"
    )
    config: dict[str, Any] = Field(description="Frozen CampaignConfig snapshot for this campaign")
    sessions: list[SessionSummary] = Field(
        description="Every session in the campaign's forest, ordered by session index"
    )


def _campaign_summary(campaign: Any, session_count: int) -> CampaignSummary:
    return CampaignSummary(
        campaign_id=campaign.campaign_id,
        dataset_name=campaign.dataset_name,
        label=campaign.label,
        status=campaign.status,
        created_at=campaign.created_at,
        root_cycle_id=campaign.root_cycle_id,
        backend_id=campaign.backend_id,
        session_count=session_count,
    )


def _session_summaries(store: Stores, campaign_id: str) -> list[SessionSummary]:
    """Build the campaign's session list from its root cycles' index.json."""
    out: list[SessionSummary] = []
    for e in store.campaigns.enumerate_cycles():
        if e["campaign_id"] != campaign_id or not e["is_root"]:
            continue
        out.append(
            SessionSummary(
                root_cycle_id=e["cycle_id"],
                session_index=session_index(e["cycle_id"]),
                status=e["status"],
                n_rounds=e["n_rounds"],
                best_accuracy=e["best_accuracy"],
                created_at=e["created_at"],
                updated_at=e["updated_at"],
            )
        )
    out.sort(key=lambda s: s.session_index)
    return out


@campaigns_router.get("/campaigns", response_model=CampaignListResponse)
async def list_campaigns(
    store: StoreDep,
    dataset: str | None = Query(default=None, description="Filter to one dataset"),
) -> CampaignListResponse:
    """Every campaign on disk, newest first, optionally filtered by dataset."""
    campaigns = store.campaigns.list_campaigns(dataset)
    campaigns.sort(key=lambda c: c.created_at, reverse=True)
    return CampaignListResponse(
        campaigns=[
            _campaign_summary(c, len(store.campaigns.list_sessions(c.campaign_id)))
            for c in campaigns
        ],
        total=len(campaigns),
    )


@campaigns_router.get("/campaigns/{campaign_id}", response_model=CampaignDetailResponse)
async def get_campaign(store: StoreDep, campaign_id: str) -> CampaignDetailResponse:
    """Campaign manifest detail + its session forest."""
    campaign = store.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    sessions = _session_summaries(store, campaign_id)
    return CampaignDetailResponse(
        campaign_id=campaign.campaign_id,
        dataset_name=campaign.dataset_name,
        label=campaign.label,
        status=campaign.status,
        created_at=campaign.created_at,
        root_cycle_id=campaign.root_cycle_id,
        backend_id=campaign.backend_id,
        session_count=len(sessions) or 1,
        root_content_hash=campaign.root_content_hash,
        config=campaign.config,
        sessions=sessions,
    )


# ---------------------------------------------------------------------------
# Per-cycle reads — index, rounds, dashboard, log, ledger.
# All routes carry (campaign_id, cycle_id).
# ---------------------------------------------------------------------------


class CycleSummary(BaseModel):
    campaign_id: str
    cycle_id: str
    sibling_kind: Literal["root", "fork", "diag", "sweep"]
    is_root: bool
    parent_cycle_id: str | None = None
    status: str = ""
    n_rounds: int = 0
    best_accuracy: float | None = None
    origin_accuracy: float | None = None
    created_at: str = ""
    updated_at: str = ""


class CampaignCyclesResponse(BaseModel):
    campaign_id: str
    cycles: list[CycleSummary] = Field(description="Every cycle in the campaign's lineage tree")


class RoundSummary(BaseModel):
    round: int = Field(description="Round number within the cycle")
    label: str = Field(description="Human-readable label (winner's L1 description)")
    accuracy: float | None = Field(default=None, description="Round-level accuracy (winner)")
    improved: bool = Field(default=False, description="Whether this round improved over best")


class CycleDetailResponse(CycleSummary):
    backend_id: str = Field(default="", description="Backend this cycle optimizes against")
    best_round_id: str | None = Field(default=None, description="Round id of the best round")
    rounds: list[RoundSummary] = Field(description="Ordered round summaries")


@campaigns_router.get("/campaigns/{campaign_id}/cycles", response_model=CampaignCyclesResponse)
async def list_campaign_cycles(store: StoreDep, campaign_id: str) -> CampaignCyclesResponse:
    """Every cycle in one campaign's lineage tree."""
    if store.campaigns.load_campaign(campaign_id) is None:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    cycles = [
        CycleSummary(
            campaign_id=e["campaign_id"],
            cycle_id=e["cycle_id"],
            sibling_kind=e["sibling_kind"],
            is_root=e["is_root"],
            parent_cycle_id=e["parent_cycle_id"],
            status=e["status"],
            n_rounds=e["n_rounds"],
            best_accuracy=e["best_accuracy"],
            created_at=e["created_at"],
            updated_at=e["updated_at"],
        )
        for e in store.campaigns.enumerate_cycles()
        if e["campaign_id"] == campaign_id
    ]
    return CampaignCyclesResponse(campaign_id=campaign_id, cycles=cycles)


def _round_summaries(index: dict[str, Any]) -> list[RoundSummary]:
    out: list[RoundSummary] = []
    rounds_raw = index.get("rounds")
    if not isinstance(rounds_raw, list):
        return out
    for r in rounds_raw:
        if not isinstance(r, dict):
            continue
        rn = r.get("round")
        if not isinstance(rn, int):
            continue
        out.append(
            RoundSummary(
                round=rn,
                label=str(r.get("label") or ""),
                accuracy=(
                    float(r["accuracy"]) if isinstance(r.get("accuracy"), int | float) else None
                ),
                improved=bool(r.get("improved", False)),
            )
        )
    return out


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}", response_model=CycleDetailResponse
)
async def get_cycle(store: StoreDep, campaign_id: str, cycle_id: str) -> CycleDetailResponse:
    """One cycle's ``index.json`` detail with round summaries."""
    index = store.campaigns.load(campaign_id, cycle_id)
    if index is None:
        raise HTTPException(404, f"Cycle not found: {campaign_id}/{cycle_id}")
    kind = sibling_kind(cycle_id)
    return CycleDetailResponse(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        sibling_kind=index.get("sibling_kind", kind),
        is_root=kind == "root",
        parent_cycle_id=index.get("parent_cycle_id"),
        status=str(index.get("status") or ""),
        n_rounds=int(index.get("n_rounds", 0)),
        best_accuracy=(
            float(index["best_accuracy"])
            if isinstance(index.get("best_accuracy"), int | float)
            else None
        ),
        origin_accuracy=(
            float(index["origin_accuracy"])
            if isinstance(index.get("origin_accuracy"), int | float)
            else None
        ),
        created_at=str(index.get("created_at") or ""),
        updated_at=str(index.get("updated_at") or ""),
        backend_id=str(index.get("backend_id") or ""),
        best_round_id=index.get("best_round_id"),
        rounds=_round_summaries(index),
    )


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/rounds/{round_num}",
    response_model=dict[str, Any],
)
async def get_round(
    store: StoreDep, campaign_id: str, cycle_id: str, round_num: int
) -> dict[str, Any]:
    """Full round detail for one round of one cycle."""
    round_data = store.campaigns.load_round_file(campaign_id, cycle_id, round_num)
    if round_data is None:
        raise HTTPException(404, f"Round {round_num} not found")
    return round_data


# ---------------------------------------------------------------------------
# Session-scoped telemetry — dashboard.json lives in the session's root
# cycle dir. The route accepts any cycle of the session and resolves the
# session-family root server-side.
# ---------------------------------------------------------------------------


@campaigns_router.get("/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard")
async def get_cycle_dashboard(store: StoreDep, campaign_id: str, cycle_id: str) -> dict[str, Any]:
    """Live session telemetry — ``cycles/{session_root}/dashboard.json``.

    ``dashboard.json`` is written once per session, into the session's
    root cycle dir; the session root and its forks share it. Pass any
    cycle of the session — the session-family root is resolved here.
    """
    session_root = root_cycle_id(cycle_id)
    path = cycle_dir_for(store.base_dir, campaign_id, session_root) / "dashboard.json"
    if not path.is_file():
        raise HTTPException(404, f"dashboard.json not present for {campaign_id}/{session_root}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Per-cycle live reads — log, ledger, decisions, forks.
# ---------------------------------------------------------------------------


class CycleRecordEnvelope(BaseModel):
    """One typed ledger record + its offset.

    ``record_type`` discriminates ResumeCheckpointRecord / PhaseRecord / SnapshotRecord;
    ``payload`` carries the model's fields verbatim (Pydantic model_dump).
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
    scope_id: str = Field(description="Campaign id or cycle id the markdown belongs to")
    markdown: str = Field(description="Verbatim log.md source")


def _open_cycle_ledger_or_404(campaign_id: str, cycle_id: str, store: Stores) -> CycleEventLog:
    """Open the per-cycle ledger; 404 if the cycle dir doesn't exist."""
    cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    if not cycle_dir.exists():
        raise HTTPException(404, f"Cycle '{campaign_id}/{cycle_id}' not found")
    return CycleEventLog.open(CycleDir(cycle_dir))


def _record_to_envelope(record: Any, offset: int) -> CycleRecordEnvelope:
    return CycleRecordEnvelope(
        offset=offset,
        record_type=record.record_type,
        payload=record.model_dump(),
    )


@campaigns_router.get("/campaigns/{campaign_id}/log_md", response_model=LogMdResponse)
async def get_campaign_log_md(store: StoreDep, campaign_id: str) -> LogMdResponse:
    """Campaign digest — ``campaigns/{campaign_id}/log.md`` in a typed envelope."""
    campaign_dir = campaign_root_dir_for(store.base_dir, campaign_id)
    text = read_text_or_404(campaign_dir / "log.md", "log.md")
    return LogMdResponse(scope_id=campaign_id, markdown=text)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/log_md",
    response_model=LogMdResponse,
)
async def get_cycle_log_md(store: StoreDep, campaign_id: str, cycle_id: str) -> LogMdResponse:
    """Pre-rendered per-cycle ``log.md`` — markdown source in a typed envelope."""
    cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    text = read_text_or_404(cycle_dir / "log.md", "log.md")
    return LogMdResponse(scope_id=cycle_id, markdown=text)


@campaigns_router.get("/campaigns/{campaign_id}/cycles/{cycle_id}/hard_samples")
async def get_cycle_hard_samples(
    store: StoreDep, campaign_id: str, cycle_id: str
) -> dict[str, Any]:
    """Cycle-scoped hard-sample artifact — ``cycles/{cycle_id}/hard_samples.json``.

    The Rasch fit over this cycle's rounds only. Campaign-scoped and
    dataset-scoped heatmaps are served elsewhere (campaign dir artifact /
    archive snapshot via the datasets router).
    """
    cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    if not cycle_dir.exists():
        raise HTTPException(404, f"Cycle '{campaign_id}/{cycle_id}' not found")
    path = cycle_dir / "hard_samples.json"
    if not path.is_file():
        raise HTTPException(404, "hard_samples.json not present (cycle has no rounds yet)")
    return json.loads(path.read_text(encoding="utf-8"))


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/ledger",
    response_model=LedgerSliceResponse,
)
async def get_cycle_ledger(
    store: StoreDep,
    campaign_id: str,
    cycle_id: str,
    since: int = Query(0, ge=0, description="Skip records before this offset"),
    limit: int = Query(1000, ge=1, le=50_000, description="Max records to return"),
) -> LedgerSliceResponse:
    """Typed CycleRecord stream from the cycle's events.jsonl, since the given offset.

    Pass back ``next_offset`` as ``?since=`` for incremental polling —
    the webapp's primary live-state read path. Up to ``limit`` records
    per call so the operator can stream a long history without one-shot
    huge responses.
    """
    ledger = _open_cycle_ledger_or_404(campaign_id, cycle_id, store)
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
    "/campaigns/{campaign_id}/cycles/{cycle_id}/decisions",
    response_model=DecisionsResponse,
)
async def get_cycle_decisions(
    store: StoreDep, campaign_id: str, cycle_id: str
) -> DecisionsResponse:
    """All ResumeCheckpointRecord records from the cycle's ledger, in append order.

    Filtered view over ``GET /ledger`` for the common gating-event use
    case. Includes archival kinds (probe, fork_cut) — consumers filter
    by ``kind`` if they only want REPLAYED ones.
    """
    ledger = _open_cycle_ledger_or_404(campaign_id, cycle_id, store)
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
    "/campaigns/{campaign_id}/cycles/{cycle_id}/forks",
    response_model=ForksResponse,
)
async def get_cycle_forks(store: StoreDep, campaign_id: str, cycle_id: str) -> ForksResponse:
    """Sibling forks minted from this cycle, derived from FORK_CUT records.

    Each fork's metadata comes from a single ``ResumeCheckpointRecord(kind=FORK_CUT)``
    in the parent's ledger — ``outcome`` is the child cycle_id,
    ``inputs_ref.from_round`` is the cut point, ``data.forked_at`` is
    the wall-clock fork time.
    """
    ledger = _open_cycle_ledger_or_404(campaign_id, cycle_id, store)
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
# Campaign lineage — every cycle in a campaign + each cycle's rounds with
# candidates + the parent-round where each fork was cut. One round-trip
# from the webapp; per-cycle index.json reads are batched server-side.
# Backs the cross-cycle search-point cladogram in the dashboard.
# ---------------------------------------------------------------------------


class CampaignLineageCandidate(BaseModel):
    candidate_id: str = Field(description="Stable id assigned at L1-score time")
    label: str = Field(default="", description="Short L1-generated description")
    accuracy: float | None = Field(default=None, description="Per-candidate accuracy")
    rank: int | None = Field(default=None, description="Final rank within the round")
    is_winner: bool = Field(default=False, description="True for the round's elected winner")


class CampaignLineageRound(BaseModel):
    round: int = Field(description="Round number within the cycle (1-indexed)")
    label: str = Field(default="", description="Round label — winner's L1 description")
    accuracy: float | None = Field(default=None, description="Round-level accuracy (winner)")
    candidates: list[CampaignLineageCandidate] = Field(
        description="All candidates scored this round, sorted by rank"
    )


class CampaignLineageCycle(BaseModel):
    cycle_id: str
    sibling_kind: Literal["root", "fork", "diag", "sweep"]
    # Immediate parent, read from index.json so sub-forks (forks of forks)
    # attach to their actual parent in the visual tree.
    immediate_parent_cycle_id: str | None
    # Round of the immediate parent at which this cycle's first round was
    # cut. None for roots; may be None for forks whose index didn't record it.
    fork_from_round: int | None
    # Candidate id at the parent's fork_from_round that this fork descends
    # from. Only set when index.json::fork carries from_candidate (operator
    # HITL forks); divergence/sweep forks attach at round-level only.
    fork_from_candidate_id: str | None
    # Fork creation trigger — drives the round-numbering convention.
    trigger: str
    # X-axis offset for this cycle's rounds in the campaign cladogram —
    # add to each round's ``round`` number to get its absolute column.
    round_column_offset: int
    status: str
    dataset_name: str
    best_accuracy: float | None
    rounds: list[CampaignLineageRound]


class CampaignLineageResponse(BaseModel):
    campaign_id: str
    cycles: list[CampaignLineageCycle] = Field(
        description="Every cycle in the campaign (root + forks + sweeps + diag). "
        "Sorted by cycle id; lay out via immediate_parent_cycle_id."
    )


def _extract_candidates(scoreboard: list[Any]) -> list[CampaignLineageCandidate]:
    out: list[CampaignLineageCandidate] = []
    for c in scoreboard:
        if not isinstance(c, dict):
            continue
        out.append(
            CampaignLineageCandidate(
                candidate_id=str(c.get("candidate_id") or ""),
                label=str(c.get("label") or ""),
                accuracy=(
                    float(c["accuracy"]) if isinstance(c.get("accuracy"), int | float) else None
                ),
                rank=(int(c["rank"]) if isinstance(c.get("rank"), int) else None),
                is_winner=bool(c.get("is_winner", False)),
            )
        )
    return out


def _fork_from_round_from_ledger(parent_dir: Path, child_cycle_id: str) -> int | None:
    """Find the FORK_CUT record in *parent_dir* whose outcome is *child_cycle_id*.

    Final fallback when index.json::fork::from_round doesn't carry the
    value. Returns None if the parent's ledger is missing or the record
    isn't there.
    """
    if not (parent_dir / "events.jsonl").is_file():
        return None
    try:
        ledger = CycleEventLog.open(CycleDir(parent_dir))
    except Exception:
        return None
    for rec in ledger.iter():
        if (
            isinstance(rec, ResumeCheckpointRecord)
            and rec.kind is ResumeCheckpointKind.FORK_CUT
            and str(rec.outcome) == child_cycle_id
        ):
            v = rec.inputs_ref.get("from_round")
            if isinstance(v, int):
                return v
    return None


def _filter_post_divergence_rounds(
    rounds: list[CampaignLineageRound], trigger: str, fork_from_round: int | None
) -> list[CampaignLineageRound]:
    """For divergence / sweep / diag forks, drop rounds inherited from the
    parent (round <= fork_from_round). Those rounds belong to the parent's
    lane and would visually overlap if rendered in the fork's lane.

    Operator HITL forks restart numbering at 1 so all their rounds are
    post-divergence by definition — return as-is.
    """
    if trigger == "operator_hitl":
        return rounds
    if fork_from_round is None:
        return rounds
    return [r for r in rounds if r.round > fork_from_round]


@campaigns_router.get(
    "/campaigns/{campaign_id}/lineage",
    response_model=CampaignLineageResponse,
)
async def get_campaign_lineage(store: StoreDep, campaign_id: str) -> CampaignLineageResponse:
    """Aggregated lineage for the whole campaign.

    One pass over every cycle in the campaign — reads each index.json
    (which already carries the per-round scoreboard) and supplements with
    a ledger scan for fork-cut rounds when the index doesn't have them.
    The tree is built from each cycle's ``parent_cycle_id``.
    """
    if store.campaigns.load_campaign(campaign_id) is None:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    enum_entries = [
        e for e in store.campaigns.enumerate_cycles() if e["campaign_id"] == campaign_id
    ]

    out_cycles: list[CampaignLineageCycle] = []
    for entry in sorted(enum_entries, key=lambda e: e["cycle_id"]):
        cid = entry["cycle_id"]
        cdir = cycle_dir_for(store.base_dir, campaign_id, cid)
        index = read_json_optional(cdir / "index.json")
        if not isinstance(index, dict):
            out_cycles.append(
                CampaignLineageCycle(
                    cycle_id=cid,
                    sibling_kind=sibling_kind(cid),
                    immediate_parent_cycle_id=entry["parent_cycle_id"],
                    fork_from_round=None,
                    fork_from_candidate_id=None,
                    trigger="",
                    round_column_offset=0,
                    status="missing",
                    dataset_name=entry["dataset_name"],
                    best_accuracy=None,
                    rounds=[],
                )
            )
            continue

        immediate_parent = index.get("parent_cycle_id") or None
        _fork = index.get("fork")
        fork_block: dict = _fork if isinstance(_fork, dict) else {}
        trigger = str(fork_block.get("trigger") or "")

        # Two sources for fork_from_round, tried in this order:
        #   1. index.json::fork::from_round
        #   2. parent ledger's FORK_CUT record (last-resort scan)
        from_round: int | None = None
        block_fr = fork_block.get("from_round")
        if isinstance(block_fr, int):
            from_round = block_fr
        elif immediate_parent:
            from_round = _fork_from_round_from_ledger(
                cycle_dir_for(store.base_dir, campaign_id, immediate_parent), cid
            )

        from_candidate = fork_block.get("from_candidate")
        from_candidate_str = (
            str(from_candidate) if isinstance(from_candidate, str) and from_candidate else None
        )

        rounds_raw = index.get("rounds")
        rounds_out: list[CampaignLineageRound] = []
        if isinstance(rounds_raw, list):
            for r in rounds_raw:
                if not isinstance(r, dict):
                    continue
                rn = r.get("round")
                if not isinstance(rn, int):
                    continue
                rounds_out.append(
                    CampaignLineageRound(
                        round=rn,
                        label=str(r.get("label") or ""),
                        accuracy=(
                            float(r["accuracy"])
                            if isinstance(r.get("accuracy"), int | float)
                            else None
                        ),
                        candidates=_extract_candidates(r.get("scoreboard") or []),
                    )
                )

        rounds_out = _filter_post_divergence_rounds(rounds_out, trigger, from_round)
        col_offset = from_round if trigger == "operator_hitl" and isinstance(from_round, int) else 0

        header_raw = index.get("header")
        header = header_raw if isinstance(header_raw, dict) else {}

        out_cycles.append(
            CampaignLineageCycle(
                cycle_id=cid,
                sibling_kind=str(index.get("sibling_kind") or sibling_kind(cid)),  # type: ignore[arg-type]
                immediate_parent_cycle_id=immediate_parent,
                fork_from_round=from_round,
                fork_from_candidate_id=from_candidate_str,
                trigger=trigger,
                round_column_offset=col_offset,
                status=str(index.get("status") or ""),
                dataset_name=str(header.get("dataset_name") or entry["dataset_name"]),
                best_accuracy=(
                    float(index["best_accuracy"])
                    if isinstance(index.get("best_accuracy"), int | float)
                    else None
                ),
                rounds=rounds_out,
            )
        )

    return CampaignLineageResponse(campaign_id=campaign_id, cycles=out_cycles)


# ---------------------------------------------------------------------------
# File-tree reads — recursive listing + content for one file under the
# cycle dir or the campaign-level artifacts.
# ---------------------------------------------------------------------------


class FileEntry(BaseModel):
    path: str = Field(description="Path relative to the scope root, forward slashes")
    scope: Literal["cycle", "campaign"] = Field(description="Which root the path is under")
    size: int = Field(description="File size in bytes")
    mtime: str = Field(description="ISO 8601 UTC modification time")


class FilesResponse(BaseModel):
    campaign_id: str
    cycle_id: str
    entries: list[FileEntry]


class FileContentResponse(BaseModel):
    campaign_id: str
    cycle_id: str
    scope: Literal["cycle", "campaign"]
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
    "/campaigns/{campaign_id}/cycles/{cycle_id}/files",
    response_model=FilesResponse,
)
async def list_cycle_files(store: StoreDep, campaign_id: str, cycle_id: str) -> FilesResponse:
    """Recursive file tree for the cycle dir + campaign-level artifacts."""
    cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    campaign_dir = campaign_root_dir_for(store.base_dir, campaign_id)
    if not cycle_dir.exists():
        raise HTTPException(404, f"Cycle '{campaign_id}/{cycle_id}' not found")

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

    for name in _CAMPAIGN_FILE_LEVEL_ARTIFACTS:
        f = campaign_dir / name
        if f.is_file():
            entries.append(
                FileEntry(
                    path=name,
                    scope="campaign",
                    size=f.stat().st_size,
                    mtime=_iso_mtime(f),
                )
            )

    return FilesResponse(campaign_id=campaign_id, cycle_id=cycle_id, entries=entries)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/file",
    response_model=FileContentResponse,
)
async def get_cycle_file(
    store: StoreDep,
    campaign_id: str,
    cycle_id: str,
    scope: Literal["cycle", "campaign"] = Query(..., description="cycle | campaign"),
    path: str = Query(..., description="Relative path under the chosen scope root"),
) -> FileContentResponse:
    """Read the contents of one file under the cycle or campaign scope."""
    if scope == "cycle":
        scope_root = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    else:
        scope_root = campaign_root_dir_for(store.base_dir, campaign_id)
    if not scope_root.exists():
        raise HTTPException(404, f"Scope root not found: {campaign_id}/{cycle_id}")

    resolved = _resolve_safe_file(scope_root, path)
    size = resolved.stat().st_size
    mtime = _iso_mtime(resolved)
    classification = _classify_suffix(resolved.suffix.lower())

    if size > _MAX_PREVIEW_BYTES:
        return FileContentResponse(
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            scope=scope,
            path=path,
            size=size,
            mtime=mtime,
            content_type="text",
            content=None,
        )

    if classification is None or classification == "text":
        try:
            text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return FileContentResponse(
                campaign_id=campaign_id,
                cycle_id=cycle_id,
                scope=scope,
                path=path,
                size=size,
                mtime=mtime,
                content_type="binary",
                content=None,
            )
        return FileContentResponse(
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            scope=scope,
            path=path,
            size=size,
            mtime=mtime,
            content_type="text",
            content=text,
        )

    return FileContentResponse(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        scope=scope,
        path=path,
        size=size,
        mtime=mtime,
        content_type=classification,
        content=resolved.read_text(encoding="utf-8"),
    )


__all__ = [
    "CampaignCyclesResponse",
    "CampaignDetailResponse",
    "CampaignLineageCandidate",
    "CampaignLineageCycle",
    "CampaignLineageResponse",
    "CampaignLineageRound",
    "CampaignListResponse",
    "CampaignSummary",
    "CycleDetailResponse",
    "CycleRecordEnvelope",
    "CycleSummary",
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
    "SessionSummary",
    "campaigns_router",
]
