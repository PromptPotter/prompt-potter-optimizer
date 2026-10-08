"""The ONE served dashboard body — what the poll route returns and the SSE snapshot leads with:
``dashboard.json`` plus everything a stored copy would hold stale, laid on here on the way out."""

from __future__ import annotations

import json
from typing import Any

from promptpotter.application.jobs.quota import next_launch_limits
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.cycle_paths import Cut, CycleDir, CycleHop
from promptpotter.domain.phases import RunPhase
from promptpotter.infrastructure.projections.live_dashboard.projection import fold_at
from promptpotter.infrastructure.projections.live_dashboard.state import (
    LiveDashboardState,
    overlay_criterion_dials,
    overlay_round_readings,
    overlay_spend_metered,
    overlay_verify,
    warming_payload,
)
from promptpotter.infrastructure.runtime_flags import derive_run_phase, overlay_armed_controls
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.stores import Stores

__all__ = ["served_dashboard"]


def served_dashboard(stores: Stores, hop: CycleHop, *, at: int | None = None) -> dict[str, Any]:
    """The dashboard of the cycle at *hop*. ``at`` replays the same state to that ledger offset: it
    keeps the live ``run_phase`` and takes NEITHER the armed controls nor the next-launch ceilings."""
    cycle_path = cycle_dir_for(stores.base_dir, hop)
    stored: Any = None
    unreadable = False
    try:
        stored = read_json_optional(CycleLayout(cycle_path).dashboard)
    except json.JSONDecodeError:
        unreadable = True
    run_phase = str(derive_run_phase(cycle_path))
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    meter = None if campaign is None else ceiling_meter(campaign.arm)

    if at is not None:
        replay = fold_at(
            Cut(cycle=CycleDir(cycle_path), hop=hop, offset=at),
            wiring=LiveDashboardState.wiring_of(stored),
        ).model_dump(mode="json")
        replay["run_phase"] = run_phase
        overlay_criterion_dials(replay)
        overlay_round_readings(replay)
        if meter is not None:
            overlay_spend_metered(replay, meter)
        return replay

    if not isinstance(stored, dict):
        warming = warming_payload(hop, run_phase=run_phase)
        if unreadable:
            warming["reason"] = "dashboard_unreadable"
        return warming

    body: dict[str, Any] = stored
    body["run_phase"] = run_phase
    limits = body.get("run_limits")
    if (
        campaign is not None
        and campaign.config
        and run_phase != RunPhase.RUNNING
        and isinstance(limits, dict)
    ):
        limits.update(
            next_launch_limits(
                resolve_campaign_config(stores, campaign, hop), stores=stores, hop=hop
            )
        )
    overlay_armed_controls(body, cycle_path)
    overlay_criterion_dials(body)
    overlay_round_readings(body)
    overlay_verify(body, cycle_path)
    if meter is not None:
        overlay_spend_metered(body, meter)
    return body
