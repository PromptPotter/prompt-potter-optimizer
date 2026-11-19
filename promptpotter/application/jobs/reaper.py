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

# The periodic steps, each recording its last pass (`JobRegistry.record_pass`), any process's.
SWEEP_STEP, ROUTE_ANSWERS_STEP, RATES_STEP = "sweep", "route-answers", "rates"
_PRICES = ((RATES_STEP, refresh_rates), (ROUTE_ANSWERS_STEP, refresh_route_answers))


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
        except OSError:
            continue
        reclaimed += 1
    return reclaimed


def _reap(stores: Stores, unread: list[str]) -> int:
    """Each cycle of *stores*, then each sandbox one of its cycles owns: none nests by path. A
    cycle it cannot read is named in *unread*, and the rest are reaped all the same."""
    reaped = 0
    for campaign_id in stores.campaigns.list_campaign_ids():
        for hop in stores.campaigns.campaign_cycle_hops(campaign_id):
            try:
                if stores.mark_producer_vanished(hop):
                    reaped += 1
            except FileUnreadableError as refusal:
                unread.append(fault_said(refusal)[0])
            except NotFoundError:
                continue
            if (inner := inner_sandbox_store(stores, hop)) is not None:
                reaped += _reap(inner, unread)
    return reaped


def sweep_dead_cycles(projects_root: Path) -> int:
    """Every launch's, and the server's tick: each cycle it could not read stands in
    `machine-status` until a pass reads it."""
    unread: list[str] = []
    reaped = sum(
        _reap(tenant_stores(projects_root, WorkspaceDir(tenant)), unread)
        for tenant in sorted(projects_root.iterdir() if projects_root.is_dir() else ())
        if campaigns_root_dir_for(WorkspaceDir(tenant)).is_dir()
    )
    JobRegistry.attach(jobs_dir(projects_root)).record_pass(SWEEP_STEP, unread)
    return reaped


def sweep(projects_root: Path) -> None:
    """Every reclaim of what a dead producer left: no read stamps or deletes any of it, and only
    this releases its runs (`reap_dead_runs`)."""
    sweep_dead_cycles(projects_root)
    reclaim_orphan_sandboxes(projects_root)
    reap_dead_runs(projects_root, JobRegistry.attach(jobs_dir(projects_root)))


async def periodic_sweep(projects_root: Path, *, interval_s: float = SWEEP_EVERY_S) -> None:
    """The reclaim, then the prices: the home's rates, so an API that launches nothing prices off a
    list at most a day old, and the route answers, whose one refresher this loop is. At boot the
    prices alone, so a restart serves no day-old one; the reclaim waits a tick: every read derives
    what a dead producer left, so no first page waits on it."""
    jobs = JobRegistry.attach(jobs_dir(projects_root))
    refresh = tuple((name, functools.partial(_refreshed, jobs, name, fn)) for name, fn in _PRICES)
    steps: tuple[tuple[str, Callable[[], None]], ...] = refresh
    while True:
        for name, step in steps:
            # A raising step must not END the loop, nor skip the other: each would stop for the
            # rest of the server's uptime. A clean pass is each step's own to record.
            try:
                await asyncio.to_thread(step)
            except Exception as fault:  # noqa: BLE001
                jobs.record_pass(name, [fault_said(fault)[0]])
        await asyncio.sleep(interval_s)
        steps = ((SWEEP_STEP, functools.partial(sweep, projects_root)), *refresh)


def _refreshed(jobs: JobRegistry, step: str, prices: Callable[[], object]) -> None:
    prices()
    jobs.record_pass(step, ())
