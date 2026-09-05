"""Liveness reaper. It rests on one fact: a genuinely alive cycle cannot go stale, because it
heartbeats within ``RUN_FRESH_S`` — so a persistently-detached cycle is dead, not quiet."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.phases import RunPhase
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import read_json_optional, validate_path_component
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    inner_sandboxes_dir,
    sandbox_owner_path,
    tenant_workspace,
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
    """Stamp a single dead cycle terminal, resolving its dir across tenants. Judges no liveness —
    the registry has already proven the task gone. Idempotent, ``False`` on a missing cycle."""
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
    """A cycle with no live producer — the index-readable guard plus one comparison, delegating
    entirely to ``derive_run_phase``. No second "is it running?" computation lives here."""
    data = read_json_optional(CycleLayout(cycle_dir).manifest)
    if not isinstance(data, dict):
        return False  # unreadable — nothing to judge
    finished_at = data.get("finished_at")
    phase = derive_run_phase(cycle_dir, is_terminal=bool(finished_at), fresh_s=dead_after_s)
    return phase == RunPhase.DETACHED


def _sweep_roots(projects_root: Path) -> list[Path]:
    """``projects_root`` plus every L4 inner sandbox, itself a projects-root-shaped tree, so the
    SAME glob reaches it one level in — the registry never nests a sandbox inside another."""
    roots = [projects_root]
    inner_dir = inner_sandboxes_dir(projects_root)
    if inner_dir.is_dir():
        roots.extend(p for p in inner_dir.iterdir() if p.is_dir())
    return roots


def reclaim_orphan_sandboxes(projects_root: Path) -> int:
    """Delete every inner sandbox whose owner cycle is GONE — then it is unreachable, not merely
    stale. A sandbox whose owner exists is KEPT, terminal or not: finished is not unreachable."""
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
            store = CampaignStore(tenant_workspace(projects_root, str(owner.get("tenant_id", ""))))
        except ValueError as exc:
            # An owner triple that will not validate cannot name a workspace to bank INTO, and
            # this function deletes on a fact: the sandbox is kept, money and all.
            logger.warning("orphan inner sandbox %s names an unusable owner: %s", sandbox, exc)
            continue
        try:
            store.delete_inner_sandbox(sandbox, campaign_id=str(owner.get("campaign_id", "")))
        except OSError as exc:
            # Reported, never swallowed: an unreclaimable sandbox is the exact silence
            # this function exists to end.
            logger.warning("could not reclaim orphan inner sandbox %s: %s", sandbox, exc)
            continue
        logger.info("reclaimed orphan inner sandbox %s (owner cycle gone)", sandbox.name)
        reclaimed += 1
    return reclaimed


def sweep_dead_cycles(projects_root: Path, *, dead_after_s: float = DEAD_AFTER_S) -> int:
    """Reap every dead cycle across all tenants and sandboxes. No ``live_keys`` exclusion — the
    freshness gate in :func:`_is_dead` alone protects a live run; the registry is not authoritative."""
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
    dead_after_s: float = DEAD_AFTER_S,
    initial_delay_s: float = 120.0,
) -> None:
    """Background sweep for the server's lifetime, so a CLI-launched death is reaped mid-uptime.
    Two guards against a false reap across a MACHINE sleep: the boot delay, and a skipped tick.

    **How OFTEN it looks and how STALE counts as dead are two facts.** They share a default and
    used to share the parameter, so shortening the interval to notice a death sooner would also
    have declared any cycle quiet for that long dead — a false reap of a live run, bought by an
    edit that looks like pure scheduling."""
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
        try:
            await asyncio.to_thread(sweep_dead_cycles, projects_root, dead_after_s=dead_after_s)
            # Reclamation rides the same tick but stays a separate call: one judges LIVENESS
            # (is this cycle's producer gone?), the other judges REACHABILITY (does this
            # sandbox's owner still exist?). Same schedule, different question — folding them
            # would make either count unreadable.
            await asyncio.to_thread(reclaim_orphan_sandboxes, projects_root)
        except Exception:
            # A raising tick must not END the loop: both halves write, so one unwritable path would
            # silently stop all reaping for the rest of the server's uptime, then resurface hours
            # later as a shutdown crash when the lifespan awaits this task. Cancellation is a
            # BaseException, so shutdown still lands.
            logger.exception("periodic sweep tick failed; the loop continues")
        sleep_for = interval_s


__all__ = [
    "periodic_sweep",
    "reap_cycle_by_id",
    "reclaim_orphan_sandboxes",
    "sweep_dead_cycles",
]
