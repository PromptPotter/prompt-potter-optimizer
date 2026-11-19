from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from pathlib import Path

from promptpotter.application.jobs.quota import reap_dead_runs
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.paths import jobs_dir
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.llm.pricing import refresh_rates, refresh_route_answers
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.layout import (
    campaigns_root_dir_for,
    inner_sandboxes_dir,
    read_sandbox_owner,
    tenant_workspace,
)
from promptpotter.infrastructure.store.stores import Stores, inner_sandbox_store, tenant_stores
from promptpotter.shared.errors import FileUnreadableError, NotFoundError, fault_said

# Scheduling only: what counts as dead has no clock in it.
SWEEP_EVERY_S = 900.0

# The periodic steps, each returning its faults to `_recorded`, any process's.
SWEEP_STEP, ORPHANS_STEP = "sweep", "orphans"
ROUTE_ANSWERS_STEP, RATES_STEP = "route-answers", "rates"
_PRICES = (
    (RATES_STEP, lambda: [f] if (f := refresh_rates().unfetched()) else []),
    (ROUTE_ANSWERS_STEP, refresh_route_answers),
)


def reclaim_orphan_sandboxes(projects_root: Path) -> list[str]:
    """Each sandbox whose owner cycle is gone; one it cannot delete is a fault it returns."""
    inner_dir = inner_sandboxes_dir(projects_root)
    undeleted: list[str] = []
    for sandbox in inner_dir.iterdir() if inner_dir.is_dir() else ():
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
        except OSError as refusal:
            undeleted.append(fault_said(refusal)[0])
    return undeleted


def _reap(stores: Stores) -> list[str]:
    """Each cycle of *stores*, then each sandbox one of its cycles owns: none nests by path. A
    cycle it cannot read is a fault it returns, and the rest are reaped all the same."""
    unread: list[str] = []
    for campaign_id in stores.campaigns.list_campaign_ids():
        for hop in stores.campaigns.campaign_cycle_hops(campaign_id):
            try:
                stores.mark_producer_vanished(hop)
            except FileUnreadableError as refusal:
                unread.append(fault_said(refusal)[0])
            except NotFoundError:
                continue
            if (inner := inner_sandbox_store(stores, hop)) is not None:
                unread += _reap(inner)
    return unread


def _recorded(projects_root: Path, step: str, run: Callable[[], list[str]]) -> None:
    """The one recorder of a step's pass, whoever runs it: the faults *run* returns, or the one it
    raises, which ends neither the loop nor a launch. A clean pass clears what an earlier left."""
    try:
        faults = run()
    except Exception as fault:  # noqa: BLE001
        faults = [fault_said(fault)[0]]
    JobRegistry.attach(jobs_dir(projects_root)).record_pass(step, faults)


def sweep_dead_cycles(projects_root: Path) -> None:
    """Every launch's, and the server's tick: each cycle it could not read stands in
    `machine-status` until a pass reads it."""
    _recorded(
        projects_root,
        SWEEP_STEP,
        lambda: [
            fault
            for tenant in sorted(projects_root.iterdir() if projects_root.is_dir() else ())
            if campaigns_root_dir_for(WorkspaceDir(tenant)).is_dir()
            for fault in _reap(tenant_stores(projects_root, WorkspaceDir(tenant)))
        ],
    )


def sweep(projects_root: Path) -> None:
    """Every reclaim of what a dead producer left: no read stamps or deletes any of it, and only
    this releases its runs (`reap_dead_runs`)."""

    def orphans() -> list[str]:
        undeleted = reclaim_orphan_sandboxes(projects_root)
        reap_dead_runs(projects_root, JobRegistry.attach(jobs_dir(projects_root)))
        return undeleted

    sweep_dead_cycles(projects_root)
    _recorded(projects_root, ORPHANS_STEP, orphans)


async def periodic_sweep(projects_root: Path, *, interval_s: float = SWEEP_EVERY_S) -> None:
    """The reclaim, then the prices: the home's rates, so an API that launches nothing prices off a
    list at most a day old, and the route answers, whose one refresher this loop is. At boot the
    prices alone, so a restart serves no day-old one; the reclaim waits a tick: every read derives
    what a dead producer left, so no first page waits on it."""
    prices = [functools.partial(_recorded, projects_root, name, run) for name, run in _PRICES]
    steps = prices
    while True:
        for step in steps:
            await asyncio.to_thread(step)
        await asyncio.sleep(interval_s)
        steps = [functools.partial(sweep, projects_root), *prices]
