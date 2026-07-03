"""Liveness reaper + the one terminal-stamp seam it defers to.

Silent-harm guarded: a reap that clobbers a paused or check-in cycle's
resumability with no error (the operator just finds their in-progress unit
silently stamped ``producer_vanished``), or a reap that writes a
half-shaped terminal record that disagrees with every other stop path.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from promptpotter.application.jobs.reaper import sweep_dead_cycles
from promptpotter.domain.phases import StopReason
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import write_json

_CAMPAIGN = "testds__20260101-000000"
_CYCLE = "cycle-0"


def _mint(
    stores: Stores, *, checkin: bool = False, paused: bool = False, dashboard: bool = False
) -> Path:
    stores.campaigns.create(_CAMPAIGN, _CYCLE, {})
    cycle_dir = stores.campaigns.cycle_dir(_CAMPAIGN, _CYCLE)
    runtime = cycle_dir / ".runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    if checkin:
        (runtime / "checkin.flag").touch()
    if paused:
        (runtime / "pause.flag").touch()
    if dashboard:
        write_json(cycle_dir / "dashboard.json", {"run_phase": "running"})
    return cycle_dir


def _age(cycle_dir: Path, seconds_ago: float) -> None:
    """Backdate every on-disk mtime `_is_dead` reads, so a short `dead_after_s`
    in a test doesn't require an actual sleep."""
    stamp = time.time() - seconds_ago
    for name in ("index.json", "dashboard.json"):
        p = cycle_dir / name
        if p.exists():
            os.utime(p, (stamp, stamp))


def test_mark_producer_vanished_skips_checkin_cycle(built_stores: Stores) -> None:
    _mint(built_stores, checkin=True)
    assert built_stores.campaigns.mark_producer_vanished(_CAMPAIGN, _CYCLE) is False
    data = built_stores.campaigns.load(_CAMPAIGN, _CYCLE)
    assert data is not None and "finished_at" not in data


def test_mark_producer_vanished_skips_paused_cycle(built_stores: Stores) -> None:
    _mint(built_stores, paused=True)
    assert built_stores.campaigns.mark_producer_vanished(_CAMPAIGN, _CYCLE) is False
    data = built_stores.campaigns.load(_CAMPAIGN, _CYCLE)
    assert data is not None and "finished_at" not in data


def test_mark_producer_vanished_is_idempotent_once_finished(built_stores: Stores) -> None:
    _mint(built_stores)
    assert built_stores.campaigns.mark_producer_vanished(_CAMPAIGN, _CYCLE) is True
    # Second call: already carries finished_at — no-op.
    assert built_stores.campaigns.mark_producer_vanished(_CAMPAIGN, _CYCLE) is False


def test_mark_producer_vanished_stamps_via_mark_finished_shape(built_stores: Stores) -> None:
    _mint(built_stores)
    assert built_stores.campaigns.mark_producer_vanished(_CAMPAIGN, _CYCLE) is True
    data = built_stores.campaigns.load(_CAMPAIGN, _CYCLE)
    assert data is not None
    reason = StopReason.PRODUCER_VANISHED.value
    assert data["status"] == reason
    assert data["stop_reason"] == reason
    assert data["finished_at"]
    # mark_finished's shape: no verdict block, no partial-round markers.
    assert "final" not in data
    assert "interrupted_round" not in data
    assert "crash_traceback" not in data


def test_sweep_dead_cycles_reaps_a_stale_cycle(built_stores: Stores) -> None:
    cycle_dir = _mint(built_stores, dashboard=True)
    _age(cycle_dir, seconds_ago=1000.0)
    reaped = sweep_dead_cycles(built_stores.projects_root, dead_after_s=1.0)
    assert reaped == 1
    data = built_stores.campaigns.load(_CAMPAIGN, _CYCLE)
    assert data is not None and data["finished_at"]


def test_sweep_dead_cycles_spares_a_fresh_cycle(built_stores: Stores) -> None:
    _mint(built_stores, dashboard=True)
    reaped = sweep_dead_cycles(built_stores.projects_root, dead_after_s=900.0)
    assert reaped == 0
    data = built_stores.campaigns.load(_CAMPAIGN, _CYCLE)
    assert data is not None and "finished_at" not in data


def test_sweep_dead_cycles_no_longer_accepts_live_keys(built_stores: Stores) -> None:
    """``live_keys`` was dead weight (provably empty at every real call site,
    slice 4) — dropped from the signature entirely."""
    with pytest.raises(TypeError):
        sweep_dead_cycles(built_stores.projects_root, live_keys=frozenset())


def test_sweep_dead_cycles_reaps_an_inner_sandbox_cycle(built_stores: Stores) -> None:
    """L4 inner cycles live at ``<workspace>/.inner/<outer_cycle_id>/{tenant}/…`` —
    a sibling of ``projects_root``, not under it — so the sweep must walk that
    tree too, not just ``projects_root`` itself (slice 5)."""
    inner_tenant_dir = built_stores.projects_root.parent / ".inner" / "outer-cycle-1" / "tenant"
    inner_store = CampaignStore(inner_tenant_dir)
    inner_store.create(_CAMPAIGN, _CYCLE, {})
    cycle_dir = inner_store.cycle_dir(_CAMPAIGN, _CYCLE)
    write_json(cycle_dir / "dashboard.json", {"run_phase": "running"})
    _age(cycle_dir, seconds_ago=1000.0)

    reaped = sweep_dead_cycles(built_stores.projects_root, dead_after_s=1.0)
    assert reaped == 1
    data = inner_store.load(_CAMPAIGN, _CYCLE)
    assert data is not None and data["finished_at"]
