from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from promptpotter.application.account import account_of
from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.jobs import quota
from promptpotter.application.jobs.launcher.admission import withdraw_queued
from promptpotter.domain.run_records import ArmedCeilingEffect
from promptpotter.domain.spend import MONEY_AXES, Budget
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
    from promptpotter.infrastructure.store.stores import Stores


def change_run_limits(dispatched: DispatchedToCycle[ChangeRunLimitsPayload]) -> Applier[object]:
    dispatcher, hop, change = dispatched.dispatcher, dispatched.hop, dispatched.payload.budget
    return Applier.silent(
        lambda: _apply_change_run_limits(dispatcher, hop, change),
        effect_fn=lambda: _armed_ceiling(dispatcher.stores, hop),
    )


def _armed_ceiling(stores: Stores, hop: CycleHop) -> ArmedCeilingEffect:
    cycle = stores.campaigns.read_standing_budget(hop).budget
    return ArmedCeilingEffect(armed=stores.campaigns.read_campaign_ceiling(hop).over(cycle))


def _clamp_to_account_ceilings(
    dispatcher: CommandDispatcher, hop: CycleHop, change: Budget
) -> tuple[Budget, Budget]:
    stores = dispatcher.stores
    return quota.clamp_budget_change(
        requested=change,
        user=account_of(stores),
        stores=stores,
        job_registry=dispatcher.job_registry,
        hop=hop,
    )


async def _apply_change_run_limits(
    dispatcher: CommandDispatcher, hop: CycleHop, change: Budget
) -> None:
    """Clamped to the account FIRST: unclamped, a raise here is the way around the host-wallet gate."""
    reserve = Budget()
    # Rounds and a clock are no money: a change naming neither money axis skips the wallet read.
    if change.named & MONEY_AXES:
        change, reserve = await asyncio.to_thread(
            _clamp_to_account_ceilings, dispatcher, hop, change
        )
    await asyncio.to_thread(
        quota.hold_budget_change,
        job_registry=dispatcher.job_registry,
        stores=dispatcher.stores,
        hop=hop,
        change=change,
        reserve=reserve,
    )


def set_concurrent_cycles(dispatched: Dispatched[SetConcurrentCyclesPayload]) -> Applier[object]:
    stores, limit = dispatched.dispatcher.stores, dispatched.payload.max_concurrent_cycles
    return Applier.silent(lambda: quota.set_concurrent_cycles(stores=stores, limit=limit))


def cancel_queued_run(dispatched: Dispatched[CancelQueuedRunPayload]) -> Applier[object]:
    """A queued FORK's stub stays for ``cleanup-empty-cycles``: deleting a cycle is that verb's."""
    dispatcher, job_id = dispatched.dispatcher, dispatched.payload.job_id

    def _apply() -> None:
        stores = dispatcher.stores
        principal = acting_principal(stores.identity).id
        if not withdraw_queued(stores, dispatcher.job_registry, job_id, principal_id=principal):
            raise NotFoundError("Not found", code="not_found")

    return Applier.silent(_apply)
