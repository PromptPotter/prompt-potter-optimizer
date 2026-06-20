"""Per-cycle reads — cycle list, cycle detail, rounds, dashboard.

All routes carry ``(campaign_id, cycle_id)``. ``dashboard.json`` is
per-cycle — the dashboard route serves the viewed cycle's own file, so a
fork's chart shows the fork's trajectory, not the session root's.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from promptpotter.infrastructure.store import cycle_dir_for
from promptpotter.infrastructure.store.base import read_json_tolerant
from promptpotter.infrastructure.store.paths import sibling_kind
from promptpotter.presentation.api.deps import StoreDep, warming_payload
from promptpotter.presentation.api.routers.campaigns._conditional import (
    client_seen_at_or_after,
    http_date,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import NotFoundError


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
    human_intervened: bool = False


class CampaignCyclesResponse(BaseModel):
    campaign_id: str
    cycles: list[CycleSummary] = Field(description="Every cycle in the campaign's lineage tree")


class CycleRoundEntry(BaseModel):
    round: int = Field(description="Round number within the cycle")
    label: str = Field(description="Human-readable label (winner's L1 description)")
    accuracy: float | None = Field(default=None, description="Round-level accuracy (winner)")
    improved: bool = Field(default=False, description="Whether this round improved over best")


class CycleDetailResponse(CycleSummary):
    backend_id: str = Field(default="", description="Backend this cycle optimizes against")
    best_round_id: str | None = Field(default=None, description="Round id of the best round")
    rounds: list[CycleRoundEntry] = Field(description="Ordered round summaries")


@campaigns_router.get("/campaigns/{campaign_id}/cycles", response_model=CampaignCyclesResponse)
def list_campaign_cycles(store: StoreDep, campaign_id: str) -> CampaignCyclesResponse:
    """Every cycle in one campaign's lineage tree."""
    if store.campaigns.load_campaign(campaign_id) is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
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
            human_intervened=e.get("human_intervened", False),
        )
        for e in store.campaigns.enumerate_cycles()
        if e["campaign_id"] == campaign_id
    ]
    return CampaignCyclesResponse(campaign_id=campaign_id, cycles=cycles)


def _round_summaries(index: dict[str, Any]) -> list[CycleRoundEntry]:
    out: list[CycleRoundEntry] = []
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
            CycleRoundEntry(
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
def get_cycle(store: StoreDep, campaign_id: str, cycle_id: str) -> CycleDetailResponse:
    """One cycle's ``index.json`` detail with round summaries."""
    index = store.campaigns.load(campaign_id, cycle_id)
    if index is None:
        raise NotFoundError(f"Cycle not found: {campaign_id}/{cycle_id}")
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
        backend_id=str((index.get("header") or {}).get("backend_id") or ""),
        best_round_id=index.get("best_round_id"),
        rounds=_round_summaries(index),
    )


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/rounds/{round_num}",
    response_model=dict[str, Any],
)
def get_round(store: StoreDep, campaign_id: str, cycle_id: str, round_num: int) -> dict[str, Any]:
    """Full round detail for one round of one cycle."""
    round_data = store.campaigns.load_round_file(campaign_id, cycle_id, round_num)
    if round_data is None:
        raise NotFoundError(f"Round {round_num} not found")
    return round_data


@campaigns_router.get("/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard")
def get_cycle_dashboard(
    request: Request, store: StoreDep, campaign_id: str, cycle_id: str
) -> Response:
    """Live telemetry for the viewed cycle — ``cycles/{cycle_id}/dashboard.json``.

    ``dashboard.json`` is per-cycle: every cycle (root, fork, sweep, diag)
    owns its own live file, stamped with its own ``cycle_id``. The route
    serves the file for the cycle passed in — no session-root collapse.

    Honors ``If-Modified-Since`` and returns ``304 Not Modified`` when the
    on-disk mtime hasn't advanced — keeps the 2 s webapp poll cheap during
    quiescent stretches. When ``dashboard.json`` does not yet exist (fresh
    campaign before origin has flushed its first snapshot), returns a
    ``warming_up`` payload at 200 instead of 404 so the webapp can render a
    "campaign initialising" placeholder rather than appear offline.
    """
    cycle_path = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    path = cycle_path / "dashboard.json"
    present = path.is_file()

    # Conditional-GET once, before reading the body: Last-Modified rides the
    # dashboard mtime when present, else the cycle dir's mtime (so polling
    # clients still get cheap 304s while a fresh campaign warms up). The read
    # only happens after the 304 check passes — keeps the 2 s poll cheap.
    try:
        mtime_epoch = (path if present else cycle_path).stat().st_mtime
        headers = {"Last-Modified": http_date(mtime_epoch)}
        if client_seen_at_or_after(request.headers.get("if-modified-since"), mtime_epoch):
            return Response(status_code=304, headers=headers)
    except FileNotFoundError:
        headers = {}

    # ``run_phase`` rides dashboard.json itself (declared by the runner, projected
    # by LiveDashboardView) — the webapp reads it straight off the 2 s poll, so a
    # paused run reads "paused" with no separate /runstate round-trip. The spend
    # cap rides ``dashboard.json::spend.budget_usd`` already; the deleted
    # /runstate endpoint's ``spend_cap_usd`` was unused.
    body = read_json_tolerant(path) if present else None
    if body is None:
        # Missing OR corrupt (half-written / truncated): degrade to the warming
        # placeholder rather than 500 on the 2 s poll. A present-but-unreadable
        # file carries a reason so the panel can say so, matching the SSE tail.
        body = warming_payload(campaign_id, cycle_id)
        if present:
            body["reason"] = "dashboard_unreadable"
    return JSONResponse(body, headers=headers)
