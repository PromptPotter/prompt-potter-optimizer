"""Per-cycle live reads — log.md, ledger stream, decisions, forks, hard samples."""

from __future__ import annotations

from typing import Any

from fastapi import Query
from pydantic import BaseModel, Field

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import ResumeCheckpointKind, ResumeCheckpointRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import Stores, campaign_root_dir_for, cycle_dir_for
from promptpotter.infrastructure.store.io import read_json
from promptpotter.presentation.api.deps import StoreDep, get_cycle_dir_or_404, read_text_or_404
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import NotFoundError


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
    return CycleEventLog.open(CycleDir(get_cycle_dir_or_404(campaign_id, cycle_id, store)))


def _record_to_envelope(record: Any, offset: int) -> CycleRecordEnvelope:
    return CycleRecordEnvelope(
        offset=offset,
        record_type=record.record_type,
        payload=record.model_dump(),
    )


@campaigns_router.get("/campaigns/{campaign_id}/log_md", response_model=LogMdResponse)
def get_campaign_log_md(store: StoreDep, campaign_id: str) -> LogMdResponse:
    """Campaign digest — ``campaigns/{campaign_id}/log.md`` in a typed envelope."""
    campaign_dir = campaign_root_dir_for(store.base_dir, campaign_id)
    text = read_text_or_404(campaign_dir / "log.md", "log.md")
    return LogMdResponse(scope_id=campaign_id, markdown=text)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/log_md",
    response_model=LogMdResponse,
)
def get_cycle_log_md(store: StoreDep, campaign_id: str, cycle_id: str) -> LogMdResponse:
    """Pre-rendered per-cycle ``log.md`` — markdown source in a typed envelope."""
    cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    text = read_text_or_404(cycle_dir / "log.md", "log.md")
    return LogMdResponse(scope_id=cycle_id, markdown=text)


@campaigns_router.get("/campaigns/{campaign_id}/cycles/{cycle_id}/hard_samples")
def get_cycle_hard_samples(store: StoreDep, campaign_id: str, cycle_id: str) -> dict[str, Any]:
    """Cycle-scoped hard-sample artifact — ``cycles/{cycle_id}/hard_samples.json``.

    The Rasch fit over this cycle's rounds only. Campaign-scoped and
    dataset-scoped heatmaps are served elsewhere (campaign dir artifact /
    archive snapshot via the datasets router).
    """
    cycle_dir = get_cycle_dir_or_404(campaign_id, cycle_id, store)
    path = cycle_dir / "hard_samples.json"
    if not path.is_file():
        raise NotFoundError("hard_samples.json not present (cycle has no rounds yet)")
    artifact: dict[str, Any] = read_json(path)
    return artifact


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/ledger",
    response_model=LedgerSliceResponse,
)
def get_cycle_ledger(
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
def get_cycle_decisions(store: StoreDep, campaign_id: str, cycle_id: str) -> DecisionsResponse:
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
def get_cycle_forks(store: StoreDep, campaign_id: str, cycle_id: str) -> ForksResponse:
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
