"""Per-cycle reads — cycle list, cycle detail, rounds, dashboard.

All routes carry ``(campaign_id, cycle_id)``. ``dashboard.json`` is
per-cycle — the dashboard route serves the viewed cycle's own file, so a
fork's chart shows the fork's trajectory, not the session root's.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from typing import Any, Literal

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from promptpotter.infrastructure.runtime_flags import (
    is_paused,
    is_running,
    is_stop_requested,
    read_spend_cap,
)
from promptpotter.infrastructure.store import cycle_dir_for
from promptpotter.infrastructure.store.paths import sibling_kind
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router

# dashboard.json older than this ⇒ the run is treated as not-running. The loop
# bumps the file on every sample / progress tick / round boundary, so a healthy
# run stays well inside the window even across long backend calls.
RUNSTATE_FRESH_S = 30.0


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


def _http_date(epoch_seconds: float) -> str:
    """Format an mtime as an HTTP-date (RFC 7231 §7.1.1.1). Second resolution."""
    return format_datetime(datetime.fromtimestamp(int(epoch_seconds), tz=UTC), usegmt=True)


def _client_seen_at_or_after(if_modified_since: str | None, mtime_epoch: float) -> bool:
    """Return True iff the client's `If-Modified-Since` covers the current mtime.
    Malformed header → False (serve full body)."""
    if not if_modified_since:
        return False
    try:
        client_dt = parsedate_to_datetime(if_modified_since)
    except (TypeError, ValueError):
        return False
    if client_dt is None:
        return False
    return int(client_dt.timestamp()) >= int(mtime_epoch)


@campaigns_router.get("/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard")
async def get_cycle_dashboard(
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

    if not path.is_file():
        # Fresh-campaign warming_up shape. Last-Modified rides the cycle
        # dir's mtime so polling clients still get cheap 304s while waiting.
        try:
            mtime_epoch = cycle_path.stat().st_mtime
            headers = {"Last-Modified": _http_date(mtime_epoch)}
            if _client_seen_at_or_after(request.headers.get("if-modified-since"), mtime_epoch):
                return Response(status_code=304, headers=headers)
        except FileNotFoundError:
            headers = {}
        warming: dict[str, Any] = {
            "warming_up": True,
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
            "phase_hint": "origin",
        }
        return JSONResponse(warming, headers=headers)

    mtime_epoch = path.stat().st_mtime
    headers = {"Last-Modified": _http_date(mtime_epoch)}
    if _client_seen_at_or_after(request.headers.get("if-modified-since"), mtime_epoch):
        return Response(status_code=304, headers=headers)

    dashboard: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return JSONResponse(dashboard, headers=headers)


class CycleRunState(BaseModel):
    """Non-cached run-control state for one cycle.

    Unlike the dashboard route (304-cached on file mtime — useless while a
    paused run leaves the file static), this is recomputed every call:
    ``paused`` / ``stop_requested`` are flag-driven (instant), ``running`` is
    derived from ``dashboard.json`` freshness. The run controls + state badges
    read this for the *viewed* cycle, so they reflect the run you're looking at
    rather than the single active-pointer cycle.
    """

    campaign_id: str
    cycle_id: str
    running: bool
    paused: bool
    stop_requested: bool
    spend_cap_usd: float | None = None


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/runstate", response_model=CycleRunState
)
async def get_cycle_runstate(store: StoreDep, campaign_id: str, cycle_id: str) -> CycleRunState:
    """Live run-control state for the viewed cycle — always fresh, never 304."""
    cycle_path = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
    runtime = cycle_path / ".runtime"
    # Both the per-cycle dashboard.json and the runtime flags live on this
    # cycle's own dir, where the runner writes + polls them.
    dashboard_path = cycle_path / "dashboard.json"
    return CycleRunState(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        running=is_running(dashboard_path, fresh_s=RUNSTATE_FRESH_S),
        paused=is_paused(runtime),
        stop_requested=is_stop_requested(runtime),
        spend_cap_usd=read_spend_cap(runtime),
    )
