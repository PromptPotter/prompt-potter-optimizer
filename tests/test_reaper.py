"""Liveness reaper + the one terminal-stamp seam it defers to.

Silent-harm guarded: a reap that clobbers a paused or check-in cycle's
resumability with no error (the operator just finds their in-progress unit
silently stamped ``producer_vanished``), or a reap that writes a
half-shaped terminal record that disagrees with every other stop path.

Same class, and the reason ``reclaim_orphan_sandboxes`` is tested here: it is
the package's only unattended recursive DELETE. If its reachability predicate is
ever loosened, a live L4 campaign's inner measurement history disappears on a
background tick with nothing raised and nothing to restore it from.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from promptpotter.application.jobs.reaper import reclaim_orphan_sandboxes, sweep_dead_cycles
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.phases import RunPhase, StopReason
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    inner_sandbox_dir,
    inner_sandboxes_dir,
    sandbox_owner_path,
)
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import ConflictError

_CAMPAIGN = "testds__20260101-000000"
_CYCLE = "cycle-0"


def _mint(
    stores: Stores, *, checkin: bool = False, paused: bool = False, dashboard: bool = False
) -> Path:
    stores.campaigns.create(_CAMPAIGN, _CYCLE, {})
    cycle_dir = stores.campaigns.cycle_dir(_CAMPAIGN, _CYCLE)
    layout = CycleLayout(cycle_dir)
    layout.runtime.mkdir(parents=True, exist_ok=True)
    if checkin:
        layout.checkin_flag.touch()
    if paused:
        layout.pause_flag.touch()
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


def test_sweep_dead_cycles_reaps_an_inner_sandbox_cycle(built_stores: Stores) -> None:
    """L4 inner cycles live at ``<workspace>/.inner/<key>/{tenant}/…`` — a sibling of
    ``projects_root``, not under it — so the sweep must walk that tree too, not just
    ``projects_root`` itself (slice 5). The sweep is key-agnostic: it walks whatever
    directories `.inner/` holds."""
    inner_tenant_dir = inner_sandboxes_dir(built_stores.projects_root) / "outer-cycle-1" / "tenant"
    inner_store = CampaignStore(WorkspaceDir(inner_tenant_dir))
    inner_store.create(_CAMPAIGN, _CYCLE, {})
    cycle_dir = inner_store.cycle_dir(_CAMPAIGN, _CYCLE)
    write_json(cycle_dir / "dashboard.json", {"run_phase": "running"})
    _age(cycle_dir, seconds_ago=1000.0)

    reaped = sweep_dead_cycles(built_stores.projects_root, dead_after_s=1.0)
    assert reaped == 1
    data = inner_store.load(_CAMPAIGN, _CYCLE)
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
    write_json(cycle_dir / "dashboard.json", {"run_phase": "gate"})
    _age(cycle_dir, seconds_ago=1000.0)

    assert sweep_dead_cycles(built_stores.projects_root, dead_after_s=1.0) == 0
    assert built_stores.campaigns.mark_producer_vanished(_CAMPAIGN, _CYCLE) is False
    data = built_stores.campaigns.load(_CAMPAIGN, _CYCLE)
    assert data is not None and "finished_at" not in data


def test_a_live_gated_cycle_derives_gate_not_running(built_stores: Stores) -> None:
    """`gate` is DECLARED (no `.runtime/` flag), so the derivation could never
    return it and every non-live reader — the cycle list, and the dock built on it —
    saw an ordinary `running` cycle. The one phase that requires the operator to act
    was the one phase the operator's dock could not show."""
    cycle_dir = _mint(built_stores, dashboard=True)
    write_json(cycle_dir / "dashboard.json", {"run_phase": "gate"})
    assert derive_run_phase(cycle_dir, is_terminal=False) is RunPhase.GATE
    # A gated cycle that actually died must still be reapable, not pinned at `gate`.
    _age(cycle_dir, seconds_ago=1000.0)
    assert derive_run_phase(cycle_dir, is_terminal=False, fresh_s=1.0) is RunPhase.DETACHED


def _sandbox(stores: Stores, owner_campaign_id: str, owner_cycle_id: str) -> Path:
    """The inner-sandbox scratch tree owned by one cycle, holding one inner cycle.

    Built through the real key + owner record, so a change to either shows up here rather
    than leaving the reaper tested against a shape nothing writes.
    """
    sandbox = inner_sandbox_dir(
        stores.shared_root, str(stores.tenant_id), owner_campaign_id, owner_cycle_id
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
    inner.create("innerds__20260101-000000", "inner-cycle-0", {})
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
    assert built_stores.campaigns.mark_producer_vanished(_CAMPAIGN, _CYCLE) is True

    assert reclaim_orphan_sandboxes(built_stores.projects_root) == 0
    assert (sandbox / "tenant").is_dir()


def test_reclaim_spares_a_sandbox_it_cannot_prove_is_an_orphan(built_stores: Stores) -> None:
    """No owner record ⇒ KEEP. The key is a hash, so a sandbox missing its ``owner.json``
    cannot have an owner derived from its name — and this function is the package's only
    unattended recursive delete. It must act on a fact, never on the absence of one."""
    sandbox = inner_sandbox_dir(built_stores.shared_root, "t", "c__aaaaaa", "cycle-x")
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

    built_stores.campaigns.create("ppself__aaaaaa", shared_origin_cycle, {})
    # Finished, so the delete guard is about the sandbox key and not about liveness.
    built_stores.campaigns.update(
        "ppself__aaaaaa", shared_origin_cycle, {"finished_at": "2026-01-01T00:00:00Z"}
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
        stores.campaigns.update(_CAMPAIGN, _CYCLE, {"finished_at": "2026-01-01T00:00:00Z"})
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
    built_stores.campaigns.create(_CAMPAIGN, stub, {"parent_cycle_id": _CYCLE, "n_rounds": 0})
    stub_dir = built_stores.campaigns.cycle_dir(_CAMPAIGN, stub)
    write_json(stub_dir / "dashboard.json", {"run_phase": "running"})
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
