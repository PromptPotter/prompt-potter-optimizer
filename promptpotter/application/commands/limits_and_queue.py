from __future__ import annotations

import asyncio

from promptpotter.application.commands.dispatcher import (
    Applier,
    CommandDispatcher,
    Dispatched,
    DispatchedToCycle,
    Replaying,
    replays,
)
from promptpotter.application.jobs import quota
from promptpotter.application.jobs.launcher.admission import campaign_sends
from promptpotter.application.served_dashboard import current_caps
from promptpotter.domain.command_kinds import (
    CancelQueuedRunPayload,
    ChangeRunLimitsPayload,
    CommandKind,
    SetConcurrentCyclesPayload,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import ArmedCaps
from promptpotter.domain.records.run_records import ArmAct
from promptpotter.domain.spend import MONEY_AXES
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import acting_principal


async def change_run_limits(
    dispatched: DispatchedToCycle[ChangeRunLimitsPayload],
) -> Applier[ArmedCaps]:
    """Answers the caps as ARMED, each against its own book: a clamp is said where it was made."""
    dispatcher, hop, change = dispatched.dispatcher, dispatched.hop, dispatched.payload
    at = quota.CampaignCycle(dispatched.campaign, hop)

    async def _apply() -> ArmedCaps:
        await _apply_change_run_limits(dispatcher, at, change)
        return await asyncio.to_thread(current_caps, dispatcher.stores, hop)

    return Applier(_apply)


@replays(CommandKind.SET_LIMITS)
async def limits_armed(replaying: Replaying[ChangeRunLimitsPayload]) -> ArmedCaps:
    """Off the cycle as it stands: caps are a standing state, so what binds now is the honest answer
    to a retry, whatever moved them since; a cycle deleted since answers its 404."""
    sent = replaying.payload
    hop = CycleHop(campaign_id=sent.campaign_id, cycle_id=sent.cycle_id)
    return await asyncio.to_thread(current_caps, replaying.stores, hop)


def _change_budget(
    dispatcher: CommandDispatcher, at: quota.CampaignCycle, change: ChangeRunLimitsPayload
) -> bool:
    stores = dispatcher.stores
    return quota.change_budget(
        change.budget,
        overshoot=change.overshoot,
        stores=stores,
        job_registry=dispatcher.job_registry,
        at=at,
        # A change naming no money axis reads no config: it sends nothing a clamp asks of.
        sends=campaign_sends(stores, *at) if change.budget.named & MONEY_AXES else (),
    )


async def _apply_change_run_limits(
    dispatcher: CommandDispatcher, at: quota.CampaignCycle, change: ChangeRunLimitsPayload
) -> None:
    """Clamped to the account FIRST: unclamped, a raise here is the way around the host-wallet gate.
    Settable on every arm of a head-to-head: a change that moves a ceiling the arm runs under moves
    it alone, off its head-to-head's budget, so the act is its exit (decisions.md § 0009); a change
    restating the ceilings it stands under leaves the arm in its comparison."""
    moved = await asyncio.to_thread(_change_budget, dispatcher, at, change)
    if moved:
        await asyncio.to_thread(dispatcher.stores.campaigns.act, at.hop, ArmAct.SET_LIMITS)


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
