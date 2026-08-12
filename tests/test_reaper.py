"""Liveness reaper + the one terminal-stamp seam it defers to.

Silent-harm guarded: a reap that clobbers a paused or check-in cycle's
resumability with no error (the operator just finds their in-progress unit
silently stamped ``producer_vanished``), or a reap that writes a
half-shaped terminal record that disagrees with every other stop path.

Same class, and the reason ``reclaim_orphan_sandboxes`` is tested here: it is
the package's only unattended recursive DELETE. If its reachability predicate is
ever loosened, a live L4 campaign's inner measurement history disappears on a
background tick with nothing raised and nothing to restore it from.

Also same class, and why ``sleep_measuring_suspend`` and the heartbeat's
``on_suspend`` are tested here rather than beside their own modules: they are the
two halves of ONE rule — a machine suspend must never be charged to a producer as
silence, nor to an inner cell as work. Both failures are silent, and both end at
the same stamp.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from fastapi import Request

from promptpotter.application.jobs import reaper
from promptpotter.application.jobs.reaper import reclaim_orphan_sandboxes, sweep_dead_cycles
from promptpotter.application.optimization.dispatch.llm_call import heartbeat as heartbeat_mod
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.phases import RunPhase, StopReason
from promptpotter.infrastructure.runtime_flags import RUN_FRESH_S, derive_run_phase
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import read_json_tolerant, write_json
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    inner_sandbox_dir,
    inner_sandboxes_dir,
    sandbox_owner_path,
)
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.presentation.api.routers.campaigns._conditional import http_date
from promptpotter.presentation.api.routers.campaigns.cycles import serve_dashboard_response
from promptpotter.shared import clock
from promptpotter.shared.errors import ConflictError

_CAMPAIGN = "testds__20260101-000000"
_CYCLE = "cycle-0"


def _mint(
    stores: Stores, *, checkin: bool = False, paused: bool = False, dashboard: bool = False
) -> Path:
    stores.campaigns.create(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE), {})
    cycle_dir = stores.campaigns.cycle_dir(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    layout = CycleLayout(cycle_dir)
    layout.runtime.mkdir(parents=True, exist_ok=True)
    if checkin:
        layout.checkin_flag.touch()
    if paused:
        layout.pause_flag.touch()
    if dashboard:
        write_json(cycle_dir / "dashboard.json", {"declared_phase": "running"})
    return cycle_dir


def _age(cycle_dir: Path, seconds_ago: float) -> None:
    """Backdate this cycle's whole on-disk footprint, so a short `dead_after_s` in a test doesn't
    require an actual sleep. The two DIRECTORIES are aged alongside the files because
    `run_phase_validator_epoch` reads their mtimes too — leaving them at "now" would hand the
    conditional-GET test a stamp that moved for a reason it isn't measuring."""
    stamp = time.time() - seconds_ago
    layout = CycleLayout(cycle_dir)
    for p in (cycle_dir / "index.json", cycle_dir / "dashboard.json", layout.runtime, cycle_dir):
        if p.exists():
            os.utime(p, (stamp, stamp))


