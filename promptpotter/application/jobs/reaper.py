"""Liveness reaper — the single owner reconciling on-disk cycle state with
whether a producer is actually alive.

Two launch paths leave a cycle non-terminal on disk after their producer is
gone: an **API-launched** run whose asyncio task died mid-session (a torn task
in the ``--workers 1`` server), and a **CLI-launched** run (``python -m
promptpotter …``, never in the :class:`JobRegistry`) that crashed / was killed /
slept without stamping a terminal record. Post in-flight-heartbeat a genuinely
alive cycle can never go stale (it heartbeats its ledger → ``dashboard.json``
within ``RUN_FRESH_S``), so a persistently-detached cycle is *dead*, not quiet —
and must be reaped to ``TERMINAL`` (``PRODUCER_VANISHED``) so the OS-style dock
and the on-disk truth agree instead of showing a ghost open-app forever.

Two callers, one write seam (``CampaignStore.mark_producer_vanished``, idempotent):

- :func:`reap_cycle_by_id` — the :class:`JobRegistry` ``on_reap`` callback fires
  it the instant an API job's task is proven gone (immediate stamp).
- :func:`sweep_dead_cycles` — the per-tick body of :func:`periodic_sweep`, a
  background loop (started at server boot, cancelled at shutdown) that catches
  CLI-launched dead cycles the registry never saw. Gated on a long
  ``DEAD_AFTER_S`` staleness, plus :func:`periodic_sweep`'s own initial-delay +
  suspend-skip guard, so a live run — even one whose MACHINE just slept — is
  never falsely reaped.

Tenant-agnostic: ``campaign_id`` is globally unique, so cycle dirs are resolved
by globbing ``projects_root`` — no identity/tenant mapping needed. The sweep
walks ``projects_root`` PLUS every L4 inner sandbox (``<workspace>/.inner/<outer
cycle_id>``, a sibling tree of the same shape — see :func:`_sweep_roots`), so an
inner cycle's dead producer is reaped exactly like a top-level one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from promptpotter.domain.phases import RunPhase
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import read_json_optional, validate_path_component

logger = logging.getLogger(__name__)

# A cycle whose dashboard/index has not moved for this long, with no live task,
# is treated as dead. Far above RUN_FRESH_S (30 s) and any plausible inner
# measurement — wide enough that a slow sample never approaches it. The real
# protection against reaping a cycle whose MACHINE (not just the process) was
# asleep is not this bound — it's :func:`periodic_sweep`'s initial delay +
# suspend-skip guard, which keep any judgment at least ``initial_delay_s``
# behind the producer's next heartbeat after a wake.
DEAD_AFTER_S = 900.0


def _tenant_root_of(cycle_dir: Path) -> Path:
    """``projects_root/{tenant}`` for a ``…/{tenant}/campaigns/{cid}/cycles/{cyid}`` dir."""
    return cycle_dir.parents[3]


def _store_for(cycle_dir: Path) -> CampaignStore:
    return CampaignStore(_tenant_root_of(cycle_dir))


def reap_cycle_by_id(projects_root: Path, campaign_id: str, cycle_id: str) -> bool:
    """Stamp a single dead cycle terminal, resolving its dir across tenants.

    Used by the JobRegistry ``on_reap`` callback. Idempotent + safe on a missing
    cycle (returns ``False``). Does not itself judge liveness — the registry has
    already proven the task gone before calling."""
    try:
        validate_path_component(campaign_id)
        validate_path_component(cycle_id)
    except ValueError:
        return False
    matches = list(projects_root.glob(f"*/campaigns/{campaign_id}/cycles/{cycle_id}/index.json"))
    if not matches:
        return False
    cycle_dir = matches[0].parent
    stamped = _store_for(cycle_dir).mark_producer_vanished(campaign_id, cycle_id)
    if stamped:
        logger.info("reaped cycle %s/%s (producer_vanished)", campaign_id, cycle_id)
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
    data = read_json_optional(cycle_dir / "index.json")
    if not isinstance(data, dict):
        return False  # unreadable — nothing to judge
    finished_at = data.get("finished_at")
    phase = derive_run_phase(cycle_dir, is_terminal=bool(finished_at), fresh_s=dead_after_s)
    return phase == RunPhase.DETACHED


def _sweep_roots(projects_root: Path) -> list[Path]:
    """The top-level ``projects_root`` plus every L4 inner sandbox
    (``<workspace>/.inner/<outer_cycle_id>``, a sibling of ``projects_root`` —
    see ``runner/inner_recursion.py``). Each inner sandbox is itself a
    projects-root-shaped tree (``{tenant}/campaigns/{cid}/cycles/{cyid}/``), so
    the SAME glob pattern reaches it one level in — no recursion needed, the
    registry never nests an inner sandbox inside another."""
    roots = [projects_root]
    inner_dir = projects_root.parent / ".inner"
    if inner_dir.is_dir():
        roots.extend(p for p in inner_dir.iterdir() if p.is_dir())
    return roots


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
            if _store_for(cycle_dir).mark_producer_vanished(campaign_id, cycle_id):
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
    cancels it at shutdown), replacing the old startup-only one-shot so a
    CLI-launched death is reaped mid-uptime, not only at the next restart.

    Two protections against a false reap across a MACHINE sleep (the process
    freezes too, so nothing runs to distinguish "still asleep" from "just
    died" except elapsed wall time):

    - **Boot case**: the first sweep waits ``initial_delay_s`` (120 s — well
      above the 15 s heartbeat interval) before judging anything, so a
      producer that woke alongside the server gets its first post-wake
      heartbeat in before any staleness verdict.
    - **Mid-uptime case**: each tick times its own ``asyncio.sleep`` in wall
      clock. A real OS suspend freezes the event loop's monotonic clock along
      with everything else, so a scheduled sleep only completes AFTER its
      full duration re-elapses post-wake — wall time overshoots what was
      asked for by roughly the suspend duration. When the overshoot exceeds
      ``~60s``, this SKIPS that tick's sweep and waits ``initial_delay_s``
      again (the same heartbeat grace period as the boot case) before
      resuming judgment — so a just-woken producer is never reaped for the
      silence caused by the sleep itself.

    ``sweep_dead_cycles`` is blocking disk I/O (~0.2-0.3 s measured at 258
    cycles); run off the event loop via ``asyncio.to_thread`` so a slow sweep
    never stalls request handling.
    """
    sleep_for = initial_delay_s
    while True:
        before = time.time()
        await asyncio.sleep(sleep_for)
        actual = time.time() - before
        if actual > sleep_for + 60.0:
            logger.info(
                "periodic sweep: %.0fs elapsed for a %.0fs sleep — the machine was "
                "likely suspended; skipping this tick to let producers heartbeat",
                actual,
                sleep_for,
            )
            sleep_for = initial_delay_s
            continue
        await asyncio.to_thread(sweep_dead_cycles, projects_root, dead_after_s=interval_s)
        sleep_for = interval_s


__all__ = ["DEAD_AFTER_S", "periodic_sweep", "reap_cycle_by_id", "sweep_dead_cycles"]
