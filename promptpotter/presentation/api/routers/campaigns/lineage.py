"""Campaign lineage — every cycle in a campaign + each cycle's rounds with
candidates + the parent-round where each fork was cut.

One round-trip from the webapp; per-cycle index.json reads are batched
server-side. Backs the cross-cycle search-point cladogram in the dashboard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import ResumeCheckpointKind, ResumeCheckpointRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import cycle_dir_for
from promptpotter.infrastructure.store.base import read_json_optional
from promptpotter.infrastructure.store.paths import sibling_kind
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


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
        fork_block: dict[str, Any] = _fork if isinstance(_fork, dict) else {}
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
                sibling_kind=str(index.get("sibling_kind") or sibling_kind(cid)),
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