def test_mark_producer_vanished_skips_checkin_cycle(built_stores: Stores) -> None:
    _mint(built_stores, checkin=True)
    assert (
        built_stores.campaigns.mark_producer_vanished(
            CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE)
        )
        is False
    )
    data = built_stores.campaigns.load(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    assert data is not None and "finished_at" not in data


def test_mark_producer_vanished_skips_paused_cycle(built_stores: Stores) -> None:
    _mint(built_stores, paused=True)
    assert (
        built_stores.campaigns.mark_producer_vanished(
            CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE)
        )
        is False
    )
    data = built_stores.campaigns.load(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    assert data is not None and "finished_at" not in data


def test_a_pause_that_set_no_flag_is_still_never_reaped(built_stores: Stores) -> None:
    """The flag is only ONE of the two ways a cycle pauses. An operator ``pause`` writes
    it; a Ctrl+C inside the round loop and an ``asyncio.CancelledError`` (the L4 sample
    deadline cancelling its inner campaign) write nothing and only DECLARE the phase.
    ``_finalize_run`` then deliberately skips every terminal write, so a cycle paused that
    way carries no ``finished_at``, no flag, and a frozen dashboard — which is precisely
    the shape a dead producer has.

    That is how two deliberately-cancelled L4 inner campaigns were stamped
    ``producer_vanished`` 15 minutes later, sending the operator to hunt a process that
    had never crashed. The declaration is the whole difference and it must bind here, not
    just in the freshness read."""
    cycle_dir = _mint(built_stores, dashboard=True)
    write_json(cycle_dir / "dashboard.json", {"declared_phase": RunPhase.PAUSED.value})
    _age(cycle_dir, seconds_ago=1000.0)

    assert derive_run_phase(cycle_dir, is_terminal=False, fresh_s=1.0) is RunPhase.PAUSED
    assert sweep_dead_cycles(built_stores.projects_root, dead_after_s=1.0) == 0
    assert (
        built_stores.campaigns.mark_producer_vanished(
            CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE)
        )
        is False
    )
    data = built_stores.campaigns.load(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    assert data is not None and "finished_at" not in data


def test_mark_producer_vanished_is_idempotent_once_finished(built_stores: Stores) -> None:
    _mint(built_stores)
    assert (
        built_stores.campaigns.mark_producer_vanished(
            CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE)
        )
        is True
    )
    # Second call: already carries finished_at — no-op.
    assert (
        built_stores.campaigns.mark_producer_vanished(
            CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE)
        )
        is False
    )


