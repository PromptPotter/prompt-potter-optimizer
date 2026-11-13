from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.jobs import quota
from promptpotter.application.jobs.launcher.admission import campaign_sends
from promptpotter.domain.run_records import ComparisonExit
from promptpotter.domain.spend import MONEY_AXES
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import acting_principal

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import (
        CommandDispatcher,
        Dispatched,
        DispatchedToCycle,
    )
    from promptpotter.domain.command_kinds import (
        CancelQueuedRunPayload,
        ChangeRunLimitsPayload,
        SetConcurrentCyclesPayload,
    )
    from promptpotter.domain.cycle_paths import CycleHop


async def change_run_limits(
    dispatched: DispatchedToCycle[ChangeRunLimitsPayload],
) -> Applier[object]:
    dispatcher, hop, change = dispatched.dispatcher, dispatched.hop, dispatched.payload
    return Applier.silent(lambda: _apply_change_run_limits(dispatcher, hop, change))


def _change_budget(
    dispatcher: CommandDispatcher, hop: CycleHop, change: ChangeRunLimitsPayload
) -> bool:
    stores = dispatcher.stores
    return quota.change_budget(
        change.budget,
        overshoot=change.overshoot,
        stores=stores,
        job_registry=dispatcher.job_registry,
        hop=hop,
        # A change naming no money axis reads no config: it sends nothing a clamp asks of.
        sends=campaign_sends(stores, hop) if change.budget.named & MONEY_AXES else (),
    )


async def _apply_change_run_limits(
    dispatcher: CommandDispatcher, hop: CycleHop, change: ChangeRunLimitsPayload
) -> None:
    """Clamped to the account FIRST: unclamped, a raise here is the way around the host-wallet gate.
    Settable on every arm of a head-to-head: a change that moves a ceiling the arm runs under moves
    it alone, off its head-to-head's budget, so the act is its exit (decisions.md § 0009); a change
    restating the ceilings it stands under leaves the arm in its comparison."""
    moved = await asyncio.to_thread(_change_budget, dispatcher, hop, change)
    if moved:
        await asyncio.to_thread(
            dispatcher.stores.campaigns.leave_comparison, hop, ComparisonExit.SET_LIMITS
        )


async def set_concurrent_cycles(
    dispatched: Dispatched[SetConcurrentCyclesPayload],
) -> Applier[object]:
    stores, limit = dispatched.dispatcher.stores, dispatched.payload.max_concurrent_cycles

    async def _apply() -> None:
        quota.set_concurrent_cycles(stores=stores, limit=limit)

    return Applier.silent(_apply)


async def cancel_queued_run(dispatched: Dispatched[CancelQueuedRunPayload]) -> Applier[object]:
    """A queued FORK's stub stays for ``cleanup-empty-cycles``: deleting a cycle is that verb's."""
    dispatcher, job_id = dispatched.dispatcher, dispatched.payload.job_id

    def _apply() -> None:
        stores, registry = dispatcher.stores, dispatcher.job_registry
        # Its own launch only: a job's principal never changes, so it is asked outside the gate.
        job = registry.get(job_id)
        if (
            job is None
            or job.principal_id != acting_principal(stores.identity).id
            or registry.cancel_queued(job_id) is None
        ):
            raise NotFoundError("Not found", code="not_found")

    return Applier.blocking(_apply)
