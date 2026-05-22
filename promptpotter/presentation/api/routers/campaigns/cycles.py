"""Per-cycle reads — cycle list, cycle detail, rounds, dashboard.

All routes carry ``(campaign_id, cycle_id)``. ``dashboard.json`` is
session-scoped — the dashboard route accepts any cycle of the session and
resolves the session-family root server-side.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from promptpotter.infrastructure.store import cycle_dir_for, root_cycle_id
from promptpotter.infrastructure.store.paths import sibling_kind
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


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
    dashboard: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return dashboard