def test_mark_producer_vanished_stamps_via_mark_finished_shape(built_stores: Stores) -> None:
    _mint(built_stores)
    assert (
        built_stores.campaigns.mark_producer_vanished(
            CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE)
        )
        is True
    )
    data = built_stores.campaigns.load(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    assert data is not None
    reason = StopReason.PRODUCER_VANISHED.value
    assert data["status"] == reason
    assert data["stop_reason"] == reason
    assert data["finished_at"]
    # mark_finished's shape: no verdict block, no partial-round markers.
    assert "final" not in data
    assert "interrupted_round" not in data
    assert "crash_traceback" not in data


def test_every_surface_reports_one_run_phase_for_a_dead_producer(built_stores: Stores) -> None:
    """A reap stamps ``index.json`` and nothing rewrites ``dashboard.json`` — its ``declared_phase``
    has one writer, inside the runner's own process. So the file goes on declaring ``running``,
    and the live surfaces that served it raw (the dashboard route, the SSE snapshot) went on saying
    Running while ``/cycles`` and ``/tree``, which have always re-derived, said terminal.

    Silent by construction: nothing raises, the operator reads the remote-control pill and hunts a
    process that is not there. Asserted as AGREEMENT rather than against a chosen phase word, so it
    keeps binding whichever value the derivation returns."""
    cycle_dir = _mint(built_stores, dashboard=True)
    _age(cycle_dir, seconds_ago=1000.0)
    assert sweep_dead_cycles(built_stores.projects_root, dead_after_s=1.0) == 1

    stored = read_json_tolerant(cycle_dir / "dashboard.json")
    assert isinstance(stored, dict)
    entry = next(e for e in built_stores.campaigns.enumerate_cycles() if e["cycle_id"] == _CYCLE)
    served = serve_dashboard_response(
        Request({"type": "http", "method": "GET", "headers": []}),
        built_stores.base_dir,
        _CAMPAIGN,
        _CYCLE,
    )
    body = json.loads(bytes(served.body))

    assert body["run_phase"] == entry["run_phase"]
    assert body["run_phase"] != stored["declared_phase"]


def test_dead_producer_is_not_304d_at_the_phase_it_died_declaring(built_stores: Stores) -> None:
    """The 304 half of the same harm, and the half nothing else can reach. A browser polling every
    2 s holds the ``Last-Modified`` it got while the run was alive; the producer then dies, writing
    NOTHING. Every file mtime therefore stands still while the phase turns ``detached`` on the
    CLOCK — so a validator built from mtimes alone answers 304 forever and the operator watches a
    dead cycle report Running for as long as the tab stays open.

    Silent twice over: the 304 is a correct-looking response, and the body it suppresses is the one
    carrying the correction. Asserted through the ROUTE rather than against
    `run_phase_validator_epoch` directly, so it binds the behaviour and not today's plumbing."""
    cycle_dir = _mint(built_stores, dashboard=True)
    _age(cycle_dir, seconds_ago=RUN_FRESH_S * 10)
    # Read AFTER aging: this is the stamp the last live poll actually banked, and nothing has
    # written since. Taking it before would bank a future the producer never reached.
    alive_at = (cycle_dir / "dashboard.json").stat().st_mtime

    served = serve_dashboard_response(
        Request(
            {
                "type": "http",
                "method": "GET",
                # What the client banked on its last poll, taken while the producer was writing.
                "headers": [(b"if-modified-since", http_date(alive_at).encode())],
            }
        ),
        built_stores.base_dir,
        _CAMPAIGN,
        _CYCLE,
    )

    assert served.status_code == 200, "a dead producer stayed cached at the phase it declared"
    assert json.loads(bytes(served.body))["run_phase"] == RunPhase.DETACHED.value


def test_sweep_dead_cycles_reaps_a_stale_cycle(built_stores: Stores) -> None:
    cycle_dir = _mint(built_stores, dashboard=True)
    _age(cycle_dir, seconds_ago=1000.0)
    reaped = sweep_dead_cycles(built_stores.projects_root, dead_after_s=1.0)
    assert reaped == 1
    data = built_stores.campaigns.load(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    assert data is not None and data["finished_at"]


def test_sweep_dead_cycles_spares_a_fresh_cycle(built_stores: Stores) -> None:
    _mint(built_stores, dashboard=True)
    reaped = sweep_dead_cycles(built_stores.projects_root, dead_after_s=900.0)
    assert reaped == 0
    data = built_stores.campaigns.load(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    assert data is not None and "finished_at" not in data


def test_sweep_dead_cycles_reaps_an_inner_sandbox_cycle(built_stores: Stores) -> None:
    """L4 inner cycles live at ``<workspace>/.inner/<key>/{tenant}/…`` — a sibling of
    ``projects_root``, not under it — so the sweep must walk that tree too, not just
    ``projects_root`` itself (slice 5). The sweep is key-agnostic: it walks whatever
    directories `.inner/` holds."""
    inner_tenant_dir = inner_sandboxes_dir(built_stores.projects_root) / "outer-cycle-1" / "tenant"
    inner_store = CampaignStore(WorkspaceDir(inner_tenant_dir))
    inner_store.create(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE), {})
    cycle_dir = inner_store.cycle_dir(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    write_json(cycle_dir / "dashboard.json", {"declared_phase": "running"})
    _age(cycle_dir, seconds_ago=1000.0)

    reaped = sweep_dead_cycles(built_stores.projects_root, dead_after_s=1.0)
    assert reaped == 1
    data = inner_store.load(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    assert data is not None and data["finished_at"]


def test_a_cycle_held_at_the_origin_gate_is_never_reaped(built_stores: Stores) -> None:
    """The gate's wait is unbounded — it ends only when a human decides — so the
    cycle is alive and idle by design. It used to go stale in 30s and be stamped
    TERMINAL 15 minutes later while still polling; a decision arriving after that
    resumed a run into a cycle already marked finished. Nothing raised.

    The wait now heartbeats, so in practice it never goes stale. This pins the
    SECOND line: even from a stale tree (a machine sleep beat the heartbeat), a
    declared gate is not a dead producer."""
    cycle_dir = _mint(built_stores, dashboard=True)
    write_json(cycle_dir / "dashboard.json", {"declared_phase": "gate"})
    _age(cycle_dir, seconds_ago=1000.0)

    assert sweep_dead_cycles(built_stores.projects_root, dead_after_s=1.0) == 0
    assert (
        built_stores.campaigns.mark_producer_vanished(
            CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE)
        )
        is False
    )
    data = built_stores.campaigns.load(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    assert data is not None and "finished_at" not in data


def test_a_live_gated_cycle_derives_gate_not_running(built_stores: Stores) -> None:
    """`gate` is DECLARED (no `.runtime/` flag), so the derivation could never
    return it and every non-live reader — the cycle list, and the dock built on it —
    saw an ordinary `running` cycle. The one phase that requires the operator to act
    was the one phase the operator's dock could not show."""
    cycle_dir = _mint(built_stores, dashboard=True)
    write_json(cycle_dir / "dashboard.json", {"declared_phase": "gate"})
    assert derive_run_phase(cycle_dir, is_terminal=False) is RunPhase.GATE
    # A gated cycle that actually died must still be reapable, not pinned at `gate`.
    _age(cycle_dir, seconds_ago=1000.0)
    assert derive_run_phase(cycle_dir, is_terminal=False, fresh_s=1.0) is RunPhase.DETACHED


class _TicksExhaustedError(Exception):
    """Breaks an intentionally-infinite tick loop once the scripted ticks run out."""


class _ImmediateSleep:
    """``asyncio`` stand-in whose ``sleep`` returns at once — the fake clock supplies
    the elapsed time, so no test ever waits."""

    @staticmethod
    async def sleep(_seconds: float) -> None:
        return None


class _FakeWallClock:
    """A ``time``-module stand-in whose ``time()`` jumps once, staging a suspend without
    one. Only ``sleep_measuring_suspend`` reads it."""

    def __init__(self, *, elapse_s: float) -> None:
        self._now = 1_000_000.0
        self._elapse = elapse_s

    def time(self) -> float:
        now = self._now
        self._now += self._elapse
        self._elapse = 0.0
        return now


def _scripted_overshoots(values: list[float]) -> Callable[[float], Awaitable[float]]:
    """A ``sleep_measuring_suspend`` stand-in returning *values* in order, then stopping
    the loop that is driving it."""
    remaining = iter(values)

    async def _fake_sleep(_seconds: float) -> float:
        try:
            return next(remaining)
        except StopIteration:
            raise _TicksExhaustedError from None

    return _fake_sleep


async def test_sleep_measuring_suspend_reports_the_wall_overshoot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The arithmetic BOTH suspend guards rest on — the reaper's tick-skip and the L4
    inner deadline's extension. If it silently returned 0, neither guard would ever fire
    and neither would say so: the reaper would reap live producers whose machine slept,
    and an inner cell would be cancelled for a deadline it spent suspended, landing an
    unscoreable hole in the panel that reads as a real measurement failure."""
    monkeypatch.setattr(clock, "asyncio", _ImmediateSleep())
    # 10s of wall elapsed for a 4s sleep ⇒ 6s of it was the machine being away.
    monkeypatch.setattr(clock, "time", _FakeWallClock(elapse_s=10.0))
    assert await clock.sleep_measuring_suspend(4.0) == pytest.approx(6.0)
    # An ordinary sleep overshoots by nothing, and never reports a negative.
    monkeypatch.setattr(clock, "time", _FakeWallClock(elapse_s=4.0))
    assert await clock.sleep_measuring_suspend(4.0) == pytest.approx(0.0)


async def test_periodic_sweep_skips_its_tick_when_the_machine_slept(
    monkeypatch: pytest.MonkeyPatch, built_stores: Stores
) -> None:
    """A machine suspend freezes the producer's heartbeat along with everything else, so
    on wake every live cycle looks stale at once. Reaping on that tick stamps healthy,
    resumable cycles ``producer_vanished`` — the exact silent clobber this file guards,
    except arriving in a batch and blamed on the wrong cause."""
    swept: list[float] = []
    monkeypatch.setattr(
        reaper,
        "sleep_measuring_suspend",
        _scripted_overshoots([reaper.SUSPEND_GRACE_S + 1.0, 0.0]),
    )
    monkeypatch.setattr(
        reaper, "sweep_dead_cycles", lambda root, dead_after_s: swept.append(dead_after_s)
    )
    monkeypatch.setattr(reaper, "reclaim_orphan_sandboxes", lambda root: None)

    with pytest.raises(_TicksExhaustedError):
        await reaper.periodic_sweep(built_stores.projects_root, interval_s=900.0)

    # Tick 1 overshot the grace and swept nothing; tick 2 was ordinary and swept.
    assert swept == [900.0]


async def test_heartbeat_reports_a_suspend_to_its_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """``on_suspend`` is the wire from the one ticking loop to the L4 wall-clock deadline.
    A heartbeat that ticked but never called it would leave the deadline charging suspend
    time as work, with the heartbeat still appending happily — the failure looks like a
    healthy run right up to the fabricated cancellation."""
    reported: list[float] = []
    monkeypatch.setattr(
        heartbeat_mod,
        "sleep_measuring_suspend",
        _scripted_overshoots([1.0, heartbeat_mod.SUSPEND_GRACE_S + 5.0]),
    )
    with pytest.raises(_TicksExhaustedError):
        await heartbeat_mod.heartbeat(
            None,  # no ledger: the tick must still run, so the guard cannot be disarmed
            call_id="inner:seed-3",
            node="inner_campaign",
            round_num=1,
            start_monotonic=0.0,
            on_suspend=reported.append,
        )
    # Only the tick that exceeded the grace is a suspend; ordinary lateness is not.
    assert reported == [heartbeat_mod.SUSPEND_GRACE_S + 5.0]


def _sandbox(stores: Stores, owner_campaign_id: str, owner_cycle_id: str) -> Path:
    """The inner-sandbox scratch tree owned by one cycle, holding one inner cycle.

    Built through the real key + owner record, so a change to either shows up here rather
    than leaving the reaper tested against a shape nothing writes.
    """
    sandbox = inner_sandbox_dir(
        stores.shared_root,
        str(stores.tenant_id),
        CycleHop(campaign_id=owner_campaign_id, cycle_id=owner_cycle_id),
    )
    write_json(
        sandbox_owner_path(sandbox),
        {
            "tenant_id": str(stores.tenant_id),
            "campaign_id": owner_campaign_id,
            "cycle_id": owner_cycle_id,
        },
    )
    inner = CampaignStore(WorkspaceDir(sandbox / "tenant"))
    inner.create(CycleHop(campaign_id="innerds__20260101-000000", cycle_id="inner-cycle-0"), {})
    return sandbox


def test_reclaim_deletes_a_sandbox_whose_owner_cycle_is_gone(built_stores: Stores) -> None:
    """The accumulation mechanism: nothing else in the package can reach or delete
    a sandbox once its owner cycle is off disk."""
    sandbox = _sandbox(built_stores, _CAMPAIGN, "orphaned-outer-cycle")
    assert sandbox.is_dir()

    assert reclaim_orphan_sandboxes(built_stores.projects_root) == 1
    assert not sandbox.exists()


def test_reclaim_spares_a_sandbox_whose_owner_cycle_still_exists(built_stores: Stores) -> None:
    """The silent harm. An operator drilling into a COMPLETED L4 campaign walks into
    exactly this tree, so "the owner finished" must never be read as "unreachable" —
    reclamation keys on the owner's absence, and nothing else."""
    _mint(built_stores)  # owner cycle _CYCLE, on disk
    sandbox = _sandbox(built_stores, _CAMPAIGN, _CYCLE)
    # Terminal owner: the tempting-but-wrong reclamation trigger.
    assert (
        built_stores.campaigns.mark_producer_vanished(
            CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE)
        )
        is True
    )

    assert reclaim_orphan_sandboxes(built_stores.projects_root) == 0
    assert (sandbox / "tenant").is_dir()


def test_reclaim_spares_a_sandbox_it_cannot_prove_is_an_orphan(built_stores: Stores) -> None:
    """No owner record ⇒ KEEP. The key is a hash, so a sandbox missing its ``owner.json``
    cannot have an owner derived from its name — and this function is the package's only
    unattended recursive delete. It must act on a fact, never on the absence of one."""
    sandbox = inner_sandbox_dir(
        built_stores.shared_root, "t", CycleHop(campaign_id="c__aaaaaa", cycle_id="cycle-x")
    )
    (sandbox / "tenant").mkdir(parents=True)

    assert reclaim_orphan_sandboxes(built_stores.projects_root) == 0
    assert sandbox.is_dir()


def test_two_campaigns_on_one_origin_do_not_share_a_sandbox(built_stores: Stores) -> None:
    """``cycle_id`` is content-addressed on the origin, so two campaigns minted from the
    same origin carry the SAME one. Keyed on it alone, they shared one sandbox — and a
    ``delete`` of either cascaded into the other's inner measurement history, as did the
    sweep a fresh ``new`` used to run. Observed: 39 banked inner campaigns destroyed.

    The two facts that make it safe are the same fact: distinct keys, and a cascade that
    resolves the key from the campaign it is deleting.
    """
    shared_origin_cycle = "cycle_sameorigin"
    a = _sandbox(built_stores, "ppself__aaaaaa", shared_origin_cycle)
    b = _sandbox(built_stores, "ppself__bbbbbb", shared_origin_cycle)
    assert a != b

    built_stores.campaigns.create(
        CycleHop(campaign_id="ppself__aaaaaa", cycle_id=shared_origin_cycle), {}
    )
    # Finished, so the delete guard is about the sandbox key and not about liveness.
    built_stores.campaigns.update(
        CycleHop(campaign_id="ppself__aaaaaa", cycle_id=shared_origin_cycle),
        {"finished_at": "2026-01-01T00:00:00Z"},
    )
    write_json(
        built_stores.campaigns.campaign_root_dir("ppself__aaaaaa") / "campaign.json",
        {
            "campaign_id": "ppself__aaaaaa",
            "dataset_name": "ppself",
            "root_cycle_id": shared_origin_cycle,
            "created_at": "2026-01-01T00:00:00Z",
            "lifecycle_status": "active",
        },
    )
    assert (
        built_stores.campaigns.delete_campaign(
            "ppself__aaaaaa",
            keep_results=False,
            changed_at="2026-01-01T00:00:00Z",
            inner_sandbox_root=inner_sandboxes_dir(built_stores.shared_root),
        )
        is True
    )
    assert not a.exists()
    assert (b / "tenant").is_dir()


def _lifecycle_fixture(stores: Stores, *, running: bool) -> tuple[Path, Path]:
    """A campaign that IS the operator's active lens, with one cycle live or finished.

    Returns ``(tenant_root, campaign_dir)``. `running` keys the ONE fact the guard
    reads: a fresh `dashboard.json` with no `finished_at` derives ``RUNNING``; the same
    tree with `finished_at` derives ``TERMINAL``.
    """
    cycle_dir = _mint(stores, dashboard=True)
    tenant_root = cycle_dir.parents[3]
    campaign_dir = cycle_dir.parents[1]
    if not running:
        stores.campaigns.update(
            CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE),
            {"finished_at": "2026-01-01T00:00:00Z"},
        )
    write_json(
        campaign_dir / "campaign.json",
        {
            "campaign_id": _CAMPAIGN,
            "dataset_name": "testds",
            "root_cycle_id": _CYCLE,
            "created_at": "2026-01-01T00:00:00Z",
            "lifecycle_status": "active",
        },
    )
    write_json(
        tenant_root / ".workspace" / "active_session.json",
        {"session_id": "s1", "campaign_id": _CAMPAIGN, "cycle_id": _CYCLE},
    )
    return tenant_root, campaign_dir


def test_delete_refuses_a_campaign_with_a_live_producer(built_stores: Stores) -> None:
    """The real hazard: removing the tree under a process that is still writing it
    loses a live run's measurements, and the operator asked to delete a *campaign*,
    not to kill a run."""
    _, campaign_dir = _lifecycle_fixture(built_stores, running=True)
    with pytest.raises(ConflictError):
        built_stores.campaigns.delete_campaign(
            _CAMPAIGN, keep_results=False, changed_at="2026-01-01T00:00:00Z"
        )
    assert campaign_dir.is_dir()


def test_delete_removes_the_campaign_the_operator_is_looking_at(built_stores: Stores) -> None:
    """The dead button this replaced. The guard used to refuse whenever the campaign
    was merely ACTIVE, so in a single-operator workspace — where the campaign in view
    IS the active one — Delete and Archive always answered "switch first", naming an
    escape that exists in neither the command vocabulary nor the webapp. Deleting what
    you are looking at is the ordinary case; the pointer is released, not defended."""
    tenant_root, campaign_dir = _lifecycle_fixture(built_stores, running=False)
    assert (
        built_stores.campaigns.delete_campaign(
            _CAMPAIGN, keep_results=False, changed_at="2026-01-01T00:00:00Z"
        )
        is True
    )
    assert not campaign_dir.exists()
    # The pointer must not be left naming a campaign that is gone.
    assert read_active_pointer(tenant_root) == ("", "", "")


async def test_delete_cycle_guards_liveness_and_not_the_pointer(built_stores: Stores) -> None:
    """The inverted pair. ``delete-cycle`` refused the ACTIVE cycle and checked nothing
    about a live producer — while ``try_delete_stub_cycle`` only ever removes a cycle with
    ``n_rounds == 0``, which IS the just-minted window before round 1 commits. So the one
    deletion the verb can perform is the one most likely to be live, and that was the
    unchecked case; the case it did refuse was harmless."""
    from promptpotter.presentation.api.middleware.command_dispatcher import CommandDispatcher

    tenant_root, _ = _lifecycle_fixture(built_stores, running=True)
    stub = "cycle-0_fork_deadbeef"
    built_stores.campaigns.create(
        CycleHop(campaign_id=_CAMPAIGN, cycle_id=stub), {"parent_cycle_id": _CYCLE, "n_rounds": 0}
    )
    stub_dir = built_stores.campaigns.cycle_dir(CycleHop(campaign_id=_CAMPAIGN, cycle_id=stub))
    write_json(stub_dir / "dashboard.json", {"declared_phase": "running"})
    write_json(
        tenant_root / ".workspace" / "active_session.json",
        {"session_id": "s1", "campaign_id": _CAMPAIGN, "cycle_id": stub},
    )
    disp = CommandDispatcher(built_stores)

    # Live producer on the target → refused, and the tree is still there.
    with pytest.raises(ConflictError):
        await disp.dispatch_cycle_command(
            kind="delete-cycle",
            campaign_id=_CAMPAIGN,
            cycle_id=stub,
            payload_extras={},
            idempotency_key="k1",
            expected_version=None,
        )
    assert stub_dir.is_dir()

    # Producer gone: being the ACTIVE cycle is not a reason to refuse — the pointer
    # falls back to the parent instead.
    _age(stub_dir, 10_000)
    await disp.dispatch_cycle_command(
        kind="delete-cycle",
        campaign_id=_CAMPAIGN,
        cycle_id=stub,
        payload_extras={},
        idempotency_key="k2",
        expected_version=None,
    )
    assert not stub_dir.exists()
    assert read_active_pointer(tenant_root)[2] == _CYCLE


async def test_the_delete_verb_reaches_the_store_that_allows_it(built_stores: Stores) -> None:
    """Same fact one level up, and the reason the fix above did not reach the operator.

    The store was corrected; the DISPATCHER kept its own copy of the deleted rule and
    answered "switch first" before the store was ever asked. Both webapp buttons and both
    CLI verbs go through here, so the store-level test above passed while every operator
    surface stayed dead. A second opinion in front of a guard is not redundancy — it is
    the guard, and it is the one nothing tested."""
    from promptpotter.presentation.api.middleware.command_dispatcher import CommandDispatcher

    _, campaign_dir = _lifecycle_fixture(built_stores, running=False)
    await CommandDispatcher(built_stores).dispatch_lifecycle(
        kind="delete-campaign", campaign_id=_CAMPAIGN, reason="", idempotency_key="k1"
    )
    assert not campaign_dir.exists()


def test_reopening_a_finished_cycle_opens_a_reap_window_until_its_producer_is_fresh(
    built_stores: Stores,
) -> None:
    """Clearing the terminal latch makes a cycle reapable, and only a fresh producer closes it.

    The L4 continuation (``inner/spawn.py::_open_inner_campaign``) re-enters an abandoned
    inner campaign so its banked rounds are not orphaned, which means clearing
    ``finished_at``. That field is the ONLY thing protecting the cycle from the sweep while
    its last attempt's ``dashboard.json`` is still hours stale: ``TERMINAL`` is refused, but
    the moment the latch goes the same directory derives ``DETACHED`` — which is exactly what
    ``_is_dead`` reaps. The sweep runs in the API-server process on its own timer, so the
    window is real and nothing in the run would report losing it: the continued cycle simply
    acquires a ``producer_vanished`` stamp underneath a live producer, and the operator goes
    hunting a process that never died.

    Hence the ordering in ``_open_inner_campaign``: refresh the dashboard FIRST, clear the
    latch SECOND, so the cycle steps TERMINAL → RUNNING and is never momentarily reapable.
    This pins the hazard that ordering exists for.
    """
    cycle_dir = _mint(built_stores, dashboard=True)
    built_stores.campaigns.update(
        CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE), {"finished_at": "2026-08-02T20:30:00Z"}
    )
    _age(cycle_dir, seconds_ago=10_000.0)

    # Terminal: stale, but the latch protects it.
    assert derive_run_phase(cycle_dir, is_terminal=True, fresh_s=1.0) is RunPhase.TERMINAL
    assert sweep_dead_cycles(built_stores.projects_root, dead_after_s=1.0) == 0

    built_stores.campaigns.reopen_for_continuation(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    reopened = built_stores.campaigns.load(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    assert reopened is not None
    assert "finished_at" not in reopened, "the terminal latch survived the reopen"
    assert "final" not in reopened, "a stale winner block outlived the round that justified it"
    assert reopened["status"] == "active"

    # The latch is gone and the producer is still stale — this is the window.
    _age(cycle_dir, seconds_ago=10_000.0)
    assert derive_run_phase(cycle_dir, is_terminal=False, fresh_s=1.0) is RunPhase.DETACHED

    # A fresh dashboard write — what `build_campaign_emitter` does — closes it.
    write_json(cycle_dir / "dashboard.json", {"declared_phase": "running"})
    assert derive_run_phase(cycle_dir, is_terminal=False, fresh_s=60.0) is RunPhase.RUNNING
    assert sweep_dead_cycles(built_stores.projects_root, dead_after_s=60.0) == 0
    still = built_stores.campaigns.load(CycleHop(campaign_id=_CAMPAIGN, cycle_id=_CYCLE))
    assert still is not None and "finished_at" not in still
