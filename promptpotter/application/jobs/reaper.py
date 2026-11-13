from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Callable
from pathlib import Path

from promptpotter.application.jobs.quota import reap_dead_runs
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.paths import jobs_dir
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.llm.pricing import refresh_route_answers
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.layout import (
    campaigns_root_dir_for,
    inner_sandboxes_dir,
    read_sandbox_owner,
    tenant_workspace,
)
from promptpotter.infrastructure.store.stores import Stores, inner_sandbox_store, tenant_stores
from promptpotter.shared.errors import LedgerUnreadableError

logger = logging.getLogger(__name__)

# Scheduling only: what counts as dead has no clock in it.
SWEEP_EVERY_S = 900.0


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
        owner_store = CampaignStore(
            tenant_workspace(projects_root, owner.tenant_id), jobs_dir(projects_root)
        )
        owner_hop = CycleHop(campaign_id=owner.campaign_id, cycle_id=owner.cycle_id)
        if owner_store.load(owner_hop, moment=None) is not None:
            continue
        try:
            owner_store.delete_inner_sandbox(sandbox, campaign_id=owner.campaign_id)
        except OSError as exc:
            logger.warning("could not reclaim orphan inner sandbox %s: %s", sandbox, exc)
            continue
        logger.info("reclaimed orphan inner sandbox %s (owner cycle gone)", sandbox.name)
        reclaimed += 1
    return reclaimed


def _reap(stores: Stores) -> int:
    """Each cycle of *stores*, then each sandbox one of its cycles owns: none nests by path."""
    reaped = 0
    for campaign_id in stores.campaigns.list_campaign_ids():
        for cycle_dir in stores.campaigns.campaign_cycle_dirs(campaign_id):
            hop = CycleHop(campaign_id=campaign_id, cycle_id=cycle_dir.name)
            try:
                if stores.mark_producer_vanished(hop):
                    reaped += 1
            except LedgerUnreadableError as refused:
                # A refused cycle is no answer for the cycles after it: each is reaped on its own.
                logger.warning("sweep left a refused cycle: %s", refused)
            if (inner := inner_sandbox_store(stores, campaign_id, cycle_dir.name)) is not None:
                reaped += _reap(inner)
    return reaped


def sweep_dead_cycles(projects_root: Path) -> int:
    reaped = sum(
        _reap(tenant_stores(projects_root, WorkspaceDir(tenant)))
        for tenant in sorted(projects_root.iterdir() if projects_root.is_dir() else ())
        if campaigns_root_dir_for(WorkspaceDir(tenant)).is_dir()
    )
    if reaped:
        logger.info("sweep stamped %d dead cycle(s) terminal", reaped)
    return reaped


def sweep(projects_root: Path) -> None:
    """Every reclaim of what a dead producer left: no read stamps or deletes any of it, and only
    this releases its runs (`reap_dead_runs`)."""
    sweep_dead_cycles(projects_root)
    reclaim_orphan_sandboxes(projects_root)
    reap_dead_runs(projects_root, JobRegistry.attach(jobs_dir(projects_root)))


async def periodic_sweep(projects_root: Path, *, interval_s: float = SWEEP_EVERY_S) -> None:
    """The reclaim, then the route answers every process's fetch wrote: this loop is their one
    refresher (`pricing.refresh_route_answers`). At boot the answers alone, so a restart serves no
    day-old one; the reclaim waits a tick: every read derives what a dead producer left, so no first
    page waits on it."""
    steps: tuple[Callable[[], None], ...] = (refresh_route_answers,)
    while True:
        for step in steps:
            try:
                await asyncio.to_thread(step)
            except Exception:
                # A raising step must not END the loop, nor skip the other: each would stop for the
                # rest of the server's uptime.
                logger.exception("periodic sweep step %s failed; the loop continues", step)
        await asyncio.sleep(interval_s)
        steps = (functools.partial(sweep, projects_root), refresh_route_answers)
