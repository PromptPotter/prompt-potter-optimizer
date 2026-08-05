"""Liveness reaper — the single owner reconciling on-disk cycle state with
whether a producer is actually alive, and disposable L4 scratch with whether
anything can still reach it.

The whole thing rests on one fact: a genuinely alive cycle cannot go stale, because it
heartbeats within ``RUN_FRESH_S``. So a persistently-detached cycle is *dead*, not quiet,
and stamping it TERMINAL is what stops the dock showing a ghost forever. Both callers
write through the one idempotent seam ``CampaignStore.mark_producer_vanished``, and the
sweep's staleness + suspend-skip guards are why a live run whose MACHINE slept is not
falsely reaped. Sandbox reclamation lives here too because it answers the same shape of
question — does the thing this on-disk state belongs to still exist?
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.phases import RunPhase
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    rmtree_robust,
    validate_path_component,
)
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    inner_sandboxes_dir,
    sandbox_owner_path,
)
from promptpotter.shared.clock import SUSPEND_GRACE_S, sleep_measuring_suspend

logger = logging.getLogger(__name__)

# A cycle whose dashboard/index has not moved for this long, with no live task,
# is treated as dead. Far above RUN_FRESH_S (30 s) and any plausible inner
# measurement — wide enough that a slow sample never approaches it. The real
# protection against reaping a cycle whose MACHINE (not just the process) was
# asleep is not this bound — it's :func:`periodic_sweep`'s initial delay +
# suspend-skip guard, which keep any judgment at least ``initial_delay_s``
# behind the producer's next heartbeat after a wake.
DEAD_AFTER_S = 900.0


def _tenant_root_of(cycle_dir: Path) -> WorkspaceDir:
    """The workspace root for a ``…/{tenant}/campaigns/{cid}/cycles/{cyid}`` dir."""
    return WorkspaceDir(cycle_dir.parents[3])


def _store_for(cycle_dir: Path) -> CampaignStore:
    return CampaignStore(_tenant_root_of(cycle_dir))


def reap_cycle_by_id(projects_root: Path, hop: CycleHop) -> bool:
    """Stamp a single dead cycle terminal, resolving its dir across tenants.

    Used by the JobRegistry ``on_reap`` callback. Idempotent + safe on a missing
    cycle (returns ``False``). Does not itself judge liveness — the registry has
    already proven the task gone before calling."""
    try:
        validate_path_component(hop.campaign_id)
        validate_path_component(hop.cycle_id)
    except ValueError:
        return False
    matches = list(
        projects_root.glob(f"*/campaigns/{hop.campaign_id}/cycles/{hop.cycle_id}/index.json")
    )
    if not matches:
        return False
    cycle_dir = matches[0].parent
    stamped = _store_for(cycle_dir).mark_producer_vanished(hop)
    if stamped:
        logger.info("reaped cycle %s/%s (producer_vanished)", hop.campaign_id, hop.cycle_id)
    return stamped


def _is_dead(cycle_dir: Path, *, dead_after_s: float) -> bool:
    """A cycle with no live producer — delegates entirely to the single
    run-phase derivation (``derive_run_phase``, ``runtime_flags.py``); this
    function is now just the index-readable guard plus the one comparison
    the sweep needs. No second "is it running?" computation (checkin / paused
    / staleness) lives here — ``derive_run_phase`` owns all of it, and
    ``mark_producer_vanished`` is still the seam that re-checks pause/checkin
    before writing (slice 3), so a race between this read and a state change
    is never a silent double-reap."""
    data = read_json_optional(CycleLayout(cycle_dir).manifest)
    if not isinstance(data, dict):
        return False  # unreadable — nothing to judge
    finished_at = data.get("finished_at")
    phase = derive_run_phase(cycle_dir, is_terminal=bool(finished_at), fresh_s=dead_after_s)
    return phase == RunPhase.DETACHED


def _sweep_roots(projects_root: Path) -> list[Path]:
    """The top-level ``projects_root`` plus every L4 inner sandbox (a sibling of
    ``projects_root`` — see ``runner/inner/cycle.py``). Each inner sandbox is itself a
    projects-root-shaped tree (``{tenant}/campaigns/{cid}/cycles/{cyid}/``), so
    the SAME glob pattern reaches it one level in — no recursion needed, the
    registry never nests an inner sandbox inside another."""
    roots = [projects_root]
    inner_dir = inner_sandboxes_dir(projects_root)
    if inner_dir.is_dir():
        roots.extend(p for p in inner_dir.iterdir() if p.is_dir())
    return roots


def reclaim_orphan_sandboxes(projects_root: Path) -> int:
    """Delete every inner sandbox whose owner cycle is gone; return the count.

    A sandbox is **scratch owned by exactly one outer cycle** — named by it, written only
    while it runs, reachable afterwards only by walking down from that cycle (the lineage
    tree, the ``descend`` routes). So when the owner is no longer on disk, the sandbox is
    not merely stale, it is *unreachable*: no lineage walk finds it, no route serves it.
    Sandboxes outlive their owners whenever a campaign is deleted through a path that
    doesn't pass ``inner_sandbox_root``, or an outer cycle is rewound and re-minted under
    a new id.

    Deliberately narrow: a sandbox whose owner still exists is KEPT, terminal or not.
    "The owner finished" is not the same claim as "nobody can reach this" — an operator
    drilling into a completed L4 campaign walks into exactly these trees — and a reaper
    that guesses at the difference deletes measurement history to save disk. Absence of
    the owner is a fact, not a policy, which is why it is the one this function acts on.
    """
    inner_dir = inner_sandboxes_dir(projects_root)
    if not inner_dir.is_dir():
        return 0
    reclaimed = 0
    for sandbox in inner_dir.iterdir():
        if not sandbox.is_dir():
            continue
        # The owner names itself in the sandbox's own `owner.json` — never glob by directory
        # name. That name is the content-addressed cycle_id, so any campaign in any tenant
        # that ran the same origin would answer "owner exists".
        owner = read_json_optional(sandbox_owner_path(sandbox))
        if not isinstance(owner, dict):
            # No owner record and no way to derive one from a hashed name. Not provably an
            # orphan, so it is KEPT — this function deletes on a fact, never on a guess.
            continue
        cycle_index = (
            projects_root
            / str(owner.get("tenant_id", ""))
            / "campaigns"
            / str(owner.get("campaign_id", ""))
            / "cycles"
            / str(owner.get("cycle_id", ""))
            / "index.json"
        )
        if cycle_index.is_file():
            continue
        try:
            rmtree_robust(sandbox)
        except OSError as exc:
            # Reported, never swallowed: an unreclaimable sandbox is the exact silence
            # this function exists to end.
            logger.warning("could not reclaim orphan inner sandbox %s: %s", sandbox, exc)
            continue
        logger.info("reclaimed orphan inner sandbox %s (owner cycle gone)", sandbox.name)
        reclaimed += 1
    return reclaimed


def sweep_dead_cycles(projects_root: Path, *, dead_after_s: float = DEAD_AFTER_S) -> int:
    """Reap every dead cycle across all tenants AND every L4 inner sandbox;
    return the count stamped.

    No ``live_keys`` exclusion — a genuinely live cycle (CLI or API-launched)
    is protected by the freshness gate in :func:`_is_dead` alone; the
    JobRegistry's own view of "running" is never authoritative here (at
    server boot it is provably empty, since ``JobRegistry.__init__`` stamps
    every pending/running job file ``stopped`` before this ever runs)."""
    reaped = 0
    for root in _sweep_roots(projects_root):
        for index_path in root.glob("*/campaigns/*/cycles/*/index.json"):
            cycle_dir = index_path.parent
            campaign_id = cycle_dir.parents[1].name
            cycle_id = cycle_dir.name
            if not _is_dead(cycle_dir, dead_after_s=dead_after_s):
                continue
            if _store_for(cycle_dir).mark_producer_vanished(
                CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
            ):
                reaped += 1
    if reaped:
        logger.info("sweep stamped %d dead cycle(s) terminal", reaped)
    return reaped


async def periodic_sweep(
    projects_root: Path,
    *,
    interval_s: float = DEAD_AFTER_S,
    initial_delay_s: float = 120.0,
) -> None:
    """Background sweep loop — runs for the server's lifetime (the lifespan
    cancels it at shutdown), so a CLI-launched death is reaped mid-uptime, not
    only at the next restart.

    Two protections against a false reap across a MACHINE sleep (the process
    freezes too, so nothing runs to distinguish "still asleep" from "just
    died" except elapsed wall time):

    - **Boot case**: the first sweep waits ``initial_delay_s`` (120 s — well
      above the 15 s heartbeat interval) before judging anything, so a
      producer that woke alongside the server gets its first post-wake
      heartbeat in before any staleness verdict.
    - **Mid-uptime case**: each tick times its own sleep in wall clock via
      :func:`~promptpotter.shared.clock.sleep_measuring_suspend`, which owns
      the detection rule (and states why the wall, not the monotonic clock, is
      what can be trusted here). When the overshoot exceeds ``SUSPEND_GRACE_S``,
      this SKIPS that tick's sweep and waits ``initial_delay_s`` again (the same
      heartbeat grace period as the boot case) before resuming judgment — so a
      just-woken producer is never reaped for the silence caused by the sleep
      itself. The inner-cycle wall-clock deadline rides the same helper through
      the heartbeat's ``on_suspend``, so a slept machine cannot fabricate a
      deadline blowout either.

    ``sweep_dead_cycles`` is blocking disk I/O (~0.2-0.3 s measured at 258
    cycles); run off the event loop via ``asyncio.to_thread`` so a slow sweep
    never stalls request handling.
    """
    sleep_for = initial_delay_s
    while True:
        overshoot = await sleep_measuring_suspend(sleep_for)
        if overshoot > SUSPEND_GRACE_S:
            logger.info(
                "periodic sweep: a %.0fs sleep overshot by %.0fs — the machine was "
                "likely suspended; skipping this tick to let producers heartbeat",
                sleep_for,
                overshoot,
            )
            sleep_for = initial_delay_s
            continue
        await asyncio.to_thread(sweep_dead_cycles, projects_root, dead_after_s=interval_s)
        # Reclamation rides the same tick but stays a separate call: one judges LIVENESS
        # (is this cycle's producer gone?), the other judges REACHABILITY (does this
        # sandbox's owner still exist?). Same schedule, different question — folding them
        # would make either count unreadable.
        await asyncio.to_thread(reclaim_orphan_sandboxes, projects_root)
        sleep_for = interval_s


__all__ = [
    "DEAD_AFTER_S",
    "periodic_sweep",
    "reap_cycle_by_id",
    "reclaim_orphan_sandboxes",
    "sweep_dead_cycles",
]
