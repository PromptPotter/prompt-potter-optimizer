from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.layout import (
    inner_sandboxes_dir,
    read_sandbox_owner,
    tenant_workspace,
)

logger = logging.getLogger(__name__)

# Scheduling only: what counts as dead has no clock in it.
SWEEP_EVERY_S = 900.0


def _tenant_root_of(cycle_dir: Path) -> WorkspaceDir:
    """*cycle_dir* is `…/{tenant}/campaigns/{cid}/cycles/{cyid}`."""
    return WorkspaceDir(cycle_dir.parents[3])


def _store_for(cycle_dir: Path) -> CampaignStore:
    return CampaignStore(_tenant_root_of(cycle_dir))


def _sweep_roots(projects_root: Path) -> list[Path]:
    """Each L4 inner sandbox is a projects-root-shaped tree, and none nests inside another."""
    roots = [projects_root]
    inner_dir = inner_sandboxes_dir(projects_root)
    if inner_dir.is_dir():
        roots.extend(p for p in inner_dir.iterdir() if p.is_dir())
    return roots


def reclaim_orphan_sandboxes(projects_root: Path) -> int:
    inner_dir = inner_sandboxes_dir(projects_root)
    if not inner_dir.is_dir():
        return 0
    reclaimed = 0
    for sandbox in inner_dir.iterdir():
        if not sandbox.is_dir():
            continue
        # Never matched by directory name: that is the content-addressed cycle_id, shared by any tenant on the same origin.
        owner = read_sandbox_owner(sandbox)
        if owner is None:
            # Not provably an orphan, so KEPT.
            continue
        workspace = tenant_workspace(projects_root, owner.tenant_id)
        owner_hop = CycleHop(campaign_id=owner.campaign_id, cycle_id=owner.cycle_id)
        if CampaignStore(workspace).load(owner_hop) is not None:
            continue
        try:
            CampaignStore(workspace).delete_inner_sandbox(sandbox, campaign_id=owner.campaign_id)
        except OSError as exc:
            logger.warning("could not reclaim orphan inner sandbox %s: %s", sandbox, exc)
            continue
        logger.info("reclaimed orphan inner sandbox %s (owner cycle gone)", sandbox.name)
        reclaimed += 1
    return reclaimed


def sweep_dead_cycles(projects_root: Path) -> int:
    reaped = 0
    for root in _sweep_roots(projects_root):
        for ledger_path in root.glob("*/campaigns/*/cycles/*/.runtime/ledger.jsonl"):
            cycle_dir = ledger_path.parent.parent
            if _store_for(cycle_dir).mark_producer_vanished(
                CycleHop(campaign_id=cycle_dir.parents[1].name, cycle_id=cycle_dir.name)
            ):
                reaped += 1
    if reaped:
        logger.info("sweep stamped %d dead cycle(s) terminal", reaped)
    return reaped


async def periodic_sweep(projects_root: Path, *, interval_s: float = SWEEP_EVERY_S) -> None:
    sleep_for = 0.0
    while True:
        await asyncio.sleep(sleep_for)
        try:
            await asyncio.to_thread(sweep_dead_cycles, projects_root)
            await asyncio.to_thread(reclaim_orphan_sandboxes, projects_root)
        except Exception:
            # A raising tick must not END the loop: all reaping would stop for the rest of the server's uptime.
            logger.exception("periodic sweep tick failed; the loop continues")
        sleep_for = interval_s


__all__ = [
    "periodic_sweep",
    "reclaim_orphan_sandboxes",
    "sweep_dead_cycles",
]
